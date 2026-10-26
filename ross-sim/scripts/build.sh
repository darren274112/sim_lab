#!/bin/bash
# 在 Linux 上编译 ross-sim（需要先安装 ROSS，默认 /opt/ross）
# 依赖: sudo apt-get install -y build-essential cmake mpich libmpich-dev
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/build}"
ROSS_DIR="${ROSS_DIR:-/opt/ross}"

cmake -S "${ROOT_DIR}" -B "${BUILD_DIR}" -DROSS_DIR="${ROSS_DIR}"
cmake --build "${BUILD_DIR}" -j"$(nproc)"
echo "mil_sim: ${BUILD_DIR}/mil_sim"
