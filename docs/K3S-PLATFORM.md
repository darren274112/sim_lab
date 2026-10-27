# K3S 平台能力与清单

平台运行在单节点 K3S（`v1.36.5+k3s1`，containerd）上，命名空间 `sim-test`。
本文说明仓库中与 K3S 平台相关的清单、验证方法与注意事项。

## 1. 清单总览

| 文件 | 内容 | 作用 |
| --- | --- | --- |
| `manifests/namespace.yaml` | Namespace | 实验隔离边界 |
| `manifests/all-services.yaml` | 10 个核心 Deployment（含 state-store 3 副本） | 业务仿真服务 |
| `manifests/fire-control.yaml` | fire-control Deployment + Service | P0 核心服务独立配额 |
| `manifests/services.yaml` | 11 个 ClusterIP Service + `state-store-headless` | 服务发现 |
| `manifests/platform-services.yaml` | 6 个平台服务 + Secret | 通用平台能力 |
| `manifests/platform-rbac.yaml` | ServiceAccount / Role / RoleBinding | chaos-service 最小权限 |
| `manifests/platform-ingress.yaml` | Ingress（Traefik） | 集群外访问 API 网关 |
| `manifests/platform-policy.yaml` | LimitRange / ResourceQuota / PDB / HPA | 资源治理与可用性 |
| `manifests/prometheus.yaml` | ConfigMap / Deployment / Service | 指标采集（已纳入仓库） |
| `manifests/jaeger.yaml` | Deployment / Service | 链路追踪后端 |

`scripts/deploy.sh` 会按以下顺序应用：

```
namespace -> all-services -> fire-control -> services
          -> platform-rbac -> platform-services -> platform-policy -> platform-ingress
          -> jaeger -> prometheus
```

## 2. RBAC（最小权限）

`chaos-service` 使用专用 ServiceAccount `sim-chaos`，只允许在 `sim-test` 内：

- `pods`: get/list/watch/delete
- `deployments`: get/list/watch
- `deployments/scale`: get/patch/update
- `events`: get/list

验证：

```bash
kubectl -n sim-test auth can-i --as=system:serviceaccount:sim-test:sim-chaos delete pods
# yes
kubectl -n sim-test auth can-i --as=system:serviceaccount:sim-test:sim-chaos delete nodes
# no

kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://chaos-service:8080/chaos/self-check", timeout=5).read().decode())
PY
```

## 3. Ingress（Traefik）

K3S 默认安装 Traefik，`IngressClass` 名称为 `traefik`。

```bash
kubectl get ingressclass
kubectl -n sim-test get ingress
```

访问方式：

```bash
# 方式一：本机 hosts 指向任一节点 IP
echo "<node-ip> sim-lab.local" | sudo tee -a /etc/hosts
curl http://sim-lab.local/gateway/health

# 方式二：端口转发（不依赖 hosts）
kubectl -n sim-test port-forward svc/api-gateway 8088:8080
curl http://127.0.0.1:8088/gateway/health
```

## 4. 资源治理

### LimitRange

`sim-defaults` 为未声明 `resources` 的容器提供默认值：

- request: `50m CPU / 64Mi`
- limit: `300m CPU / 256Mi`

这样 `ResourceQuota` 的 `requests.*` / `limits.*` 规则不会拒绝老服务重启。

### ResourceQuota

`sim-quota` 限制命名空间总量：

| 资源 | 上限 |
| --- | --- |
| `requests.cpu` | 4 |
| `requests.memory` | 8Gi |
| `limits.cpu` | 10 |
| `limits.memory` | 16Gi |
| `pods` | 50 |

```bash
kubectl -n sim-test describe resourcequota sim-quota
```

### PDB

| PDB | 策略 | 说明 |
| --- | --- | --- |
| `state-store-pdb` | `minAvailable: 2` | 3 副本状态存储，节点维护时至少保留 2 个 |
| `api-gateway-pdb` | `minAvailable: 1` | 网关至少保留 1 个 |

### HPA

`api-gateway-hpa`：CPU 利用率 70% 时在 1~4 副本之间伸缩。
依赖 K3S 自带 metrics-server：

```bash
kubectl -n sim-test get hpa
kubectl top pods -n sim-test
```

> HPA 只作用于 `api-gateway`，避免干扰 `state-store` 的固定 3 副本一致性实验。

## 5. 网络策略与故障注入

### NetworkPolicy

`actions/fault.py::fault.inject_network_partition` 会创建 `sim-fault-<service>` 的
NetworkPolicy，把目标服务的入站/出站置空（拒绝全部）。K3S 默认 CNI 支持 NetworkPolicy。

```bash
kubectl -n sim-test get networkpolicy
# 注入后出现 sim-fault-state-store
```

### tc/netem（宿主机动作）

`fault.inject_netem` 不是 K8S API，而是在宿主机上：

1. 用 `k3s crictl inspect <container-id>` 解析容器 PID；
2. `nsenter -t <pid> -n tc qdisc replace dev <iface> root netem delay ... loss ...`。

因此需要：root 权限、宿主机 `tc`/`nsenter`/`ip`、`k3s crictl`。
清理使用 `tc qdisc del`，规则同时写入 `runs/<id>/netem.json`，支持断点续跑时恢复清理。

## 6. 指标与追踪

- Prometheus 抓取核心 11 个服务 + 平台 6 个服务的 `/metrics`，间隔 5s。
- `prometheus.yaml` 中的 ConfigMap 是唯一配置来源，不再依赖镜像内烘焙的配置。
- Jaeger all-in-one 接收 OTLP/HTTP JSON（`:4318`），Query API `:16686`。

```bash
kubectl -n sim-test get pods -l app=prometheus
kubectl -n sim-test logs deploy/prometheus --tail=20
kubectl -n sim-test port-forward svc/prometheus 9090:9090
kubectl -n sim-test port-forward svc/jaeger 16686:16686
```

## 7. 一键自检

```bash
./scripts/verify_platform.sh
```

检查项：节点、Pod、Deployment、18 个 Service 端点、`state-store-headless` 副本数、
集群内 HTTP 健康、`/trigger` 业务链路、状态副本一致性、Jaeger、Prometheus、HPA/PDB/配额、
chaos-service RBAC 自检。

## 8. 已知限制

1. **单节点集群**：PDB/HPA 能演示策略对象，但真正的多节点调度、节点故障转移需要多节点集群。
2. **NetworkPolicy 依赖 CNI**：K3S 默认 flannel 不强制执行策略；本环境需额外 CNI（如 Calico）
   才具备强隔离。`fault.inject_network_partition` 的动作会成功创建策略对象，但隔离效果取决于 CNI。
   需要确定性隔离时优先使用 `fault.inject_netem`（真实生效）。
3. **netem 需要宿主机权限**：容器内无法完成，故放在 workflow 宿主机侧，而不是 `chaos-service`。
4. **Jaeger/Prometheus 存储为内存/emptyDir**：Pod 重建即清空，长跑需换持久化存储。
5. **镜像为本地导入**：`:latest` 必须配 `imagePullPolicy: IfNotPresent`，否则会去 Docker Hub 拉取失败。
