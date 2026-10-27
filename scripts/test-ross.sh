#!/usr/bin/env bash
# 在 K3S 内验证 sim-bridge 能否真正运行 ROSS（mil_sim）
set -euo pipefail

NAMESPACE="${NAMESPACE:-sim-test}"
END_TIME="${END_TIME:-20}"
UNITS="${UNITS:-10}"

kubectl -n "$NAMESPACE" exec -i deploy/orchestrator -- \
  python - "$END_TIME" "$UNITS" <<'PY'
import json
import sys
import time
import urllib.request

end_time = sys.argv[1]
units = sys.argv[2]
base = "http://sim-bridge:8080"


def call(path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


root = call("/")
print("real_ross_available:", root["real_ross_available"])
if not root["real_ross_available"]:
    raise SystemExit("镜像中没有 mil_sim，请重新构建 sim-bridge 镜像")

print("start:", call(f"/start?end_time={end_time}&units={units}", "POST"))

status = {}
for _ in range(60):
    status = call("/status")
    if not status["running"]:
        break
    time.sleep(0.5)

print("status:", json.dumps(status, ensure_ascii=False, indent=2))
if status.get("mode") != "real" or status.get("returncode") != 0:
    raise SystemExit("ROSS 运行失败")
print("OK: real ROSS simulation finished")
PY
