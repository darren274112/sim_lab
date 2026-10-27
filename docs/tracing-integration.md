# 链路追踪接入（Jaeger）——从拓扑图到真实 Trace

> 目标：让"基于链路追踪的故障感知"有真实数据支撑，而不是一张手画的调用图。
> 环境：K3S 1.36 / namespace `sim-test` / 11 个 FastAPI 微服务。

## 1. 结论

| 项 | 接入前 | 接入后 |
| --- | --- | --- |
| 调用拓扑来源 | 代码里写死的 `DEP_GRAPH` 字典 | Jaeger 中真实 span 的父子关系（`CHILD_OF`）推导 |
| 服务级指标 | Prometheus 请求率 + metrics-server CPU/内存 | 追加 span 数 / 错误率 / p50 / p95 / max / 首个错误时刻 |
| 故障定位依据 | 只看 K8S Pod 就绪态 | 失败 span 的 `peer.service` 直接指认故障方；沿真实拓扑做传播/影响面分析 |
| 感知时刻 | K8S 轮询（受采样周期与自愈速度影响） | 首个失败 span 的时间戳（事实时刻，不受轮询周期影响） |
| 可验证性 | 无法复盘一次调用 | 一条 trace 39 个 span，可在 Jaeger UI 逐层展开 |

## 2. 组件与数据流

```
业务请求
  │  traceparent (W3C)
  ▼
┌───────────────────────────┐   OTLP/HTTP JSON    ┌──────────────┐
│ 微服务 (common/tracing.py)│ ──────────────────► │   Jaeger     │
│  · ASGI 中间件 → SERVER span│   :4318/v1/traces  │ all-in-one   │
│  · 下游调用 → CLIENT span  │                    │ :16686 查询  │
└───────────────────────────┘                    └──────┬───────┘
                                                        │ /api/services, /api/traces
                                                        ▼
                                        ┌───────────────────────────────┐
                                        │ actions/trace.py              │
                                        │ trace.collect / trace.graph    │
                                        │ traffic.probe（造真实流量）    │
                                        └──────────────┬────────────────┘
                                                       ▼
                                        ┌───────────────────────────────┐
                                        │ algos/fault_detect_dl.py      │
                                        │ PCA + AutoEncoder + 注意力     │
                                        │ + 真实 Trace 图/证据定位       │
                                        └───────────────────────────────┘
```

| 文件 | 作用 |
| --- | --- |
| `services/common/tracing.py` | 追踪 SDK：`traceparent` 解析/注入、`Span`、ASGI 中间件、OTLP 导出器（纯标准库，零第三方依赖） |
| `services/common/base.py` | 所有服务统一的 `create_app()` 挂中间件；新增 `/tracez` 自检端点 |
| `deploy_jaeger.py` / `manifests/jaeger.yaml` | 部署 Jaeger（all-in-one，内存存储 2 万条 trace） |
| `rebuild_services.py` | 把 `common/` 与改动过的 `app.py` 热补丁进 11 个镜像（不重新 pip install，约 2.5 分钟） |
| `actions/trace.py` | `trace.collect` / `trace.graph` / `trace.verify` / `traffic.probe` |
| `configs/tracing_pipeline.yaml` | 链路追踪验证工作流 |
| `configs/ab_baseline.yaml` / `ab_trace.yaml` | 感知 A/B（同流量、同注入时序，只换算法） |
| `scripts/verify_tracing.py`、`scripts/podpy.py` | 端到端自检 / 在 Pod 内跑脚本 |

## 3. 关键实现

**跨服务串链**：`traceparent`（W3C）随下游 HTTP 头传播。服务内 `traced_json_request()` 同时开 CLIENT span 并注入头，
被打的服务开 SERVER span，父 span 指向调用方 CLIENT span —— 因此一条 trace 是完整的调用树。

**零依赖导出**：手写 OTLP/HTTP JSON payload（`resourceSpans → scopeSpans → spans`），后台线程按批 POST 到
`http://jaeger:4318/v1/traces`。队列有界（4096），失败只计数不抛错，`/tracez` 可看 `spans_sent/failed/dropped`。

**避开噪声**：`/metrics`（Prometheus 每 5s 抓取）不建 span，否则每 5s 产生一条单 span 的"根 trace"，
把真正的业务链路挤出采样窗口——第一版就踩了这个坑（一次查询回来 526 条 trace 里绝大多数是 `/metrics/`）。

**真实多级链路**：`POST /trigger` 现在会真正走业务：
```
orchestrator ─┬─ scenario-mgr ─┬─ state-store
              │                └─ log-service
              ├─ fire-control ─┬─ sensor-service ─┬─ entity-service
              │                │                  └─ env-service
              │                └─ comms-service
              └─ telemetry-gw
```
一次请求 = 39 个 span（实测），4 层深度。

**服务名解析**：Jaeger 1.57 的 `/api/traces` 把 process 放在 `trace.processes[processID]` 里，不在 span 上；
采集时两处都兼容（`processID → processes[].serviceName`，回退 `span.process.serviceName`）。

## 4. 真实数据（`trace-001` / `ab-trace-001`）

```
[TRACE] traces=16 spans=499 edges=16 coverage=1.0
[TRACE] runtime graph (from real spans):
        fire-control   -> comms-service, sensor-service
        orchestrator   -> comms-service, entity-service, env-service, fire-control,
                          log-service, scenario-mgr, sensor-service, sim-bridge,
                          state-store, telemetry-gw
        scenario-mgr   -> log-service, state-store
        sensor-service -> entity-service, env-service
[TRACE] edge coverage = 1.0
```

- 16 条边全部由真实 span 推导，与设计态拓扑一致（coverage = 1.0）
- 服务级 p95（正常态）：entity 4.0ms / comms 5.4ms / sensor 13.4ms / scenario-mgr 14.4ms /
  fire-control 22.4ms / orchestrator 96.8ms
- 故障态出现 `failed_peers={'fire-control': 3}`：3 个失败 span 的 `peer.service` 指向 fire-control

## 5. 算法改造（`algos/fault_detect_dl.py`）

1. **拓扑**：`prop_graph` 由 `runtime_graph(trace)` 构造（边 < 3 条时退回设计态图并标注 `graph_source`）。
2. **特征**：CPU / 内存 / 请求率 / 可用性 4 维 → PCA + AutoEncoder + 注意力加权复合评分（不变）。
3. **证据**：`failed_peers`（谁被调失败）进入贝叶斯似然后验：
   - `r == peer` → 似然 0.90（强证据，次数加权，最多计 3 次）
   - `peer` 在 r 的传播可达集内 → 0.40
   - 否则 0.05
4. **感知时刻**：取 `min(首次未就绪, 首个错误 span 时刻)`。错误 span 记在调用方身上，
   所以凡是有错误 span 的服务都参与比较——否则会把 0.6s 的证据漏掉，退化成"算法跑完的时刻"。
5. **影响面**：沿传播图 BFS，输出受影响的异常服务与跳数（`impacted_services`）。
6. **阈值**：`max(med + 3·1.4826·MAD, 2.5·med, 0.01)`。只用 MAD 时中位数极小的场景会把
   "0.006 vs 0.0055"判成异常，噪声服务（log-service/telemetry-gw）反而干扰根因排序。

## 6. A/B 结果（同一流量、同一注入时序，只换感知算法）

| 指标 | K8S 轮询基线 | 深度学习 + 真实 Trace |
| --- | --- | --- |
| 是否检出 | 是（Pod 已被替换的兜底事件；12s 轮询期间**未看到未就绪窗口**） | 是（首个失败 span） |
| 感知延迟 | **18.70 s** | **0.605 s**（↓ 96.8%） |
| 根因服务 | fire-control（由实验预设告知，不是"定位"） | fire-control（**自动**，`failed_peers` 指认） |
| 置信度 | 0.60 | 0.977 |
| 影响面 / 传播路径 | 无 | impacted=[orchestrator(1)]，path=[fire-control] |
| 算法扫描耗时 | 12.14 s | 8.3 s |

> 基线之所以慢：pod-kill 后 Pod 约 3~5s 就被重建为 Ready，500ms 一次的轮询窗口落空，
> 最后靠"Pod 名字变了"这一兜底事件才发现。链路证据是"业务真的失败"的时刻，不受自愈速度影响。

## 7. 局限与后续

1. **公平测量**：本实验的流量是"批次打点"，一次 `/trigger` 约 200ms。
   生产化对比应在注入前启动**持续流量**（如 20 QPS），并把检测器做成**流式消费**（每秒拉 Jaeger），
   此时 trace 侧延迟 ≈ 1 个请求周期。
2. **采样策略**：现在全量采样（`TRACE_SAMPLE_RATE=1.0`）。长跑 48h 建议头部采样 10% + 错误 span 全留
   （tail-based 需要 collector 侧配置）。
3. **存储**：Jaeger 目前是内存存储（2 万条上限，重启即清空），48h 长跑需换 badger/Cassandra。
4. **指标联动**：可把 trace 的 p95/错误率导出为 Prometheus 指标，做"指标发现异常 → trace 定位"的两级联动。
5. **追踪维度**：目前是服务级。若要定位到"哪个调用参数导致慢"，需要补 span 事件（`span.log`）。
