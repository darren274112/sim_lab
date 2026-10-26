# 调试指南

## 环境准备
```bash
pip install -r requirements.txt
kubectl get nodes  # 确认集群可用
```

## 部署微服务
```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/all-services.yaml
kubectl apply -f manifests/fire-control.yaml
kubectl -n sim-test get pods
```

## 编写算法
复制模板并实现函数：
```bash
cp algos/_template.py algos/my_algorithm.py
```

函数签名：
- `detect(ctx)` - 故障感知（返回需包含 `detected_at` 用于延迟计算）
- `tolerate(ctx)` - 容错决策
- `consist(ctx)` - 一致性维护

`ctx` 常用字段：`inject_time`, `fault_info`, `timestamps`, `vars`, `config`, `exp_id`

## 运行实验
```bash
# 完整运行
python wf.py run --config configs/military_fault_detect.yaml --id exp-001

# 单步调试
python wf.py run --config configs/military_fault_detect.yaml --id exp-002 --step detect

# 从某步继续
python wf.py run --config configs/military_fault_detect.yaml --id exp-001 --from detect

# 跳过步骤
python wf.py run --config configs/military_fault_detect.yaml --id exp-001 --skip baseline,cleanup

# 查看状态
python wf.py status --id exp-001
```

## 注意事项
- 故障感知算法需返回 `detected_at`（时间戳）以便 `measure.detect_latency` 正确计算延迟
- 状态保存在 `runs/<exp_id>/state.json`，可直接查看调试
- `cleanup` 步骤设置了 `always_run: true`，失败也会执行
- 镜像需使用 `sim/*:latest`，k3s 需用 `k3s ctr images import` 导入，kind 需用 `kind load docker-image`
