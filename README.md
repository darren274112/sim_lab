# 仿真微服务算法验证沙箱

面向研发人员开发验证算法的最简工具。使用自研步骤编排器串联实验流程，支持快速插拔算法、单步调试。

## 架构

- **wf.py**: 自研简单工作流引擎（步骤编排器）
- **actions/**: 动作实现（K8s操作、故障注入、观测、算法调用等）
- **algos/**: 算法实现目录（研发直接在这里写算法）
- **configs/**: 实验配置YAML
- **services/**: 11个仿真微服务 + ROSS桥接
- **manifests/**: K8s部署清单

## 仿真微服务（11个）

1. orchestrator - 自研工作流编排器
2. scenario-mgr - 想定管理（营攻击、空防、合同作战等）
3. entity-service - 实体管理（作战单位）
4. comms-service - 通信服务
5. sensor-service - 传感器/情报
6. fire-control - 火控系统（核心）
7. env-service - 环境（地形气象）
8. log-service - 日志服务（非核心，可降级）
9. state-store - 状态存储（一致性实验对象）
10. telemetry-gw - 遥测网关
11. sim-bridge - ROSS仿真引擎桥接

## 快速开始

### 安装依赖
```bash
python3 -m pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
```

### 构建并部署微服务
首次运行需构建 11 个镜像并导入 K3S（使用 buildah，无需 Docker）：
```bash
sudo apt-get install -y buildah
sudo ./scripts/build-images.sh
./scripts/deploy.sh
```

`sim-bridge` 镜像通过多阶段构建内置了 ROSS 8.1.1 和 `mil_sim`，不需要为 ROSS 单独构建镜像或部署服务。sim-bridge 已移除 mock 模式，调用 `/start` 即运行真实 ROSS。验证：
```bash
./scripts/test-ross.sh
```

### 查看实验状态
```bash
python wf.py status --id exp-xxx
```

### 运行故障感知实验
```bash
python wf.py run --config configs/military_fault_detect.yaml --id test-001
```

### 单步调试算法
```bash
python wf.py run --config configs/military_fault_detect.yaml --id test-002 --step detect
```

### 从某一步继续
```bash
python wf.py run --config configs/military_fault_detect.yaml --id test-001 --from detect
```

### 跳过步骤
```bash
python wf.py run --config configs/military_fault_detect.yaml --id test-001 --skip baseline,cleanup
```

## 算法接入

在 `algos/` 目录创建新文件：

```python
# algos/my_fault_detect.py
import time

def detect(ctx):
    # 从ctx中获取信息：inject_time, fault_info等
    # 实现你的算法逻辑
    return {
        "detected": True,
        "detected_at": time.time(),
        "faulty_service": "fire-control",
        "confidence": 0.95
    }
```

然后在配置文件中引用：
```yaml
- id: detect
  action: algo.run_detect
  params:
    algo: my_fault_detect  # 对应 algos/my_fault_detect.py
```

## 三个研究课题

1. **故障感知** - 基于深度学习和链路追踪的仿真微服务故障感知
2. **容错** - 基于服务降级和动态调度的仿真微服务容错  
3. **一致性** - 基于模糊控制与排队论协同决策的仿真微服务状态一致性维护

## 链路追踪（Jaeger）

链路追踪不是画的拓扑图，而是真实 Trace 数据。组成：

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| 追踪 SDK | `services/common/tracing.py` | W3C `traceparent` 传播、ASGI 中间件生成 SERVER span、OTLP/HTTP JSON 批量导出（纯标准库，无第三方依赖） |
| 追踪后端 | `sim/jaeger:latest`（`deploy_jaeger.py` 部署） | 收 span、存 trace、提供 UI(16686) 与查询 API |
| 采集动作 | `actions/trace.py` | `trace.collect` 聚合服务级指标 + 运行态依赖图；`trace.graph` 对比设计态；`trace.verify` 校验落库；`traffic.probe` 打业务流量 |
| 算法消费 | `algos/fault_detect_dl.py` | 依赖图来自真实 span 父子关系；失败 span 的 `peer.service` 指认故障服务；首个错误 span 时刻作为感知时刻 |

一次 `POST orchestrator:8080/trigger` 会串起真实调用链：

```
orchestrator -> scenario-mgr -> state-store / log-service
             -> fire-control -> sensor-service -> entity-service / env-service
             -> telemetry-gw
```

验证：
```bash
python deploy_jaeger.py                 # 部署 Jaeger（已部署可跳过）
python scripts/verify_tracing.py        # OTLP 收口 + 多级 trace 自检
python wf.py run --config configs/tracing_pipeline.yaml --id trace-001
python wf.py run --config configs/algo_samples.yaml --id algo-003
```

查看 Trace：`kubectl -n sim-test port-forward svc/jaeger 16686:16686` → http://localhost:16686
（服务选 `orchestrator`，按 `peer.service` 即可看到哪个下游被调失败）

> 注意：镜像里的 `/app/common` 是构建期拷入的，改动 `services/common/*` 后需重建镜像。
> 已部署环境可用 `python rebuild_services.py [服务名...]` 热补丁（不重新 pip install）。

## 注意

- 默认使用命名空间 `sim-test`，限制故障注入范围
- 时间戳由工作流统一记录，确保计时口径一致
- 支持中断后恢复，清理步骤设置 `always_run: true`
