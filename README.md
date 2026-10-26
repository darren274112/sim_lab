# 仿真微服务算法验证沙箱

面向研发人员开发验证算法的最简工具。使用自研步骤编排器串联实验流程，支持快速插拔算法、单步调试。

## 架构

- **wf.py**: 自研简单工作流引擎（步骤编排器）
- **actions/**: 动作实现（K8s操作、故障注入、观测、算法调用等）
- **algos/**: 算法实现目录（研发直接在这里写算法）
- **configs/**: 实验配置YAML
- **services/**: 11个军事仿真微服务 + ROSS桥接
- **manifests/**: K8s部署清单

## 军事仿真微服务（11个）

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
pip install pyyaml kubernetes
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

## 注意

- 默认使用命名空间 `sim-test`，限制故障注入范围
- 时间戳由工作流统一记录，确保计时口径一致
- 支持中断后恢复，清理步骤设置 `always_run: true`
