#!/usr/bin/env bash
# 构建仿真微服务镜像并导入 K3S（containerd）
#
# 用法:
#   sudo ./scripts/build-images.sh                  # 构建全部服务（11 核心 + 6 平台）
#   sudo ./scripts/build-images.sh fire-control     # 只构建指定服务
#   IMAGE_TAG=dev sudo ./scripts/build-images.sh    # 自定义镜像 tag
#   IMPORT=0 sudo ./scripts/build-images.sh         # 只构建，不导入 K3S
#
# 依赖: buildah（推荐）或 docker；导入 K3S 需要 k3s 命令及 root 权限。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVICES=(
  orchestrator
  scenario-mgr
  entity-service
  comms-service
  sensor-service
  fire-control
  env-service
  log-service
  state-store
  telemetry-gw
  sim-bridge
  # 通用平台服务
  api-gateway
  auth-service
  config-service
  notification-service
  platform-service
  chaos-service
  eval-service
)

IMAGE_PREFIX="${IMAGE_PREFIX:-sim}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
# 构建时使用完整仓库名，避免 buildah 自动加 localhost/ 前缀后与 K8S 的
# sim/<service>:<tag>（containerd 会归一化为 docker.io/sim/...）对不上
IMAGE_REPO="${IMAGE_REPO:-docker.io/${IMAGE_PREFIX}}"
# 当前环境无法直连 files.pythonhosted.org，默认走阿里云 PyPI 镜像
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
BUILDER="${BUILDER:-auto}"
IMPORT="${IMPORT:-auto}"
K3S_IMAGE_NAMESPACE="${K3S_IMAGE_NAMESPACE:-k8s.io}"

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGETS=("$@")
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=("${SERVICES[@]}")
fi

for svc in "${TARGETS[@]}"; do
  if [[ ! -f "services/${svc}/Dockerfile" ]]; then
    echo "ERROR: services/${svc}/Dockerfile 不存在" >&2
    echo "可用服务: ${SERVICES[*]}" >&2
    exit 1
  fi
done

if [[ "$BUILDER" == "auto" ]]; then
  if command -v buildah >/dev/null 2>&1; then
    BUILDER=buildah
  elif command -v docker >/dev/null 2>&1; then
    BUILDER=docker
  else
    echo "ERROR: 未找到 buildah 或 docker，请先安装其中一个" >&2
    echo "  Ubuntu: sudo apt-get install -y buildah" >&2
    exit 1
  fi
fi

if [[ "$BUILDER" != "buildah" && "$BUILDER" != "docker" ]]; then
  echo "ERROR: BUILDER 只能是 auto、buildah 或 docker" >&2
  exit 1
fi

if ! command -v "$BUILDER" >/dev/null 2>&1; then
  echo "ERROR: 未找到构建工具 $BUILDER" >&2
  exit 1
fi

K3S_CTR=()
if [[ "$IMPORT" == "auto" ]]; then
  if command -v k3s >/dev/null 2>&1; then
    IMPORT=1
  else
    IMPORT=0
  fi
fi

if [[ "$IMPORT" == "1" ]]; then
  if ! command -v k3s >/dev/null 2>&1; then
    echo "ERROR: 未找到 k3s，无法导入镜像" >&2
    exit 1
  fi
  if [[ $EUID -eq 0 ]]; then
    K3S_CTR=(k3s ctr)
  else
    K3S_CTR=(sudo k3s ctr)
  fi
  # 确认 containerd 可用
  "${K3S_CTR[@]}" version >/dev/null
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "构建工具 : $BUILDER"
echo "镜像仓库 : ${IMAGE_REPO}/<service>:${IMAGE_TAG}"
echo "PyPI 镜像: $PIP_INDEX_URL"
echo "导入 K3S : $([[ "$IMPORT" == "1" ]] && echo "是 (${K3S_IMAGE_NAMESPACE})" || echo "否")"
echo "待构建   : ${TARGETS[*]}"
echo

total=${#TARGETS[@]}
idx=0

for svc in "${TARGETS[@]}"; do
  idx=$((idx + 1))
  image="${IMAGE_REPO}/${svc}:${IMAGE_TAG}"
  dockerfile="services/${svc}/Dockerfile"
  archive="${TMP_DIR}/${svc}.tar"

  echo "==> [${idx}/${total}] 构建 ${image}"
  start_ts=$(date +%s)

  if [[ "$BUILDER" == "buildah" ]]; then
    buildah bud \
      --layers \
      --format docker \
      --network host \
      --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
      -t "$image" \
      -f "$dockerfile" \
      "$ROOT_DIR"
    buildah push "$image" "docker-archive:${archive}"
  else
    docker build \
      --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
      -t "$image" \
      -f "$dockerfile" \
      "$ROOT_DIR"
    docker save -o "$archive" "$image"
  fi

  if [[ "$IMPORT" == "1" ]]; then
    echo "==> [${idx}/${total}] 导入 K3S: ${image}"
    "${K3S_CTR[@]}" -n "$K3S_IMAGE_NAMESPACE" images import "$archive"
  fi

  end_ts=$(date +%s)
  echo "==> [${idx}/${total}] 完成 ${image} ($((end_ts - start_ts))s)"
  echo
done

echo "全部完成: ${#TARGETS[@]} 个镜像"
if [[ "$IMPORT" == "1" ]]; then
  echo "K3S 本地镜像:"
  "${K3S_CTR[@]}" -n "$K3S_IMAGE_NAMESPACE" images ls -q | grep "${IMAGE_REPO}/" | sort || true
else
  echo "镜像尚未导入 K3S，可执行: sudo k3s ctr images import <tar>"
fi
