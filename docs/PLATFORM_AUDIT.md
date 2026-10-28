# 平台正确性与实验可运行性审计（实测）

> 审计日期：2026-10-27  
> 环境：单节点 K3S `v1.36.5+k3s1`，命名空间 `sim-test`  
> 结论：**平台可用**。平台基线自检 **58 项全部通过**，三类研究内容均有真实运行数据，
> 状态副本分叉与修复、混沌 Pod 删除、真实 ROSS 仿真均可复现。

本文只记录实际执行过的命令与输出，不把设计意图当成验证结果。
架构说明见 `docs/ARCHITECTURE.md`，平台对象说明见 `docs/K3S-PLATFORM.md`。

## 1. 审计范围

| 层次 | 检查内容 | 判定依据 |
| --- | --- | --- |
| 集群基线 | 节点、Pod、Deployment、Service 端点 | 全部 Ready，17 个服务均有就绪端点 |
| 业务链路 | `POST /trigger` 跨服务调用 | 返回 `engaged`，健康下游 10 个 |
| 状态存储 | 3 副本一致性、分叉、反熵修复 | 分叉可观测，`/sync` 后 digest 收敛 |
| 可观测性 | Jaeger、Prometheus | Jaeger API 可用；17/17 target `up` |
| 平台治理 | HPA / PDB / ResourceQuota / LimitRange / Ingress | 对象存在且参数正确 |
| 集群内混沌 | chaos-service 的最小权限 RBAC | 可删 Pod、不可越权；Pod 自愈 |
| 故障感知 | 深度学习 + 真实 trace / K8S 基线 | 定位正确，时延可计算且非负 |
| 一致性 | 真实 netem + 单副本写入 | 观测到分叉并修复 |
| 仿真引擎 | sim-bridge -> ROSS | `mode=real`、`returncode=0` |

## 2. 环境快照

```text
NAME     STATUS   ROLES           AGE   VERSION
ubu-22   Ready    control-plane   23h   v1.36.5+k3s1
```

```text
Deployment: 19   # 17 个服务 + Jaeger + Prometheus
Pod:        21   # state-store 3 副本；其余服务各 1 个；Jaeger/Prometheus 各 1 个
Service:    20   # 17 个服务 + state-store-headless + Jaeger + Prometheus
```

Pod 全部 `Running`，没有 `CrashLoopBackOff` / `ImagePullBackOff`。
`state-store` 的 3 个副本分别位于 `10.42.0.150/151/152`，无头 Service
`state-store-headless` 能解析出 3 个地址。

## 3. 平台基线：`verify_platform.sh`

```bash
./scripts/verify_platform.sh
```

实测结尾：

```text
== 追踪与指标 ==
  [PASS] Jaeger API 可用: {"ok": true, "services": 18}
  [PASS] Prometheus targets: {"ok": true, "up": 17, "total": 17}

== K3S 平台治理对象 ==
  [PASS] hpa: 1 个
  [PASS] pdb: 2 个
  [PASS] resourcequota: 1 个
  [PASS] limitrange: 1 个
  [PASS] ingress: 1 个

== chaos-service RBAC 自检 ==
  [PASS] chaos-service 可读取 Pod（RBAC 生效）

========== 结果: PASS=58 FAIL=0 ==========
```

关键业务输出：

```json
{
  "checks": {
    "orchestrator": 200, "fire-control": 200, "state-store": 200,
    "api-gateway": 200, "auth-service": 200, "config-service": 200,
    "notification-service": 200, "platform-service": 200, "chaos-service": 200
  },
  "errors": [],
  "trigger": {"status": "engaged", "trace_id": "cac227a2...", "health_ok": 10},
  "state_replicas": ["10.42.0.150", "10.42.0.151", "10.42.0.152"],
  "state_consistent": [true, true, true]
}
```

说明：

- 17 个业务 Service 全部有就绪端点；`state-store-headless` 有 3 个副本端点。
- `/trigger` 不只是探活，而是真的走了
  `scenario-mgr -> state-store/log-service`、`fire-control -> sensor-service -> entity/env`、
  `telemetry-gw` 等下游，并产生一条可查询的 trace。
- Prometheus 的 17 个 target 覆盖 11 个核心 + 6 个平台服务，全部 `up`。
- 治理对象：`api-gateway-hpa`、`state-store-pdb`、`api-gateway-pdb`、`sim-quota`、
  `sim-defaults`、`sim-api-gateway` Ingress。

## 4. 实验链路实测

### 4.1 故障感知：深度学习 + 真实 Trace

```bash
python wf.py run --config configs/algo_samples.yaml --id verify-algo-002
```

| 指标 | 实测值 |
| --- | --- |
| 注入 | `pod_kill`，目标 `fire-control` |
| 是否检出 | 是 |
| 根因服务 | `fire-control` |
| 置信度 | `0.977` |
| 感知信号 | `trace_error` |
| 证据延迟 | **+0.9346 s** |
| 算法扫描耗时 | `10.134 s` |
| 端到端耗时 | `17.350 s` |
| 失败调用对端 | `failed_peers = {"fire-control": 3}` |
| 运行态依赖图 | 16 条边，`graph_source = runtime_trace` |
| 影响面 | `orchestrator (distance=1)` |
| 容错恢复时间 | `19.282 s` |

证据片段：

```json
{
  "detected": true,
  "detected_at": 1793067661.696,
  "detection_signal": "trace_error",
  "faulty_service": "fire-control",
  "confidence": 0.977,
  "threshold": 0.0299,
  "anomalous_services": ["orchestrator", "fire-control"],
  "root_cause_ranking": [{"service": "fire-control", "prob": 0.9774}],
  "trace_evidence": {
    "graph_source": "runtime_trace",
    "graph_edges": 16,
    "failed_peers": {"fire-control": 3},
    "window_start": 1793067658.761,
    "window_end": 1793067667.977
  }
}
```

```json
{
  "inject_time": 1793067660.7614,
  "evidence_at": 1793067661.696,
  "evidence_latency_ms": 934.6,
  "scan_duration_ms": 10134.0,
  "end_to_end_ms": 17349.9,
  "anomaly": false
}
```

> 注意两个时间口径：`evidence_latency_ms` 是“故障注入 -> 首个失败 span”的事实时延；
> `scan_duration_ms` 是算法实际扫描指标/查询 Jaeger 的耗时。二者不能混为一谈，
> 当前实现仍是离线批处理，不是流式检测。详细背景见 `docs/tracing-integration.md`。

基线算法对照（`configs/military_fault_detect.yaml`，`verify-detect-002`）：

```json
{
  "detection_signal": "k8s_readiness",
  "faulty_service": "fire-control",
  "confidence": 0.95,
  "evidence_latency_ms": 216.4,
  "scan_duration_ms": 89.0,
  "recovery_time_s": 1.935
}
```

两种算法都能检出 `fire-control`。基线快是因为本次 Pod 删除后未就绪窗口恰好被轮询捕获；
链路追踪路径的价值在于证据来自“业务真实失败”，不依赖自愈速度与轮询相位。

### 4.2 状态一致性：真实 netem + 单副本写入

```bash
python wf.py run --config configs/military_consistency.yaml --id verify-consist-004
```

执行链路：

```text
state.reset -> state.write(all) -> verify(一致)
-> inject_netem(state-store, 200ms, 10%)
-> traffic.probe -> state.write(target=one, replicate=false)
-> verify(分叉) -> consist(模糊-排队决策)
-> clear_netem -> state.repair -> verify(收敛)
```

实测输出：

```text
[STATE] verify: replicas=3 consistent=True  divergent=[]                 # 基线
[FAULT] netem on state-store-... (eth0): delay=200ms loss=10%            # 3 个副本全部注入
[STATE] write key=mission-state target=one replicate=False ok=1/1 versions=[3]
[STATE] verify: replicas=3 consistent=False divergent=['mission-state']
[ALGO_FUZZY] state=TRIGGERED level=strong sync_interval=0.100s
             events=['network_partition_suspected', 'data_update_burst', 'state_divergence']
             mm1_wait=0.0014 rtt=0.4036s source=probe cpu_avg=5m
[FAULT] netem cleared on 3 container(s)
[STATE] repair: replicas=3 consistent_after=3/3
[STATE] verify: replicas=3 consistent=True  divergent=[]                 # 修复后
```

分叉快照（`verify-consist-002`，同样是单副本写入）：

```json
{
  "consistent": false,
  "divergent_keys": ["mission-state"],
  "per_key": {
    "mission-state": {
      "10.42.0.150": [2, 1793067603.77, "state-store-...-j58nz"],
      "10.42.0.151": [1, 1793067594.38, "state-store-...-c5h48"],
      "10.42.0.152": [1, 1793067594.38, "state-store-...-c5h48"]
    }
  }
}
```

修复结果：

```json
{
  "sync": [
    {"ip": "10.42.0.150", "merged": 0},
    {"ip": "10.42.0.151", "merged": 1},
    {"ip": "10.42.0.152", "merged": 0}
  ],
  "after": "3/3 replicas consistent, digest 全部为 51737a88beb1f79d"
}
```

RTT 探针在弱网下测得真实值：

| 运行 | 网络条件 | `rtt_s` | `rtt_source` |
| --- | --- | --- | --- |
| 正常态 | 无 netem | ~0.02 s | `probe` |
| `verify-consist-003` | 200ms + 10% loss | `0.6728 s` | `probe` |
| `verify-consist-004` | 200ms + 10% loss | `0.4036 s` | `probe` |

> 早期版本 `_probe_rtt` 因脚本格式化错误总是回退到 `0.05 s`，导致模糊控制器看不到弱网。
> 修复后改为每次连续探测 3 次取最小值，全部失败才回退，并在结果中写入 `rtt_source`，
> 避免把回退值伪装成测量值。

### 4.3 链路追踪

```bash
python wf.py run --config configs/tracing_pipeline.yaml --id verify-trace-002
```

实测：

```text
traces=213  spans=1306  edges=17  coverage=1.0
trace.verify: jaeger_services=18  trace_count=54  span_total=837  ok=True
```

运行态依赖图包含全部设计边，并额外捕获到平台链路：

```text
orchestrator   -> scenario-mgr, entity-service, comms-service, sensor-service,
                  fire-control, env-service, log-service, state-store,
                  telemetry-gw, sim-bridge
scenario-mgr   -> state-store, log-service
sensor-service -> entity-service, env-service
fire-control   -> sensor-service, comms-service
api-gateway    -> sensor-service          # 真实网关代理产生的边
```

### 4.4 集群内混沌：RBAC + Pod 删除

从 orchestrator Pod 调用 chaos-service（不是本机直接删 Pod）：

```bash
kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import json, urllib.request
req = urllib.request.Request(
    "http://chaos-service:8080/chaos/pod-kill",
    data=json.dumps({"service": "notification-service"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
print(urllib.request.urlopen(req, timeout=10).read().decode())
PY
```

实测：

```json
{
  "ok": true,
  "service": "notification-service",
  "killed": ["notification-service-6db985bf48-88fc5"],
  "experiment": {
    "seq": 2,
    "kind": "pod_kill",
    "detail": {"service": "notification-service",
               "pods": ["notification-service-6db985bf48-88fc5"]}
  }
}
```

约 10 秒后：

```text
notification-service-6db985bf48-qsqvf   1/1   Running   0   10s
```

同时：

```bash
kubectl -n sim-test auth can-i --as=system:serviceaccount:sim-test:sim-chaos delete pods
# yes
kubectl -n sim-test auth can-i --as=system:serviceaccount:sim-test:sim-chaos delete nodes
# no
```

证明：chaos-service 具备最小权限的集群内故障注入能力，且 Deployment 能自愈。

### 4.5 真实 ROSS 仿真

`configs/military_consistency.yaml` 的 `ross.start_sim` 现在真正调用 sim-bridge：

```json
{
  "running": false,
  "engine": "ross",
  "mode": "real",
  "lp_count": 14,
  "events_processed": 691,
  "end_time": 20.0,
  "returncode": 0
}
```

`returncode != 0` 会让工作流步骤失败，不会静默降级到 mock。

## 5. 审计中发现并修复的问题

| 现象 | 根因 | 修复 | 复验 |
| --- | --- | --- | --- |
| 故障感知延迟出现负值 | Jaeger 1.57 忽略 `lookback`，查询窗口不对 | 显式传 `start`/`end` 微秒时间戳，严格过滤 span 窗口 | `+0.935 s`，非负 |
| 弱网下 RTT 恒为 `0.05 s` | `_probe_rtt` 外层 `%` 格式化破坏脚本 | 修正脚本；增加 3 次探测取最小值 + `rtt_source` | `0.4036 s / 0.6728 s` |
| netem 只打印日志 | 动作是预留桩 | 宿主机 `nsenter` + `tc qdisc` 真正注入/清理 | 3 个 Pod 规则生效 |
| state-store 副本各存各的 | 只有进程内字典 | LWW 复制 + 无头 Service 发现 + 异步推送 + 反熵 | 分叉可见，修复后收敛 |
| `ross.start_sim` 只写 ctx | 动作未接 sim-bridge | 通过 `kubectl exec` 调 `/inject_fault`、`/start`、`/status` | `mode=real` |
| 基线算法没有感知时刻 | 只返回 `detected` | 补 `detect_invoked_at` / `detection_signal` / 证据 | `216.4 ms` |
| 汇总缺少状态一致性 | summary 未读取 `state_verify` | 汇总副本数、分叉键、修复结果 | 见 4.2 |

其它已修正的小问题：

- `measure.detect_latency` 将“证据延迟”和“扫描耗时”分开输出，负值标记 `anomaly` 并钳到 0。
- `trace.collect` 支持 `since`，故障感知查询从注入前 2 秒开始，排除历史错误污染。
- 追踪中间件跳过 `/metrics`、`/tracez`，避免 Prometheus 抓取产生大量单 span 根 trace。
- Jaeger 1.57 的 `trace.processes[processID]` 解析兼容，避免全部 span 归到 `?`。
- 平台服务统一继承 `create_app()`，自动获得 `/healthz`、`/metrics`、`/tracez` 与 span 上报。

## 6. 已知限制与风险

1. **单节点集群**：HPA/PDB 的对象与策略已验证存在，但没有多节点调度/节点故障转移可演示。
   HPA 未做压测触发扩容，只验证了指标与对象就绪（当前 CPU `6%/70%`）。
2. **NetworkPolicy 依赖 CNI**：K3S 默认 flannel 不强制执行策略。`fault.inject_network_partition`
   能成功创建策略对象，但是否真正隔离取决于 CNI；确定性网络故障请用 `fault.inject_netem`。
3. **netem 需要宿主机权限**：必须 root，且宿主机有 `tc`/`nsenter`/`k3s crictl`。它不属于
   chaos-service 的集群内能力。
4. **检测仍是批处理**：证据时刻可以很早，但一次算法扫描约 10s。生产化需要持续流量 +
   流式消费 Jaeger，而不是一次 `/trigger` 批次。
5. **存储非持久**：Jaeger 内存存储（2 万条上限）、Prometheus `emptyDir`，Pod 重建即清空。
6. **RTT 探针是采样**：3 次取最小值，能反映弱网量级，但不是完整分布；全部失败时回退
   0.05s 并在 `rtt_source=fallback` 中标注。
7. **镜像为本地导入**：`:latest` 必须 `imagePullPolicy: IfNotPresent`，且重新构建后要重启
   Deployment 才会加载新镜像。
8. **单副本平台服务**：api-gateway 有 HPA/PDB，但 auth/config/notification/platform/chaos
   仍是单副本；平台服务故障会短时影响对应能力，不影响核心仿真链路。

## 7. 复现命令

```bash
# 1. 平台自检（只读 + 一次良性 /trigger）
./scripts/verify_platform.sh
# 期望：PASS=58 FAIL=0（若 Jaeger/Prometheus 未部署，会显示 INFO 跳过）

# 2. 故障感知（Pod kill + 深度学习/trace）
python wf.py run --config configs/algo_samples.yaml --id audit-algo

# 3. 基线感知
python wf.py run --config configs/military_fault_detect.yaml --id audit-detect

# 4. 一致性（真实 netem + 分叉 + 反熵修复）
python wf.py run --config configs/military_consistency.yaml --id audit-consist

# 5. 追踪
python wf.py run --config configs/tracing_pipeline.yaml --id audit-trace

# 6. 混沌 RBAC（会删除一个 Pod，K8S 会自动重建）
kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import json, urllib.request
req = urllib.request.Request("http://chaos-service:8080/chaos/pod-kill",
    data=json.dumps({"service": "notification-service"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
print(urllib.request.urlopen(req, timeout=10).read().decode())
PY
```

## 8. 审计结论

- **平台基线正确**：17 个服务、3 副本状态存储、Jaeger、Prometheus 与 K3S 治理对象均可用。
- **实验可运行**：故障感知、容错、一致性三条链路都产出了真实数据，而不是 mock 或静态图。
- **故障注入真实**：Pod 删除、tc/netem、单副本写入都产生了可观测的系统行为，并能清理/恢复。
- **数据可解释**：延迟、RTT、分叉、覆盖率都有明确口径；回退值和批处理耗时已在结果中显式标注。
- **仍属实验平台**：单节点、非持久存储、批处理检测决定了它适合算法研发与验证，
  不应直接当作生产高可用平台使用。
