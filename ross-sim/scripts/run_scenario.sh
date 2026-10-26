#!/bin/bash
# 运行营级进攻想定（单进程顺序仿真）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${ROOT_DIR}/build/mil_sim"

if [[ ! -x "${BIN}" ]]; then
  echo "未找到 ${BIN}，请先执行 scripts/build.sh" >&2
  exit 1
fi

# UCX_TLS=self 可避免容器/受限网络环境下 UCX 选错网卡
UCX_TLS="${UCX_TLS:-sm,self}" exec "${BIN}" \
  --synch=1 \
  --end="${END:-100.0}" \
  --units="${UNITS:-20}" \
  "$@"
