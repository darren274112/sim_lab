#!/usr/bin/env bash
# 平台自检：集群、部署、服务端点、跨服务调用、追踪、状态副本、平台治理对象
#
# 用法:
#   ./scripts/verify_platform.sh
#   NAMESPACE=sim-test ./scripts/verify_platform.sh
#
# 只做只读检查 + 一次良性业务触发，不会删除 Pod 或注入故障。
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

NS="${NAMESPACE:-sim-test}"
PASS=0
FAIL=0

ok()   { echo "  [PASS] $*"; PASS=$((PASS + 1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL + 1)); }
info() { echo "  [INFO] $*"; }

section() { echo; echo "== $* =="; }

if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: 未找到 kubectl" >&2
  exit 2
fi
if ! kubectl get nodes >/dev/null 2>&1; then
  echo "ERROR: 无法访问集群" >&2
  exit 2
fi

section "集群"
kubectl get nodes -o wide
if kubectl get nodes --no-headers | grep -q ' Ready'; then
  ok "节点 Ready"
else
  bad "没有 Ready 节点"
fi
info "$(kubectl version 2>/dev/null | tr '\n' ' ' | cut -c1-160)"

section "命名空间与 Pod"
kubectl -n "$NS" get pods -o wide
NOT_READY=$(kubectl -n "$NS" get pods --no-headers 2>/dev/null \
  | awk '$3 != "Running" && $3 != "Completed" {print $1}')
if [[ -z "$NOT_READY" ]]; then
  ok "全部 Pod Running/Completed"
else
  bad "存在未运行 Pod: $(echo "$NOT_READY" | tr '\n' ' ')"
fi

section "Deployment"
for d in $(kubectl -n "$NS" get deployments -o name); do
  if kubectl -n "$NS" rollout status "$d" --timeout=10s >/dev/null 2>&1; then
    ok "$d Ready"
  else
    bad "$d 未就绪"
  fi
done

section "Service 端点"
EXPECTED=(orchestrator scenario-mgr entity-service comms-service sensor-service
  fire-control env-service log-service state-store telemetry-gw sim-bridge
  api-gateway auth-service config-service notification-service platform-service chaos-service)
for svc in "${EXPECTED[@]}"; do
  if ! kubectl -n "$NS" get svc "$svc" >/dev/null 2>&1; then
    bad "缺少 Service: $svc"
    continue
  fi
  eps=$(kubectl -n "$NS" get endpoints "$svc" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)
  if [[ -n "$eps" ]]; then
    ok "Service $svc -> $(echo "$eps" | wc -w) endpoint(s)"
  else
    bad "Service $svc 没有就绪端点"
  fi
done

if kubectl -n "$NS" get svc state-store-headless >/dev/null 2>&1; then
  eps=$(kubectl -n "$NS" get endpoints state-store-headless -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)
  if [[ $(echo "$eps" | wc -w) -ge 2 ]]; then
    ok "无头 Service state-store-headless -> $(echo "$eps" | wc -w) 个副本"
  else
    bad "state-store-headless 副本不足: '$eps'"
  fi
else
  bad "缺少 state-store-headless"
fi

section "集群内 HTTP 检查"
if ! kubectl -n "$NS" get deploy orchestrator >/dev/null 2>&1; then
  bad "orchestrator 未部署，跳过 HTTP 检查"
else
  HTTP_OUT=$(kubectl -n "$NS" exec -i deploy/orchestrator -- python - <<'PY' 2>&1
import json, socket, urllib.request

def get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode()

report = {"checks": {}, "errors": []}
for svc in ["orchestrator", "fire-control", "state-store", "api-gateway",
            "auth-service", "config-service", "notification-service",
            "platform-service", "chaos-service"]:
    try:
        st, _ = get("http://%s:8080/healthz" % svc, 3)
        report["checks"][svc] = st
    except Exception as e:
        report["checks"][svc] = "ERR:%s" % type(e).__name__
        report["errors"].append("%s: %s" % (svc, e))

# 真实业务触发（会产生跨服务 trace）
try:
    req = urllib.request.Request("http://127.0.0.1:8080/trigger?scenario=battalion_attack",
                                 data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        body = json.loads(r.read().decode())
    report["trigger"] = {
        "status": ((body.get("engage") or {}).get("mission") or {}).get("status"),
        "trace_id": body.get("trace_id"),
        "health_ok": sum(1 for v in (body.get("health") or {}).values() if v == "ok"),
    }
except Exception as e:
    report["trigger"] = {"error": "%s: %s" % (type(e).__name__, e)}

# 状态副本发现与一致性接口
try:
    infos = socket.getaddrinfo("state-store-headless", 8080, type=socket.SOCK_STREAM)
    ips = sorted({i[4][0] for i in infos})
    report["state_replicas"] = ips
    consistent = []
    for ip in ips:
        st, raw = get("http://%s:8080/consistency" % ip, 4)
        consistent.append(json.loads(raw).get("consistent"))
    report["state_consistent"] = consistent
except Exception as e:
    report["state_replicas"] = "ERR:%s" % e

print(json.dumps(report))
PY
)
  HTTP_JSON=$(echo "$HTTP_OUT" | tail -n1)
  info "$HTTP_JSON"
  for svc in orchestrator fire-control state-store api-gateway auth-service \
             config-service notification-service platform-service chaos-service; do
    st=$(echo "$HTTP_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('checks',{}).get('$svc'))" 2>/dev/null)
    if [[ "$st" == "200" ]]; then
      ok "$svc /healthz = 200"
    else
      bad "$svc /healthz = ${st:-unparsable}"
    fi
  done
  TSTATUS=$(echo "$HTTP_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('trigger') or {}).get('status'))" 2>/dev/null)
  if [[ "$TSTATUS" == "engaged" ]]; then
    ok "POST /trigger -> engaged（跨服务链路可用）"
  else
    bad "POST /trigger 异常: $TSTATUS"
  fi
  SCONS=$(echo "$HTTP_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); v=d.get('state_consistent') or []; print(','.join(str(x) for x in v))" 2>/dev/null)
  if [[ "$SCONS" == "True,True,True" ]]; then
    ok "state-store 各副本一致"
  else
    info "state-store consistency=$SCONS（刚启动或正在同步时可能暂为 False）"
  fi
fi

section "追踪与指标"
if kubectl -n "$NS" get svc jaeger >/dev/null 2>&1; then
  JAEGER=$(kubectl -n "$NS" exec -i deploy/orchestrator -- python - <<'PY' 2>&1
import json, urllib.request
try:
    data = json.loads(urllib.request.urlopen("http://jaeger:16686/api/services", timeout=5).read())
    print(json.dumps({"ok": True, "services": len(data.get("data") or [])}))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
PY
)
  if echo "$JAEGER" | tail -n1 | grep -Eq '"ok": ?true'; then
    ok "Jaeger API 可用: $(echo "$JAEGER" | tail -n1)"
  else
    bad "Jaeger API 异常: $(echo "$JAEGER" | tail -n1)"
  fi
else
  info "Jaeger 未部署，跳过"
fi

if kubectl -n "$NS" get svc prometheus >/dev/null 2>&1; then
  PROM=$(kubectl -n "$NS" exec -i deploy/orchestrator -- python - <<'PY' 2>&1
import json, urllib.request
try:
    data = json.loads(urllib.request.urlopen("http://prometheus:9090/api/v1/targets", timeout=5).read())
    targets = (data.get("data") or {}).get("activeTargets") or []
    up = [t for t in targets if t.get("health") == "up"]
    print(json.dumps({"ok": True, "up": len(up), "total": len(targets)}))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
PY
)
  if echo "$PROM" | tail -n1 | grep -Eq '"ok": ?true'; then
    ok "Prometheus targets: $(echo "$PROM" | tail -n1)"
  else
    bad "Prometheus 异常: $(echo "$PROM" | tail -n1)"
  fi
else
  info "Prometheus 未部署，跳过"
fi

section "K3S 平台治理对象"
for kind in hpa pdb resourcequota limitrange ingress; do
  items=$(kubectl -n "$NS" get "$kind" --no-headers 2>/dev/null | wc -l)
  if [[ "$items" -gt 0 ]]; then
    ok "$kind: $items 个"
  else
    info "$kind: 无"
  fi
done

section "chaos-service RBAC 自检"
CHAOS=$(kubectl -n "$NS" exec -i deploy/orchestrator -- python - <<'PY' 2>&1
import json, urllib.request
try:
    with urllib.request.urlopen("http://chaos-service:8080/chaos/self-check", timeout=6) as r:
        print(r.read().decode())
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
PY
)
if echo "$CHAOS" | tail -n1 | grep -Eq '"ok": ?true'; then
  ok "chaos-service 可读取 Pod（RBAC 生效）"
else
  bad "chaos-service RBAC 自检失败: $(echo "$CHAOS" | tail -n1)"
fi

echo
echo "========== 结果: PASS=$PASS FAIL=$FAIL =========="
[[ "$FAIL" -eq 0 ]]
