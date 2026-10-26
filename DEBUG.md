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

`kubectl apply --dry-run=client` 校验了 `sim-test` 命名空间及 11 个微服务清单，语法和 API 版本兼容当前 K3S。

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
sudo ./scripts/build-images.sh               # 构建全部 11 个服务
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

应看到 11 个镜像：`orchestrator`、`scenario-mgr`、`entity-service`、`comms-service`、`sensor-service`、`fire-control`、`env-service`、`log-service`、`state-store`、`telemetry-gw`、`sim-bridge`。

### 部署

```bash
./scripts/deploy.sh
```

脚本会依次完成：

1. apply `namespace.yaml`、`all-services.yaml`、`fire-control.yaml`、`services.yaml`
2. 重启全部 Deployment，确保重新构建的 `:latest` 镜像生效
3. 等待滚动更新完成、旧 Pod 退出、全部 Pod Ready
4. 输出 Pod 和 Service 列表

如需手工部署：

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/all-services.yaml
kubectl apply -f manifests/fire-control.yaml
kubectl apply -f manifests/services.yaml
```

说明：

- 新增 `manifests/services.yaml`，为 10 个微服务提供 ClusterIP Service（`fire-control` 的 Service 在 `fire-control.yaml`），`orchestrator` 可通过服务名访问下游。
- 所有 Deployment 显式设置 `imagePullPolicy: IfNotPresent`，否则 `:latest` 默认 `Always`，会去 Docker Hub 拉取本地导入的 `sim/*` 镜像并失败。
- `state-store` 保持 3 副本，因此正常运行时应看到 13 个 Pod。

### 验证

```bash
kubectl -n sim-test get deploy
kubectl -n sim-test get pods -o wide
```

结果：11 个 Deployment 全部 `Available`，13 个 Pod 全部 `Running`。

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
- `manifests/services.yaml`：新增 10 个内部 Service。
- `actions/k8s.py`：`k8s.check_ready` 改为轮询等待（默认 60 秒），并忽略正在删除的 Pod，避免故障注入后误判恢复。
- `wf.py`：以脚本方式运行时把 `wf` 指向 `__main__`，修复动作注册表分裂导致的 `Action xxx not found`；工作流失败时返回非 0 退出码。

### 已知限制

- `fault.inject_netem` / `fault.clear_netem` 目前是预留接口，只打印日志，不会真正修改网络。
- `state-store` 的 3 个副本使用各自进程内存，状态并不共享，做一致性实验时需要替换为共享存储或实现副本同步。
- `sim-bridge` 默认使用 mock 模式；真实 ROSS 引擎需要额外构建并挂载到镜像中。

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
