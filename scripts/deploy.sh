#!/usr/bin/env bash
# 部署仿真微服务到 K3S 并等待就绪
#
# 用法:
#   ./scripts/deploy.sh
#   NAMESPACE=sim-test ./scripts/deploy.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

NAMESPACE="${NAMESPACE:-sim-test}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-240s}"
# 设为 0 可跳过 Jaeger / Prometheus（例如本地未导入这两个镜像时）
OBSERVABILITY="${OBSERVABILITY:-1}"

SERVICES=(
  orchestrator scenario-mgr entity-service comms-service sensor-service
  fire-control env-service log-service state-store telemetry-gw sim-bridge
  api-gateway auth-service config-service notification-service platform-service chaos-service eval-service
)

if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: 未找到 kubectl" >&2
  exit 1
fi

if ! kubectl get nodes >/dev/null 2>&1; then
  echo "ERROR: 无法访问 K3S 集群，请检查 kubeconfig 或 systemctl status k3s" >&2
  exit 1
fi

# 确认镜像已经导入 containerd，避免部署后一直 ImagePullBackOff
if command -v k3s >/dev/null 2>&1; then
  if [[ $EUID -eq 0 ]]; then
    K3S_CTR=(k3s ctr)
  else
    K3S_CTR=(sudo k3s ctr)
  fi
  mapfile -t local_images < <("${K3S_CTR[@]}" -n k8s.io images ls -q)
  missing=0
  for svc in "${SERVICES[@]}"; do
    if ! printf '%s\n' "${local_images[@]}" | grep -q "docker.io/sim/${svc}:latest"; then
      echo "缺少镜像: sim/${svc}:latest"
      missing=1
    fi
  done
  if [[ "$missing" -eq 1 ]]; then
    echo "ERROR: 请先执行 sudo ./scripts/build-images.sh 构建并导入镜像" >&2
    exit 1
  fi
fi

echo "==> 部署命名空间与核心微服务"
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/all-services.yaml
kubectl apply -f manifests/fire-control.yaml
kubectl apply -f manifests/services.yaml

echo "==> 部署平台服务（RBAC / 服务 / 治理策略 / Ingress）"
kubectl apply -f manifests/platform-rbac.yaml
kubectl apply -f manifests/platform-services.yaml
kubectl apply -f manifests/platform-policy.yaml
kubectl apply -f manifests/platform-ingress.yaml

if [[ "$OBSERVABILITY" == "1" ]]; then
  echo "==> 部署可观测性组件（Jaeger / Prometheus）"
  [[ -f manifests/jaeger.yaml ]] && kubectl apply -f manifests/jaeger.yaml
  [[ -f manifests/prometheus.yaml ]] && kubectl apply -f manifests/prometheus.yaml
fi

# 镜像 tag 固定为 latest，重新构建后需要重启 Deployment 才会使用新镜像
if [[ "${RESTART:-1}" == "1" ]]; then
  echo "==> 重启 Deployment 以加载最新镜像"
  kubectl -n "$NAMESPACE" get deployments -o name | xargs -r kubectl -n "$NAMESPACE" rollout restart
fi

echo "==> 等待 Deployment 滚动更新完成（超时 ${WAIT_TIMEOUT}）"
for deploy in $(kubectl -n "$NAMESPACE" get deployments -o name); do
  kubectl -n "$NAMESPACE" rollout status "$deploy" --timeout="$WAIT_TIMEOUT"
done

echo "==> 等待旧 Pod 退出"
for _ in $(seq 1 60); do
  if ! kubectl -n "$NAMESPACE" get pods --no-headers 2>/dev/null | awk '{print $3}' | grep -qx 'Terminating'; then
    break
  fi
  sleep 2
done

kubectl -n "$NAMESPACE" wait --for=condition=Ready pod --all --timeout="$WAIT_TIMEOUT"

echo
kubectl -n "$NAMESPACE" get pods -o wide
echo
kubectl -n "$NAMESPACE" get svc
