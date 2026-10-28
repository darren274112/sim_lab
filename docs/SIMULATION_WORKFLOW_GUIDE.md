# 仿真微服务工作流说明文档

> 面向研发：说明本平台的服务划分、接口、通信协议、调用关系、工作流编排与实验入口。

## 1. 总览

本平台是一个面向“故障感知、容错、一致性”三项研究课题的仿真微服务沙箱，运行在 K3S（命名空间 `sim-test`）上。

- **编排引擎**：`wf.py`（自研）按 `configs/*.yaml` 顺序执行步骤，支持 `--from`、`--step`、`--skip`、超时、断点续跑
- **动作层**：`actions/*`（k8s/fault/obs/algo/measure/ross/trace/state/eval）
- **算法层**：`algos/*`（`detect/tolerate/consist` 接口）
- **服务层**：17 个业务/平台微服务 + `eval-service`
- **观测**：Prometheus（指标） + Jaeger（链路追踪）
- **仿真引擎**：sim-bridge 桥接真实 ROSS（`mil_sim`）

完整架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，平台治理见 [K3S-PLATFORM.md](K3S-PLATFORM.md)。

## 2. 服务清单与业务职责

### 2.1 核心仿真服务（11 个）

| 服务 | 业务职责 | 通信方式 | 主要下游 |
|---|---|---|---|
| `orchestrator` | 编排入口，`POST /trigger` 串起作战链路，是跨服务调用的发起点 | HTTP/JSON（FastAPI） | scenario-mgr、fire-control、telemetry-gw、sim-bridge、state-store、log-service 等（全量健康探测+业务调用） |
| `scenario-mgr` | 想定加载（营攻击/空防等）、任务状态管理 | HTTP/JSON | `state-store`、`log-service` |
| `entity-service` | 作战实体、单位信息维护 | HTTP/JSON | — |
| `comms-service` | 通信指令下发 | HTTP/JSON | — |
| `sensor-service` | 目标航迹、情报融合 | HTTP/JSON | `entity-service`、`env-service` |
| `fire-control` | 火控交战（核心业务服务） | HTTP/JSON | `sensor-service`、`comms-service` |
| `env-service` | 地形气象信息 | HTTP/JSON | — |
| `log-service` | 日志聚合（非核心，可降级） | HTTP/JSON | — |
| `state-store` | 状态存储（3 副本），支持复制、反熵、一致性校验 | HTTP/JSON | 对等副本（`state-store-headless`） |
| `telemetry-gw` | 遥测数据接收与落库（模拟） | HTTP/JSON | — |
| `sim-bridge` | ROSS 仿真引擎桥接，暴露 `mil_sim` HTTP 接口 | HTTP/JSON（子进程启动 `mil_sim`） | 本地子进程（mpirun/ross） |

### 2.2 通用平台服务（6 个）

| 服务 | 业务职责 | 通信方式 | 主要下游 |
|---|---|---|---|
| `api-gateway` | 统一入口、健康聚合、受限反向代理、令牌转发 | HTTP/JSON | 各核心/平台服务（受限白名单） |
| `auth-service` | HMAC-SHA256 无状态令牌签发/校验 | HTTP/JSON | — |
| `config-service` | 版本化配置中心、变更历史 | HTTP/JSON | — |
| `notification-service` | 分级通知缓存、统计 | HTTP/JSON | — |
| `platform-service` | 平台身份、cgroup 限额、节点/Pod 信息、组件健康 | HTTP/JSON | `api-gateway`、`auth-service`、`config-service`、`notification-service` |
| `chaos-service` | 集群内混沌编排（Pod kill / scale） | HTTP/JSON + K8S API（in-cluster） | K8S API Server（受 `sim-chaos` SA 最小权限限制） |

### 2.3 评估服务（1 个）

| 服务 | 业务职责 | 通信方式 | 主要下游 |
|---|---|---|---|
| `eval-service` | 仿真结果量化评估，读取 `runs/<exp_id>/state.json`、`ctx.json` 并打分 | HTTP/JSON | 本地挂载 `/runs`（hostPath，只读） |

## 3. 通信协议与接口

所有微服务基于 `services/common/base.py` 的 `create_app()`，统一提供以下端点：

| 端点 | 说明 | 用途 |
|---|---|---|
| `GET /healthz` | 存活探针 | K8S liveness/readiness |
| `GET /readyz/business` | 业务就绪 | 可选业务依赖检查 |
| `GET /metrics` | Prometheus 指标 | 指标采集（指标名中的 `-` 会规范为 `_`） |
| `GET /tracez` | 追踪自检统计 | `spans_sent/failed/dropped` 等导出状态 |
| `/` | 服务根信息 | 标识服务名、角色 |

### 3.1 核心服务接口（关键）

| 服务 | 方法 | 路径 | 说明 |
|---|---|---|---|
| `orchestrator` | `POST` | `/trigger?scenario=...` | 主业务入口，串起全链路，返回各下游状态、`trace_id` 等 |
| `scenario-mgr` | `GET` | `/scenario/{name}` | 加载想定 |
| `sensor-service` | `GET` | `/tracks` | 获取航迹（依赖 entity/env） |
| `fire-control` | `POST` | `/engage` | 火控交战 |
| `state-store` | `GET/PUT` | `/state/{key}` | 键值读写 |
| `state-store` | `POST` | `/replicate` | 对等复制 |
| `state-store` | `GET` | `/snapshot`、`/replicas`、`/consistency` | 快照、发现副本、一致性校验 |
| `state-store` | `POST` | `/sync`、`/repair`、`/admin/reset` | 反熵、主动修复、重置 |
| `sim-bridge` | `POST` | `/inject_fault` | 注入 ROSS 故障（`lp_crash` 等） |
| `sim-bridge` | `POST` | `/start?end_time=...&units=...` | 启动真实 ROSS 仿真 |
| `sim-bridge` | `GET` | `/status`、`/ross/log` | 查询状态与日志 |

### 3.2 平台服务接口（关键）

| 服务 | 方法 | 路径 | 说明 |
|---|---|---|---|
| `api-gateway` | `GET` | `/gateway/health` | 聚合健康 |
| `api-gateway` | `POST` | `/gateway/token`、`/gateway/proxy` | 令牌/反向代理（受限） |
| `auth-service` | `POST` | `/auth/token` | 签发 HMAC 令牌 |
| `auth-service` | `POST` | `/auth/verify` | 校验令牌 |
| `auth-service` | `GET` | `/auth/policy` | 策略信息 |
| `config-service` | `GET/PUT/DELETE` | `/config`、`/config/{key}` | 配置增删改查 |
| `config-service` | `GET` | `/config-history`、`POST /config/reload` | 历史与热更新模拟 |
| `notification-service` | `POST` | `/notify`、`GET /notifications` | 通知投递与查询 |
| `notification-service` | `GET` | `/notify/stats` | 统计 |
| `platform-service` | `GET` | `/platform/info`、`/platform/health`、`/platform/capabilities` | 平台信息、cgroup 限额、组件健康 |
| `chaos-service` | `GET` | `/chaos/pods`、`/chaos/experiments`、`/chaos/self-check` | 查询、历史、RBAC 自检 |
| `chaos-service` | `POST` | `/chaos/pod-kill`、`/chaos/scale` | Pod 删除、Deployment 伸缩 |

### 3.3 评估服务接口

| 服务 | 方法 | 路径 | 说明 |
|---|---|---|---|
| `eval-service` | `GET` | `/` | 服务信息 |
| `eval-service` | `GET` | `/eval/list` | 列出 `runs/` 下的实验 |
| `eval-service` | `GET` | `/eval/{exp_id}` | 完整评估报告（感知/容错/一致性/追踪 + 综合分数、等级） |
| `eval-service` | `GET` | `/eval/{exp_id}/summary` | 精简摘要 |

### 3.4 通信协议细节

- **传输**：K8S ClusterIP Service 内部 HTTP（端口 8080），Ingress（Traefik）暴露外部访问
- **序列化**：JSON（`application/json`）
- **追踪传播**：W3C `traceparent` 头，`services/common/tracing.py` ASGI 中间件自动生成 SERVER/CLIENT span
- **链路上报**：OTLP/HTTP JSON（`http://jaeger:4318/v1/traces`），纯标准库实现
- **指标**：Prometheus 文本格式（`/metrics`）
- **集群内调用**：工作流动作通过 `kubectl exec` 到 `orchestrator` Pod 内部发起 HTTP 调用（绕过外部网络依赖）

## 4. 服务调用关系

### 4.1 业务主链路（`POST orchestrator:8080/trigger`）

```text
orchestrator
├── scenario-mgr ──┬── state-store
│                  └── log-service
├── fire-control ──┬── sensor-service ──┬── entity-service
│                  │                    └── env-service
│                  └── comms-service
├── telemetry-gw
└── sim-bridge
```

实际追踪中会形成多层 span 树（实测数十个 span），失败时可通过 `peer.service` 定位被调方。

### 4.2 平台链路

```text
client -> Ingress(traefik) -> api-gateway
api-gateway -> auth-service        (令牌)
api-gateway -> <core service>      (受限反向代理 /gateway/proxy)
platform-service -> auth-service / config-service / notification-service / api-gateway
chaos-service -> K8S API           (Pod/Deployment 操作，受 RBAC 限制)
```

### 4.3 状态复制链路（state-store）

```text
client/orchestrator -> state-store (任意副本)
state-store(replica) --async push--> state-store(replica)  (LWW)
state-store(replica) --anti-entropy--> state-store(replica) (每 5s 拉取 snapshot 合并)
discovery: state-store-headless (publishNotReadyAddresses: true)
```

### 4.4 观测链路

```text
各服务 /metrics  -> Prometheus（5s 抓取，11 核心 + 6 平台 = 17 个 target）
各服务 spans     -> Jaeger collector :4318（OTLP/HTTP JSON）
actions/trace.py -> Jaeger Query :16686（聚合、推导运行态图）
algos/*          -> trace 证据 + metrics-server + Prometheus -> 决策
eval-service     -> runs/<exp_id>/{state,ctx}.json -> 量化评估报告
```

### 4.5 工作流调用链路

```text
python wf.py run --config configs/*.yaml
  -> WorkflowEngine（加载配置、初始化 ctx/state、断点续跑）
    -> 动作（actions/*）通过 kubectl exec 到 orchestrator Pod 调用集群内服务
    -> 算法（algos/*）读取 ctx（注入时刻、故障信息、trace、指标）
    -> 落盘 runs/<exp_id>/state.json、ctx.json
    -> eval.run/eval.summary 调用 eval-service 产出评估
```

## 5. 工作流编排与配置

### 5.1 工作流引擎能力

| 能力 | 用法 | 说明 |
|---|---|---|
| 正常运行 | `python wf.py run --config configs/xxx.yaml --id exp-001` | 顺序执行全部步骤 |
| 单步调试 | `--step <step_id>` | 只跑指定步骤 |
| 断点续跑 | `--from <step_id>` | 从指定步骤继续，之前未成功的标记 SKIPPED |
| 跳步 | `--skip step1,step2` | 跳过指定步骤 |
| 超时 | 步骤配置 `timeout: N` | `SIGALRM` 强制中断 |
| 始终运行 | `always_run: true` | 如清理步骤，失败也会执行 |
| 变量替换 | `${vars.xxx}` | 从配置 `vars` 解析 |

状态落盘：

- `runs/<exp_id>/state.json`：每步状态、耗时、错误、结果摘要
- `runs/<exp_id>/ctx.json`：完整上下文（`inject_time`、`fault_info`、`netem_rules`、`trace_stats`、`eval_report` 等）

### 5.2 实验配置一览

| 配置文件 | 研究方向 | 主要步骤 | 说明 |
|---|---|---|---|
| `configs/military_fault_detect.yaml` | 故障感知（基线） | check_env -> ross_ready -> baseline -> start_sim -> inject(pod_kill) -> detect(baseline) -> tolerate -> measure -> measure_recovery -> cleanup -> **eval/run/summary** | 基于 K8S 就绪轮询的感知对比 |
| `configs/military_fault_tolerate.yaml` | 容错 | check_env -> ross_ready -> baseline -> start_sim -> inject -> tolerate(RL) -> measure_recovery -> cleanup -> **eval/run/summary** | 服务降级+动态调度 |
| `configs/military_consistency.yaml` | 一致性 | check_env -> start_sim -> state_reset/write_base/verify_base -> inject_netem -> traffic -> state_write_divergent(replicate=false) -> state_verify_divergent -> consist(fuzzy+queueing) -> clear_netem -> state_repair -> state_verify_after -> summary -> cleanup -> **eval/run/summary** | 真实 netem + 单副本写入制造分叉，反熵修复收敛 |
| `configs/algo_samples.yaml` | 三项综合 | baseline -> traffic_normal -> inject -> traffic_fault -> detect(DL+trace) -> measure_latency -> trace -> tolerate(RL) -> measure_recovery -> consist -> summary -> **eval/run/summary** | 一次实验跑通感知、容错、一致性全流程 |
| `configs/tracing_pipeline.yaml` | 链路追踪验证 | check_env -> traffic -> trace.collect -> trace.graph -> trace.verify -> **eval/run/summary** | 验证真实 Trace 覆盖、运行态依赖图推导 |
| `configs/ab_baseline.yaml` | A/B（基线） | check_env -> baseline -> traffic -> inject -> detect(baseline) -> measure_latency -> trace -> summary -> **eval/run/summary** | 同流量同注入时序，对比基线 |
| `configs/ab_trace.yaml` | A/B（Trace+DL） | check_env -> baseline -> traffic -> inject -> detect(DL) -> measure_latency -> trace -> summary -> **eval/run/summary** | 对比链路追踪增强方案 |

> 所有配置文件现已统一接入 `eval.run`、`eval.summary`，跑完自动产出量化评估。

## 6. 快速上手（研发）

### 6.1 环境准备
```bash
python3 -m pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes
```

### 6.2 构建与部署
```bash
sudo apt-get install -y buildah
sudo ./scripts/build-images.sh        # 构建 18 个服务镜像（17 业务/平台 + eval-service）
./scripts/deploy.sh                   # 部署全套组件
./scripts/verify_platform.sh          # 平台自检，期望 PASS=61 FAIL=0
```

### 6.3 运行实验
```bash
# 三项综合
python wf.py run --config configs/algo_samples.yaml --id exp-algo-001

# 故障感知
python wf.py run --config configs/military_fault_detect.yaml --id exp-detect-001

# 一致性（真实 netem）
python wf.py run --config configs/military_consistency.yaml --id exp-consist-001

# 链路追踪验证
python wf.py run --config configs/tracing_pipeline.yaml --id exp-trace-001
```

### 6.4 调试与查询
```bash
python wf.py status --id exp-algo-001
python wf.py run --config configs/military_fault_detect.yaml --id exp-detect-002 --step detect
python wf.py run --config configs/military_fault_detect.yaml --id exp-detect-001 --from detect
```

### 6.5 评估查询
```bash
# 集群内
kubectl -n sim-test exec -i deploy/orchestrator -- python -c "
import json,urllib.request
print(json.dumps(json.load(urllib.request.urlopen('http://eval-service:8080/eval/exp-algo-001')),ensure_ascii=False,indent=2))
"

# 列表
kubectl -n sim-test exec -i deploy/orchestrator -- curl -s http://eval-service:8080/eval/list
```

### 6.6 可观测性访问
```bash
kubectl -n sim-test port-forward svc/jaeger 16686:16686      # http://localhost:16686
kubectl -n sim-test port-forward svc/prometheus 9090:9090    # http://localhost:9090
kubectl -n sim-test port-forward svc/api-gateway 8088:8080   # http://localhost:8088
```

## 7. 扩展指南

### 新增微服务
1. 复制 `services/config-service/` 结构（`app.py`、`requirements.txt`、`Dockerfile`）
2. 用 `create_app()` 获得健康、指标、追踪
3. 加入 `manifests/platform-services.yaml`（或 `all-services.yaml`）的 Deployment+Service
4. 在 `scripts/build-images.sh` 和 `scripts/deploy.sh` 的 `SERVICES` 列表添加服务名
5. 若需网关访问，更新 `api-gateway` 白名单

### 新增工作流动作
1. 在 `actions/xxx.py` 用 `@register_action("xxx.action")` 注册
2. 在 `wf.py` 顶部 `import actions.xxx`
3. 在 YAML 中引用 `action: xxx.action` 并传 `params`

### 新增算法
1. 复制 `algos/_template.py`，实现 `detect(ctx)` / `tolerate(ctx)` / `consist(ctx)`
2. 在 YAML 的 `algo.run_detect/tolerate/consist` 的 `params.algo` 指定文件名（不含 `.py`）

## 8. 注意事项

- **镜像**：`:latest` 必须 `imagePullPolicy: IfNotPresent`，修改代码后需 `build-images.sh` + `deploy.sh`
- **故障注入**：`netem` 需宿主机 root 权限，依赖 `k3s crictl`/`nsenter`/`tc`；确定性网络隔离优先用 netem
- **state-store**：默认异步复制，单副本写入（`replicate=false`）会产生分叉，需 `/sync` 或反熵收敛（非强一致）
- **ROSS**：`sim-bridge` 已移除 mock，`/start` 必须跑真实 `mil_sim`，失败会返回错误而非降级
- **追踪**：Jaeger 1.57 需显式 `start/end` 微秒时间戳（已在 `actions/trace.py` 处理）
- **评估**：`eval-service` 通过 hostPath 挂载 `runs/`，若换环境需调整路径或改用 PVC
- **平台治理**：HPA/PDB/Quota/LimitRange 已配置，单节点环境下主要用于策略演示
