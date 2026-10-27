# 平台架构与工作流调用关系

本文说明仓库的分层、服务职责、真实调用链，以及一次实验从 YAML 到结果的执行路径。
配套文档：

- `docs/PLATFORM_AUDIT.md`：平台正确性与实验可运行性的实测审计（含真实输出）
- `docs/K3S-PLATFORM.md`：K3S 平台能力（RBAC / Ingress / HPA / PDB / 配额 / 网络策略）
- `docs/tracing-integration.md`：链路追踪接入细节与 A/B 结果
- `DEBUG.md`：环境搭建、镜像构建与历史踩坑

## 1. 分层

```
┌──────────────────────────────────────────────────────────────┐
│ 实验层  configs/*.yaml                                       │
│  想定 / 故障注入 / 算法 / 测量 / 清理                         │
└───────────────────────────┬──────────────────────────────────┘
                            │ python wf.py run --config ...
┌───────────────────────────▼──────────────────────────────────┐
│ 编排层  wf.py                                                │
│  步骤状态机、变量解析、超时、断点续跑、上下文落盘             │
└───────────┬──────────────────────────────┬───────────────────┘
            │ actions.*                    │ algos.*
┌───────────▼──────────────────┐  ┌────────▼───────────────────┐
│ 动作层 actions/              │  │ 算法层 algos/              │
│ k8s / fault / obs / trace /  │  │ fault_detect_*             │
│ state / ross / measure       │  │ fault_tolerate_*           │
└───────────┬──────────────────┘  │ consist_*                  │
            │                     └────────┬───────────────────┘
            │ kubectl / K8S API / tc       │ Jaeger / Prometheus / K8S
┌───────────▼──────────────────────────────▼───────────────────┐
│ 运行层  K3S namespace sim-test                                │
│  11 个核心仿真微服务 + 6 个通用平台服务                       │
│  Jaeger（追踪） / Prometheus（指标） / Traefik（Ingress）     │
└──────────────────────────────────────────────────────────────┘
```

## 2. 目录职责

| 路径 | 职责 |
| --- | --- |
| `wf.py` | 工作流引擎：顺序执行、`${vars.x}` 解析、单步/续跑/跳步、步骤超时、`runs/<id>/state.json` 与 `ctx.json` |
| `actions/` | 可复用动作，通过 `@register_action("name")` 注册 |
| `algos/` | 三个研究内容的算法实现，统一 `detect(ctx)` / `tolerate(ctx)` / `consist(ctx)` 接口 |
| `configs/` | 实验编排；算法、故障类型、目标服务都在 YAML 中声明 |
| `services/` | FastAPI 微服务；`common/` 为共享的指标与追踪底座 |
| `manifests/` | 核心服务、平台服务、RBAC、Ingress、治理策略、Jaeger、Prometheus |
| `scripts/` | 构建、部署、平台自检、ROSS 验证 |
| `runs/` | 实验输出（`state.json`、`ctx.json`、`trace_stats.json` 等） |
| `ross-sim/` | ROSS 8.1.1 想定源码，由 `sim-bridge` 镜像多阶段编译 |

## 3. 服务清单

### 3.1 核心仿真服务（11）

| 服务 | 职责 | 主要下游 |
| --- | --- | --- |
| `orchestrator` | 业务编排入口，`POST /trigger` 串起完整作战链路 | 全部核心服务 |
| `scenario-mgr` | 想定加载、任务状态 | `state-store`、`log-service` |
| `entity-service` | 作战实体/单位数据 | — |
| `comms-service` | 通信指令下发 | — |
| `sensor-service` | 目标航迹/情报融合 | `entity-service`、`env-service` |
| `fire-control` | 火控交战（核心） | `sensor-service`、`comms-service` |
| `env-service` | 地形气象 | — |
| `log-service` | 日志（非核心，可降级） | — |
| `state-store` | 可复制状态存储（一致性实验对象） | 副本之间反熵 |
| `telemetry-gw` | 遥测落库 | — |
| `sim-bridge` | ROSS 仿真引擎桥接（真实 `mil_sim`） | ROSS 子进程 |

`state-store` 部署 3 副本，并通过无头 Service `state-store-headless` 暴露每个副本 IP；
写入默认异步复制，后台反熵每 5s 拉取 peer 快照并按 LWW 合并。

### 3.2 通用平台服务（6）

| 服务 | 职责 | 关键接口 |
| --- | --- | --- |
| `api-gateway` | 统一入口、健康聚合、受限反向代理 | `/gateway/health`、`/gateway/proxy`、`/gateway/token` |
| `auth-service` | HMAC 无状态令牌签发/校验 | `/auth/token`、`/auth/verify`、`/auth/policy` |
| `config-service` | 版本化配置中心 | `/config`、`/config/{key}`、`/config-history` |
| `notification-service` | 分级通知缓存 | `/notify`、`/notifications`、`/notify/stats` |
| `platform-service` | 平台身份、cgroup 限额、平台组件健康 | `/platform/info`、`/platform/health`、`/platform/capabilities` |
| `chaos-service` | 集群内混沌编排（K8S API + RBAC） | `/chaos/pod-kill`、`/chaos/scale`、`/chaos/self-check` |

所有服务都继承 `services/common/base.py`：

- `GET /healthz`、`GET /readyz/business`、`GET /metrics`、`GET /tracez`
- ASGI 中间件为每个入站请求生成 SERVER span，经 OTLP/HTTP JSON 发往 Jaeger
- Prometheus 指标名会把服务名中的 `-` 规范为 `_`

## 4. 真实调用链

### 4.1 业务主链路

`POST orchestrator:8080/trigger?scenario=battalion_attack`：

```
orchestrator
├─ healthz -> 10 个核心服务
├─ scenario-mgr -> state-store / log-service
├─ fire-control
│   ├─ sensor-service
│   │   ├─ entity-service
│   │   └─ env-service
│   └─ comms-service
└─ telemetry-gw
```

一次调用在 Jaeger 中形成一条完整 trace（实测 30+ span、4 层深度），
`peer.service` 标签记录被调方，故障时可直接定位。

### 4.2 平台链路

```
client -> Ingress(traefik) -> api-gateway
api-gateway -> auth-service        (令牌签发)
api-gateway -> <core service>      (/gateway/proxy)
platform-service -> api-gateway / auth-service / config-service / notification-service
platform-service -> config-service (读取默认想定)
chaos-service -> K8S API           (Pod kill / scale，受 RBAC 限制)
state-store(replica) -> state-store(replica)  (异步复制 + 反熵)
```

### 4.3 观测链路

```
各服务 /metrics  <-  Prometheus (5s 抓取)
各服务 span      ->  Jaeger collector :4318 -> Query API :16686
actions/trace.py -> Jaeger Query -> 服务级指标 + 运行态依赖图
algos/fault_detect_dl.py -> trace 证据 + metrics-server + Prometheus -> 根因排序
```

## 5. 一次实验的执行路径

以 `configs/algo_samples.yaml` 为例：

1. `obs.collect_baseline`：metrics-server + Prometheus 采集基线。
2. `traffic.probe`：在 orchestrator Pod 内发真实 `/trigger` 流量。
3. `fault.inject_pod_kill`：删除目标 Pod，记录 `inject_time` 到 `ctx`。
4. `traffic.probe`：故障期间继续发流量，产生失败 span。
5. `algo.run_detect`：加载 `algos/fault_detect_dl.py`，查 Jaeger（窗口从注入前 2s 开始）、
   采集指标、PCA/AutoEncoder 评分、按失败调用对端做贝叶斯根因排序。
6. `measure.detect_latency`：输出证据延迟、扫描耗时、端到端耗时；负值会被标记并钳到 0。
7. `trace.collect`：落盘 `trace_stats.json` / `trace_graph.json`。
8. `algo.run_tolerate` / `measure.recovery_time`：容错与恢复时间。
9. `algo.run_consist`：读取 `ctx["state_verify"]`（若前序步骤做过副本校验），
   把"副本分叉"作为事件参与模糊-排队决策。
10. `measure.summary`：汇总三项研究内容与状态一致性校验结果。

### 上下文与断点续跑

- `runs/<exp_id>/state.json`：每步状态、耗时、错误、结果摘要。
- `runs/<exp_id>/ctx.json`：完整上下文（`inject_time`、`fault_info`、`k8s_watch`、`netem_rules` 等）。
- `--from <step>`：从指定步骤继续，之前未成功的步骤标记为 `SKIPPED`。
- `--step <step>`：只跑一步，便于算法调试。
- 步骤 `timeout` 通过 `SIGALRM` 强制执行；`always_run: true` 用于清理步骤。

## 6. 扩展方式

### 新增一个微服务

1. 复制 `services/config-service/` 的 `Dockerfile`、`requirements.txt`、`app.py` 结构。
2. 使用 `create_app()` 获得健康检查、指标与追踪。
3. 在 `manifests/platform-services.yaml`（或 `all-services.yaml`）增加 Deployment + Service。
4. 在 `scripts/build-images.sh` 的 `SERVICES` 与 `scripts/deploy.sh` 的 `SERVICES` 中登记。
5. 若需要被网关访问，把服务名加入 `api-gateway` 的 `ALLOWED`。

### 新增一个实验动作

```python
# actions/my_action.py
from wf import register_action

@register_action("my.action")
def my_action(ctx, params):
    ...
```

并在 `wf.py` 的 `main()` 中 `import actions.my_action`。

### 新增一个算法

复制 `algos/_template.py`，实现 `detect(ctx)` / `tolerate(ctx)` / `consist(ctx)`，
在 YAML 的 `algo.run_*` 参数中引用文件名即可。
