# 调试指南

## K3S 安装记录（2026-10-26）

### 环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Ubuntu 22.04.4 LTS（systemd） |
| 内核 | 5.15.0-94-generic |
| 资源 | 2 vCPU / 7.7 GiB RAM / 38 GiB 可用磁盘 |
| 安装方式 | 单节点 server，root 安装，systemd 开机自启 |
| K3S 版本 | v1.36.5+k3s1 |
| 容器运行时 | containerd 2.3.4-k3s1.36 |

### 网络限制

如果环境无法直连 `github.com` 和 `registry-1.docker.io`，可以：

- K3S 二进制改用 Rancher 国内镜像 `rancher-mirror.rancher.cn` 下载。
- 系统镜像（pause、coredns、traefik 等）通过 `docker.m.daocloud.io` 拉取。

### 安装步骤

1. 配置 containerd 的 Docker Hub 镜像加速（安装前创建，K3S 启动时读取）：

```bash
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<'EOF'
mirrors:
  docker.io:
    endpoint:
      - "https://docker.m.daocloud.io"
  registry-1.docker.io:
    endpoint:
      - "https://docker.m.daocloud.io"
EOF
```

2. 下载国内版安装脚本（不要用 `get.k3s.io`，它默认从 GitHub 下载且会忽略 `INSTALL_K3S_MIRROR`）：

```bash
curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh -o /tmp/k3s-install.sh
```

3. 指定版本执行安装，并把 kubeconfig 权限设为 644：

```bash
sudo env \
  INSTALL_K3S_MIRROR=cn \
  INSTALL_K3S_VERSION=v1.36.5+k3s1 \
  INSTALL_K3S_EXEC="server --write-kubeconfig-mode 644" \
  sh /tmp/k3s-install.sh
```

4. 为日常登录用户配置 kubectl 访问（K3S 默认 kubeconfig 在 `/etc/rancher/k3s/k3s.yaml`）：

```bash
sudo install -d -m 700 -o "$USER" -g "$USER" "$HOME/.kube"
sudo install -m 600 -o "$USER" -g "$USER" /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
```

为root用户配置 `/root/.kube/config`。

5. 验证：

```bash
systemctl status k3s --no-pager
kubectl get nodes -o wide
kubectl get pods -A
kubectl apply --dry-run=client -f manifests/namespace.yaml -f manifests/all-services.yaml -f manifests/fire-control.yaml
```

实测结果：

```text
NAME     STATUS   ROLES           AGE    VERSION
ubu-22   Ready    control-plane   2m2s   v1.36.5+k3s1

NAMESPACE     NAME                                      READY   STATUS
kube-system   coredns-7cfb7bc9c7-cx96p                  1/1     Running
kube-system   local-path-provisioner-77b9867795-w4c9x   1/1     Running
kube-system   metrics-server-6dc596dfb8-rl8rw           1/1     Running
kube-system   svclb-traefik-e191e098-x8654              2/2     Running
kube-system   traefik-7c8544f77-k4xnb                   1/1     Running
```

`kubectl apply --dry-run=client` 校验了 `sim-test` 命名空间及微服务清单，语法和 API 版本兼容当前 K3S。
（本节是 2026-10-26 首次部署时的 11 个核心服务快照；现在仓库已扩展到 11 核心 + 6 平台服务，
完整清单与部署顺序见 `docs/K3S-PLATFORM.md`。）

### 常用运维命令

```bash
systemctl status k3s          # 查看服务状态
systemctl restart k3s         # 重载配置（registries.yaml 修改后需要重启）
journalctl -u k3s -f          # 查看 K3S 日志
kubectl get nodes -o wide     # 查看节点
kubectl get pods -A           # 查看全部 Pod
k3s ctr images ls             # 查看 containerd 镜像
k3s ctr images import xxx.tar # 导入离线镜像
```

### 踩坑记录

- `get.k3s.io` 的安装脚本不支持 `INSTALL_K3S_MIRROR=cn`，会直接访问 GitHub 并报 `Download failed`。必须使用 `rancher-mirror.rancher.cn/k3s/k3s-install.sh`。
- 国内镜像的 channel 接口（`/k3s/channels`）返回 404，因此安装时用 `INSTALL_K3S_VERSION` 固定版本，避免查询 stable channel。
- Docker Hub 不可直连。若后续新增了其他镜像仓库，需要同步补充 `/etc/rancher/k3s/registries.yaml` 并重启 K3S。
- K3S 使用 containerd，没有 Docker。`sim/*:latest` 镜像不能直接 `docker load`，需用 `k3s ctr images import`。
- 项目镜像由 `scripts/build-images.sh` 构建并导入，部署步骤见下一节；修改服务代码后需要重新构建并重启 Deployment。

### 卸载

```bash
sudo /usr/local/bin/k3s-uninstall.sh
```

## 镜像构建与部署（2026-10-26）

### 背景

`manifests` 中的镜像为 `sim/<service>:latest`，仓库原先没有构建脚本；本机没有 Docker，K3S 使用 containerd。因此使用 **buildah** 构建，导出 docker-archive 后通过 `k3s ctr images import` 导入。

### 依赖

```bash
sudo apt-get install -y buildah python3-pip
python3 -m pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
```

`requirements.txt` 只包含 `wf.py` 运行所需的 `pyyaml`、`kubernetes`。`kubernetes` 客户端还需要兼容的 `requests/urllib3`，本环境已一并安装。

### 构建镜像

```bash
sudo ./scripts/build-images.sh               # 构建全部 17 个服务（11 核心 + 6 平台）
sudo ./scripts/build-images.sh fire-control  # 只构建指定服务
IMPORT=0 sudo ./scripts/build-images.sh      # 只构建，不导入 K3S
```

脚本行为：

- 镜像名使用 `docker.io/sim/<service>:latest`，对应 K8S 清单中的 `sim/<service>:latest`（containerd 会补全 `docker.io/` 前缀）。
- PyPI 依赖通过 `--build-arg PIP_INDEX_URL` 使用阿里云镜像；可用 `PIP_INDEX_URL=https://pypi.org/simple` 覆盖。
- 构建完成后导出 docker-archive，逐个执行 `k3s ctr -n k8s.io images import`。
- 构建缓存命中时单服务约 8~16 秒，首次全量构建约 10 分钟（取决于网络）。

确认镜像已导入：

```bash
sudo k3s ctr -n k8s.io images ls -q | grep '^docker.io/sim/'
```

应看到 17 个镜像：核心服务 `orchestrator`、`scenario-mgr`、`entity-service`、`comms-service`、
`sensor-service`、`fire-control`、`env-service`、`log-service`、`state-store`、`telemetry-gw`、
`sim-bridge`，以及平台服务 `api-gateway`、`auth-service`、`config-service`、
`notification-service`、`platform-service`、`chaos-service`。

### 部署

```bash
./scripts/deploy.sh
```

脚本会依次完成：

1. apply `namespace.yaml`、`all-services.yaml`、`fire-control.yaml`、`services.yaml`
2. apply 平台清单：`platform-rbac.yaml`、`platform-services.yaml`、`platform-policy.yaml`、`platform-ingress.yaml`
3. 默认 apply `jaeger.yaml`、`prometheus.yaml`；`OBSERVABILITY=0` 可跳过
4. 重启全部 Deployment，确保重新构建的 `:latest` 镜像生效（`RESTART=0` 可跳过）
5. 等待滚动更新完成、旧 Pod 退出、全部 Pod Ready
6. 输出 Pod 和 Service 列表

如需手工部署：

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/all-services.yaml
kubectl apply -f manifests/fire-control.yaml
kubectl apply -f manifests/services.yaml
kubectl apply -f manifests/platform-rbac.yaml
kubectl apply -f manifests/platform-services.yaml
kubectl apply -f manifests/platform-policy.yaml
kubectl apply -f manifests/platform-ingress.yaml
kubectl apply -f manifests/jaeger.yaml
kubectl apply -f manifests/prometheus.yaml
```

说明：

- `manifests/services.yaml` 为 11 个核心服务提供 ClusterIP Service（`fire-control` 的 Service 在 `fire-control.yaml`），并额外提供 `state-store-headless`；平台服务的 Service 在 `platform-services.yaml`。
- 所有 Deployment 显式设置 `imagePullPolicy: IfNotPresent`，否则 `:latest` 默认 `Always`，会去 Docker Hub 拉取本地导入的 `sim/*` 镜像并失败。
- `state-store` 保持 3 副本，正常运行时应看到 21 个 Pod：10 个单副本核心服务 + `state-store` 3 副本 + 6 个平台服务 + Jaeger + Prometheus。

### 验证

```bash
kubectl -n sim-test get deploy
kubectl -n sim-test get pods -o wide
```

结果：19 个 Deployment 全部 `Available`（17 个服务 + Jaeger + Prometheus），21 个 Pod 全部 `Running`。

内部服务连通性：

```bash
kubectl -n sim-test exec deploy/orchestrator -- python -c "
import urllib.request
req = urllib.request.Request('http://orchestrator:8080/trigger', method='POST')
print(urllib.request.urlopen(req, timeout=10).read().decode())
"
```

输出中 `scenario-mgr`、`entity-service`、`comms-service`、`sensor-service`、`fire-control`、`sim-bridge` 均为 `ok`。

三个实验工作流调试：

```bash
sudo -u root python3 wf.py run --config configs/military_fault_detect.yaml --id debug-detect2
sudo -u root python3 wf.py run --config configs/military_fault_tolerate.yaml --id debug-tolerate
sudo -u root python3 wf.py run --config configs/military_consistency.yaml --id debug-consist
```

其中故障感知实验完成故障注入、等待 Pod 重建、算法检测和延迟测量。

### 循环重复调试

修改服务代码后：

```bash
sudo ./scripts/build-images.sh fire-control
./scripts/deploy.sh
sudo -u root python3 wf.py run --config configs/military_fault_detect.yaml --id debug-001
```

`deploy.sh` 默认会重启 Deployment；如不需要重启可执行 `RESTART=0 ./scripts/deploy.sh`。

### 环境适配修改

- `services/common/base.py`：Prometheus 指标名中的 `-` 替换为 `_`。原代码用 `fire-control_requests_total` 这类非法指标名，`prometheus_client` 会在启动时抛 `ValueError`，导致全部微服务崩溃。
- `services/*/Dockerfile`：增加 `ARG PIP_INDEX_URL`，默认阿里云 PyPI，可通过构建参数覆盖。
- `manifests/all-services.yaml`、`manifests/fire-control.yaml`：增加 `imagePullPolicy: IfNotPresent`。
- `manifests/services.yaml`：11 个核心 Service + `state-store-headless`；平台服务在 `platform-services.yaml`。
- `actions/k8s.py`：`k8s.check_ready` 改为轮询等待（默认 60 秒），并忽略正在删除的 Pod，避免故障注入后误判恢复。
- `wf.py`：以脚本方式运行时把 `wf` 指向 `__main__`，修复动作注册表分裂导致的 `Action xxx not found`；工作流失败时返回非 0 退出码。

### 已知限制

- `fault.inject_netem` / `fault.clear_netem` 已是真实实现：通过宿主机 `k3s crictl` 找到容器 PID，
  再 `nsenter -t <pid> -n tc qdisc ...` 注入/清理延迟丢包。因此需要 root、宿主机 `tc`/`nsenter`/
  `ip`；规则同时写入 `runs/<id>/netem.json`，支持失败后恢复清理。
- `fault.inject_network_partition` 仍通过 NetworkPolicy 实现；K3S 默认 flannel 不强制执行策略，
  隔离效果取决于 CNI。需要确定性隔离时用 netem。
- `state-store` 的 3 个副本已实现 LWW 异步复制 + 无头 Service 对等发现 + 5s 反熵，
  但仍不是线性一致：写入默认异步推送，单副本写入（`?replicate=0`）会造成可观测分叉，
  需要 `/sync` 或反熵收敛。适合做一致性实验，不适合当强一致数据库。
- `sim-bridge` 已移除 mock 模式，`/start` 直接运行真实 ROSS；如果镜像中缺少 `mil_sim`，返回 503。
  `/inject_fault` 已接入运行中的 ROSS，可注入 `lp_crash` 等故障。
- 工作流动作 `ross.start_sim` 已通过 `kubectl exec` 真正调用 sim-bridge 的 `/inject_fault`、
  `/start`、`/status`，并把 `mode=real`、`returncode`、事件数写回 `ctx`；返回码非 0 会让步骤失败。
- 单节点集群：HPA/PDB 能演示对象与策略，但没有多节点调度和节点故障转移；HPA 未做压测扩容。
- Jaeger 内存存储、Prometheus `emptyDir`：Pod 重建即清空，长跑需持久化。
- 故障感知仍是批处理（一次扫描约 10s），证据时刻早不等于结论实时产出。

## 平台服务与治理清单（2026-10-27）

在 11 个核心仿真服务之外，新增 6 个通用平台服务，服务名与镜像 `sim/<name>:latest` 一一对应：

| 服务 | 作用 | 清单 |
| --- | --- | --- |
| `api-gateway` | 统一入口、健康聚合、受限反向代理 | `platform-services.yaml` |
| `auth-service` | HMAC-SHA256 无状态令牌 | 同上 |
| `config-service` | 版本化配置中心 | 同上 |
| `notification-service` | 分级通知缓存 | 同上 |
| `platform-service` | 节点/Pod 身份、cgroup 限额、组件健康 | 同上 |
| `chaos-service` | 集群内 Pod kill / scale（ServiceAccount `sim-chaos`） | 同上 + `platform-rbac.yaml` |

配套清单：

- `platform-rbac.yaml`：chaos-service 的最小权限 Role/RoleBinding。
- `platform-policy.yaml`：LimitRange、ResourceQuota、PDB、api-gateway HPA。
- `platform-ingress.yaml`：Traefik Ingress（`sim-lab.local` -> api-gateway）。
- `prometheus.yaml`：把 Prometheus 配置纳入仓库，不再依赖手工 `/tmp` 文件。

部署与验证：

```bash
sudo ./scripts/build-images.sh      # 17 个镜像
./scripts/deploy.sh                 # 核心 + 平台 + Jaeger/Prometheus
./scripts/verify_platform.sh        # 期望 PASS=58 FAIL=0
```

更完整的对象说明、RBAC 权限边界与已知限制见 `docs/K3S-PLATFORM.md`；
逐项实测输出见 `docs/PLATFORM_AUDIT.md`。

## Prometheus 可观测性底座部署（2026-10-26）

### 用途

`actions/obs.py` 的基线采集依赖两类真实指标：

| 指标 | 来源 | 采集方式 |
| --- | --- | --- |
| CPU / 内存 | K3S 自带 metrics-server | `kubectl top pods -n sim-test` |
| 请求率（QPS） | 各服务 `/metrics` 暴露的 `*_requests_total` | Prometheus `rate(...[2m])` |

metrics-server 随 K3S 安装（`kubectl top` 直接可用）；Prometheus 需要单独部署，本节即其完整过程。

### 前置条件

- K3S 正常运行，`kubectl get nodes` 为 `Ready`。
- 17 个服务镜像（`sim/*:latest`）已按上一节构建并导入；核心与平台 Service 已 apply，集群内能用服务名互相访问。
- **Docker Hub 不可直连**（实测 `curl registry-1.docker.io` 返回 000），因此不能使用官方 `prom/prometheus` 镜像，而是从 GitHub release 下载二进制、再挂到本地已有的 `sim/orchestrator:latest` 基础层上构建。
- GitHub release 可达（`github.com` 直连正常），`buildah 1.23.1` 已安装。

### 步骤

#### 1. 下载 Prometheus 二进制

```bash
cd /tmp
if [ ! -x prometheus-2.53.0.linux-amd64/prometheus ]; then
  curl -4 -L -o prom.tgz \
    https://github.com/prometheus/prometheus/releases/download/v2.53.0/prometheus-2.53.0.linux-amd64.tar.gz
  tar xzf prom.tgz
fi
./prometheus-2.53.0.linux-amd64/prometheus --version | head -1
```

实测输出（压缩包解出 132 MB 二进制）：

```text
prometheus, version 2.53.0 (branch: HEAD, revision: 4c35b9250afefede41c5f5acd76191f90f625898)
```

#### 2. 写抓取配置 `/tmp/prometheus.yml`

抓取间隔 5s。下面是首次接入时的 11 个核心服务示例；当前仓库的
`manifests/prometheus.yaml` 已扩到 17 个（11 核心 + 6 平台），并以该 ConfigMap 为唯一配置来源：

```bash
cat > /tmp/prometheus.yml <<'EOF'
global:
  scrape_interval: 5s
  evaluation_interval: 5s
scrape_configs:
  - job_name: 'sim-services'
    metrics_path: /metrics
    static_configs:
      - targets:
        - orchestrator:8080
        - scenario-mgr:8080
        - entity-service:8080
        - comms-service:8080
        - sensor-service:8080
        - fire-control:8080
        - env-service:8080
        - log-service:8080
        - state-store:8080
        - telemetry-gw:8080
        - sim-bridge:8080
EOF
```

#### 3. 以 `sim/orchestrator:latest` 为基础层构建镜像

`buildah from` 复用本地已导入的镜像，`copy` 只追加二进制和配置，全程不联网：

```bash
set -e
cid=$(buildah from docker.io/sim/orchestrator:latest)
buildah copy $cid /tmp/prometheus-2.53.0.linux-amd64/prometheus /usr/local/bin/prometheus
buildah copy $cid /tmp/prometheus-2.53.0.linux-amd64/promtool   /usr/local/bin/promtool
buildah copy $cid /tmp/prometheus.yml /etc/prometheus/prometheus.yml
buildah config --entrypoint '["/usr/local/bin/prometheus","--config.file=/etc/prometheus/prometheus.yml","--storage.tsdb.path=/prometheus","--storage.tsdb.retention.time=2h"]' $cid
buildah commit --quiet $cid docker.io/sim/prometheus:latest
buildah rm $cid

# 导出 docker-archive（buildah 1.23 没有 load 子命令，必须走 push docker-archive）
rm -f /tmp/prom_img.tar
buildah push docker.io/sim/prometheus:latest docker-archive:/tmp/prom_img.tar
ls -lh /tmp/prom_img.tar
```

实测 `/tmp/prom_img.tar` 约 428 MB（基础层 296 MB + 二进制 132 MB）。

#### 4. 导入 containerd

```bash
k3s ctr -n k8s.io images import /tmp/prom_img.tar
k3s ctr -n k8s.io images ls -q | grep prometheus
```

应看到 `docker.io/sim/prometheus:latest`。

#### 5. 部署 Deployment + Service

```bash
cat > /tmp/prometheus.yaml <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: sim-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
    spec:
      containers:
        - name: prometheus
          image: sim/prometheus:latest
          imagePullPolicy: IfNotPresent
          command: ["/usr/local/bin/prometheus"]
          args:
            - "--config.file=/etc/prometheus/prometheus.yml"
            - "--storage.tsdb.path=/prometheus"
            - "--storage.tsdb.retention.time=2h"
          ports:
            - containerPort: 9090
          volumeMounts:
            - name: data
              mountPath: /prometheus
      volumes:
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: sim-test
spec:
  selector:
    app: prometheus
  ports:
    - port: 9090
      targetPort: 9090
EOF

kubectl apply -f /tmp/prometheus.yaml
kubectl -n sim-test rollout status deploy/prometheus --timeout=120s
```

`imagePullPolicy: IfNotPresent` 和 `command/args` **两项都不能省**，原因见下面的踩坑记录。

#### 6. 验证

```bash
# a) Pod 与健康检查
kubectl -n sim-test get pods -l app=prometheus -o wide
kubectl -n sim-test exec deploy/prometheus -- promtool check healthy

# b) 抓取目标是否全 UP（集群内 DNS 解析 prometheus:9090）
kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import json, urllib.request
d = json.loads(urllib.request.urlopen("http://prometheus:9090/api/v1/targets", timeout=10).read().decode())
act = d["data"]["activeTargets"]
print("targets", len(act), "up", sum(1 for t in act if t["health"] == "up"))
for t in act:
    if t["health"] != "up":
        print("DOWN", t["labels"].get("instance"), t.get("lastError"))
PY

# c) 请求率查询（obs.py 实际使用的表达式）
kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import json, urllib.request, urllib.parse
q = 'sum by (instance) (rate({__name__=~".*_requests_total"}[2m]))'
u = "http://prometheus:9090/api/v1/query?query=" + urllib.parse.quote(q)
d = json.loads(urllib.request.urlopen(u, timeout=10).read().decode())
for r in d["data"]["result"][:4]:
    print(r["metric"].get("instance"), round(float(r["value"][1]), 4))
PY
```

实测结果：

```text
NAME                          READY   STATUS    RESTARTS   AGE     IP           NODE
prometheus-79966c7f6b-847r9   1/1     Running   0          3h25m   10.42.0.62   ubu-22

SUCCESS:  Prometheus Server is Healthy.

targets 11 up 11
```
> 首次接入时的快照；当前 17 个服务的实测结果为 `targets 17 up 17`
> （以 `./scripts/verify_platform.sh` 为准）。

Service：`prometheus   ClusterIP   10.43.210.49   <none>   9090/TCP   app=prometheus`

请求率数值随业务流量变化（无流量时接近 0），`targets 17 up 17` 才是当前判断部署成功的硬指标。

#### 7. 集群外查看 UI / 执行 PromQL

```bash
kubectl -n sim-test port-forward svc/prometheus 9090:9090
# 浏览器打开 http://localhost:9090
#   /targets —— 查看 17 个 target 的抓取状态与 last scrape 时间
#   /graph   —— 直接跑 PromQL，例如 rate(fire_control_requests_total[2m])
```

### 与实验流程的衔接

- `actions/obs.py::collect_baseline` 在集群内查询 `http://prometheus:9090`，通过 `kubectl exec deploy/orchestrator` 执行 Pod 内 Python，不依赖本机能否路由到 ClusterIP。
- 逐服务查询每次要一次 `kubectl exec`（约 0.4 s），高频采样路径上用的是 `_prom_rates_by_instance()`：一条 `sum by (instance) (rate({__name__=~".*_requests_total"}[2m]))` 一次取回全部服务请求率，只有 1 次 exec。
- 指标名里的 `-` 在 `services/common/base.py` 中被替换成 `_`，所以实际指标是 `fire_control_requests_total` 而不是 `fire-control_requests_total`（后者会被 `prometheus_client` 判为非法并在启动时抛 `ValueError`）。
- Prometheus 不可用时 `collect_baseline` 会退化为只用 metrics-server 的 CPU/内存，请求率记 0，实验仍能跑通但特征维度缺一项。

### 踩坑记录

- **Docker Hub 不可达**：`docker.io/prom/prometheus` 拉不到。必须走"GitHub 下载二进制 + buildah 挂到 `sim/orchestrator:latest`"这条路。
- **`:latest` 默认 `imagePullPolicy: Always`**：不带 `IfNotPresent` 时 Pod 进 `ImagePullBackOff`，去 Docker Hub 拉 `sim/prometheus:latest` 必然失败。
- **`buildah config --entrypoint` 不足以让 Pod 跑对进程**：基础镜像 `sim/orchestrator:latest` 自带 `CMD python app.py`，Deployment 未指定 `command` 时会继续跑 orchestrator 的 uvicorn，`/api/v1/targets` 返回 404，日志里看到的是 orchestrator 的启动输出。必须在 Deployment 里用 `command` + `args` 显式覆盖。
- **`docker-archive` 不能覆盖已存在的 tar**：`buildah push ... docker-archive:/tmp/prom_img.tar` 在文件已存在时报错，push 前先 `rm -f`。
- **存储是 `emptyDir` + `retention 2h`**：Pod 重建即清空、只留 2 小时数据，做 48h 长跑要换 PVC（`local-path` StorageClass 已随 K3S 提供）。
- **抓取窗口**：`scrape_interval: 5s`，配套 PromQL 用 `[2m]`（24 个样本）算 `rate`，窗口过短（如 `[30s]`）在服务稀疏请求下会大量返回空值。

### 卸载 / 重建

```bash
kubectl -n sim-test delete deploy/prometheus svc/prometheus
k3s ctr -n k8s.io images rm docker.io/sim/prometheus:latest
# 改完配置重建：重复步骤 3~6 即可，Deployment 会滚动更新
```

## Jaeger 链路追踪后端部署（2026-10-27）

### 用途

`services/common/tracing.py`（纯标准库追踪 SDK）在每个微服务里生成 SERVER/CLIENT span，经 OTLP/HTTP JSON 批量推送到 Jaeger collector `:4318`；`actions/trace.py` 的 `trace.collect` / `trace.graph` / `trace.verify` 和故障感知算法（`algos/fault_detect_dl.py` 的运行态依赖图、`failed_peers`、首错时刻）都从 Jaeger Query API（`:16686`）读真实 trace。架构与实测数据详见 `docs/tracing-integration.md`。



### 前置条件

- 同 Prometheus 一节：buildah 可用、`sim/orchestrator:latest` 已导入（作基础层）、GitHub release 可达、Docker Hub 不可达。
- 17 个服务镜像须是包含 `services/common/tracing.py` 的版本，旧镜像不会上报任何 span。
  改动 `common/` 后必须用 `sudo ./scripts/build-images.sh <服务名...>` 重建并执行
  `./scripts/deploy.sh`；仓库没有热补丁脚本。

### 步骤

#### 1. 下载 Jaeger 二进制

```bash
cd /tmp
if [ ! -x jaeger-1.57.0-linux-amd64/jaeger-all-in-one ]; then
  # 已有半截包可断点续传：curl -4 -L -C - -o jaeger.tar.gz <url>
  curl -4 -L --retry 3 -o jaeger.tar.gz \
    https://github.com/jaegertracing/jaeger/releases/download/v1.57.0/jaeger-1.57.0-linux-amd64.tar.gz
  gzip -t jaeger.tar.gz   # 校验完整性，断了就 -C - 续传
  tar xzf jaeger.tar.gz
fi
./jaeger-1.57.0-linux-amd64/jaeger-all-in-one version 2>&1 | tail -1
```

实测输出：

```text
{"gitCommit":"55e991a29725468164b11be5fc4e260dc09598d6","gitVersion":"v1.57.0","buildDate":"2024-05-01T23:19:12Z"}
```

#### 2. 构建 `sim/jaeger:latest` 并导入 containerd

与 Prometheus 相同的"基础层 + 追加二进制"套路，全程不联网：

```bash
set -e
JV=1.57.0
cid=$(buildah from docker.io/sim/orchestrator:latest)
buildah copy $cid /tmp/jaeger-${JV}-linux-amd64/jaeger-all-in-one /usr/local/bin/jaeger-all-in-one
buildah config --entrypoint '["/usr/local/bin/jaeger-all-in-one"]' $cid
buildah config --env SPAN_STORAGE_TYPE=memory $cid
buildah commit --quiet $cid docker.io/sim/jaeger:latest
buildah rm $cid

rm -f /tmp/jaeger_img.tar   # docker-archive 不能覆盖已存在的 tar
buildah push docker.io/sim/jaeger:latest docker-archive:/tmp/jaeger_img.tar
k3s ctr -n k8s.io images import /tmp/jaeger_img.tar
k3s ctr -n k8s.io images ls -q | grep jaeger
```

应看到 `docker.io/sim/jaeger:latest`。

#### 3. 部署 Deployment + Service

直接用仓库里的清单：

```bash
kubectl apply -f manifests/jaeger.yaml
kubectl -n sim-test rollout status deploy/jaeger --timeout=180s
```

关键点（同 Prometheus 的教训）：

- `imagePullPolicy: IfNotPresent`（`:latest` 默认 Always 会去 Docker Hub 拉取失败）；
- `command` + `args` 显式覆盖基础镜像的 uvicorn CMD；
- 参数：`--memory.max-traces=20000`（内存存储上限）、`--collector.otlp.enabled=true`（开 OTLP 收口）、`--query.base-path=/`；
- 端口：UI `16686`、OTLP-HTTP `4318`、OTLP-gRPC `4317`、Jaeger thrift `14268`，Service 全部暴露；
- `readinessProbe` 打在 `GET / :16686`，滚动更新时能正确等待。

#### 4. 验证

```bash
# a) Pod
kubectl -n sim-test get pods -l app=jaeger -o wide

# b) 服务列表（集群内 DNS，从 orchestrator Pod 查）
kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import json, urllib.request
d = json.loads(urllib.request.urlopen("http://jaeger:16686/api/services", timeout=10).read().decode())
print("services:", d["data"])
PY

# c) OTLP 收口自检：手动造一个 span 推到 :4318，1 秒后应出现在服务列表
kubectl -n sim-test exec -i deploy/orchestrator -- python - <<'PY'
import json, time, urllib.request
now = time.time_ns()
span = {"traceId": "11111111111111111111111111111111", "spanId": "2222222222222222",
        "name": "otlp-selfcheck", "kind": 2, "startTimeUnixNano": str(now-12000000),
        "endTimeUnixNano": str(now), "status": {"code": 1}}
payload = {"resourceSpans": [{"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "otlp-selfcheck"}}]},
        "scopeSpans": [{"scope": {"name": "selfcheck"}, "spans": [span]}]}]}
req = urllib.request.Request("http://jaeger:4318/v1/traces", data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
print("OTLP_STATUS", urllib.request.urlopen(req, timeout=10).status)
time.sleep(1)
svcs = json.loads(urllib.request.urlopen("http://jaeger:16686/api/services", timeout=10).read().decode())["data"]
print("selfcheck visible:", "otlp-selfcheck" in svcs)
PY

# d) 端到端：平台自检 + 真实业务链路
./scripts/verify_platform.sh        # 期望 PASS=58 FAIL=0
python wf.py run --config configs/tracing_pipeline.yaml --id trace-check
# trace.verify 期望 ok=True，运行态依赖边覆盖率 1.0
```

实测结果：

```text
NAME                      READY   STATUS    RESTARTS   AGE   IP            NODE
jaeger-696c7bc5d9-p5ffb   1/1     Running   0          13h   10.42.0.115   ubu-22

jaeger   ClusterIP   10.43.251.211   <none>   16686/TCP,4318/TCP,4317/TCP,14268/TCP   app=jaeger

services: ['scenario-mgr', 'comms-service', 'env-service', 'platform-service',
           'fire-control', 'auth-service', 'chaos-service', 'state-store',
           'notification-service', 'config-service', 'sim-bridge', 'jaeger-all-in-one',
           'api-gateway', 'orchestrator', 'entity-service', 'sensor-service',
           'telemetry-gw', 'log-service']
```

（`jaeger-all-in-one` 是 Jaeger 自身的服务名，属正常现象。当前共 18 个服务 =
17 个业务服务 + Jaeger 自身。）

#### 5. 集群外查看 UI

```bash
kubectl -n sim-test port-forward svc/jaeger 16686:16686
# 浏览器打开 http://localhost:16686
# Service 选 orchestrator → Find Traces，可看到一次 /trigger 的完整调用树；
# span 的 peer.service 标签直接指认被调用的下游，是根因定位的关键证据。
```

### 服务侧配置（环境变量）

`services/common/tracing.py` 全部有默认值，默认即可对接本节的 Jaeger：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SERVICE_NAME` | `unknown` | span 的 `service.name`（base.py 从部署名注入） |
| `OTLP_ENDPOINT` | `http://jaeger:4318/v1/traces` | 导出地址 |
| `TRACING_ENABLED` | `1` | 设 `0` 关闭追踪（中间件直通） |
| `TRACE_SAMPLE_RATE` | `1.0` | 全量采样；48h 长跑建议调低 + 错误 span 全留 |
| `TRACE_MAX_QUEUE` | `4096` | 有界导出队列，满则丢 span 只计数不阻塞业务 |

每个服务自带 `GET /tracez` 返回导出统计（已导出/队列积压/失败数），排查上报问题先看它。

### 踩坑记录

- Prometheus 一节的坑全部适用：`:latest` 必须 `IfNotPresent`；`command`+`args` 必须显式覆盖基础镜像 CMD；`docker-archive` 前先 `rm -f`；GitHub 大包下载易断，用 `curl -C -` 续传 + `gzip -t` 校验（实测 jaeger.tar.gz 曾下到一半截断）。
- **Jaeger 1.57 的 Query API 结构**：`serviceName` 在 `trace.processes[processID].serviceName`，**不在** `span.process`（按 OTel 习惯读 `span.process` 会得到 `None`，所有 span 归到 `?`）。`actions/trace.py` 已按 1.57 结构解析；升级 Jaeger 大版本时注意这里。
- **`/metrics` 噪声**：Prometheus 每 5s 抓一次各服务，每次都会生成一条单 span 的"根 trace"，把业务链路挤出查询窗口。`tracing.py` 的中间件已跳过 `/metrics` 与 `/tracez` 等路径，新增抓取类路径时记得同步进 `_SKIP_PATHS`。
- **多服务查询要去重**：`/api/traces?service=A&service=B` 返回的 trace 有重叠，`actions/trace.py` 按 `traceID` 去重，否则 span 数虚高约 17 倍。
- **统计调用边要两遍扫描**：先登记全部 `spanID -> serviceName`，再扫父子引用，否则子 span 先出现时丢边。
- **内存存储**：`SPAN_STORAGE_TYPE=memory` + `--memory.max-traces=20000`，Pod 重启即清空、上限 2 万条。想清空 trace 直接 `kubectl -n sim-test rollout restart deploy/jaeger`；48h 长跑需换 badger/Cassandra 存储。
- **OTLP JSON 收口**：Jaeger 1.57 的 `/v1/traces` 接受 `application/json` 的 OTLP JSON（无需 protobuf），这正是纯标准库 SDK 能零依赖工作的前提。

### 卸载 / 重建

```bash
kubectl -n sim-test delete deploy/jaeger svc/jaeger
k3s ctr -n k8s.io images rm docker.io/sim/jaeger:latest
# 重新部署：kubectl apply -f manifests/jaeger.yaml（镜像已存在则跳过步骤 2）
```

## ROSS 仿真引擎（sim-bridge）

### 是否需要单独镜像

**不需要。** ROSS 是一个 C 语言离散事件仿真库，`ross-sim` 编译出的是可执行文件 `mil_sim`，它们都不是对外提供 HTTP 接口的微服务，因此：

- 不需要为 ROSS 建单独的 Deployment/Service/镜像；
- 已通过 **多阶段构建** 把 ROSS + `mil_sim` 编译进 `sim/sim-bridge:latest`；
- 运行 `mil_sim` 的 MPI 运行库也已装进同一个镜像；
- sim-bridge 通过子进程启动 `mil_sim`，再通过 HTTP 暴露启动/状态查询接口。

这样 K8S 中仍然是 17 个服务镜像，sim-bridge 是唯一与 ROSS 相关的镜像。

### 镜像构建方式

`services/sim-bridge/Dockerfile` 分两个阶段：

1. `ross-builder`：安装 `build-essential cmake mpich libmpich-dev`，下载 ROSS `v8.1.1` 源码，安装到 `/opt/ross`，再编译 `ross-sim` 得到 `/src/ross-sim/build/mil_sim`。
2. 运行阶段：只保留 Python 服务、`mil_sim` 和 `libmpich12`，不包含编译器和 ROSS 源码。

```bash
sudo ./scripts/build-images.sh sim-bridge
./scripts/deploy.sh
```

可用构建参数覆盖 ROSS 版本/下载地址：

```bash
buildah bud \
  --build-arg ROSS_VERSION=8.1.1 \
  --build-arg ROSS_URL=https://codeload.github.com/ross-org/ROSS/tar.gz/refs/tags/v8.1.1 \
  -t docker.io/sim/sim-bridge:latest -f services/sim-bridge/Dockerfile .
```

说明：

- 当前环境访问 GitHub 直链不稳定，Dockerfile 默认使用 `codeload.github.com`。
- buildah 解压 ROSS tar 包时可能报 `Cannot change mode ... Operation not permitted`，文件实际已解出，Dockerfile 中已忽略该错误并用 `test -f CMakeLists.txt` 校验。
- 容器内单进程运行 MPI 程序时 UCX 可能选错网卡，镜像中已设置 `UCX_TLS=sm,self`。

### 在 K3S 中验证真实 ROSS

```bash
./scripts/test-ross.sh
```

脚本会通过 `orchestrator` Pod 调用 sim-bridge：

```bash
curl -X POST 'http://sim-bridge:8080/start?end_time=20&units=10'
curl http://sim-bridge:8080/status
curl http://sim-bridge:8080/ross/log
```

`/start` 不再接受 `use_real_ross` 参数，调用即运行真实 ROSS。已验证结果：

- `/start` 返回 `mode: real`
- `/status` 返回 `running: false`、`returncode: 0`、`lp_count: 14`、`events_processed: 691`
- 日志末尾出现 `=== Simulation Complete (end time 20.0) ===`

如果镜像中没有 `mil_sim`，`/start` 返回 `503`，不会退回到模拟数据。

### 本地编译 ross-sim

```bash
sudo apt-get install -y build-essential cmake mpich libmpich-dev
# 先安装 ROSS 到 /opt/ross（步骤见 Dockerfile 的 ross-builder 阶段）
cd ross-sim
ROSS_DIR=/opt/ross ./scripts/build.sh
UCX_TLS=sm,self ./scripts/run_scenario.sh
```

### 为适配当前 ROSS 版本做的修改

原 `ross-sim` 代码按旧版 ROSS API 编写，在 ROSS 8.1.1 下无法编译/运行，已修改：

- `tw_lptype` 从 6 个字段补全为 8 个字段（`init/pre_run/event/revent/commit/final/map/state_sz`）。
- 增加 `mil_map()`；ROSS 发送事件时会调用 `src_lp->type->map`，为 `NULL` 会段错误。
- 移除已不存在的 `tw_event_type()`，改为在 `mil_message` 中携带事件类型。
- `tw_nlp` 改为 `g_tw_nlp`，移除 `tw_set_lps_per_pe()`。
- 特殊 LP（火控/传感器/通信/地形）改为动态分配在单位 LP 之后，不再写死 20/21/22/23。
- `--nlp=24` 改为 `--units=N`；`--synch=3`（乐观同步）改为 `--synch=1`（顺序同步），因为当前反向计算/回滚处理尚未实现。
- `CMakeLists.txt` 增加 `include/` 路径和 MPI 链接，`scripts/*.sh` 去掉 macOS 硬编码路径。

## 环境准备
```bash
python3 -m pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
kubectl get nodes  # 确认集群可用
```

## 部署微服务

推荐直接执行 `./scripts/deploy.sh`，等价的手工命令：

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/all-services.yaml
kubectl apply -f manifests/fire-control.yaml
kubectl apply -f manifests/services.yaml
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
