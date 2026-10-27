"""通用 API 网关：统一入口、令牌签发、健康聚合与受限反向代理。

所有下游调用都继承当前 trace，因此经网关发起的请求在 Jaeger 里能看到
gateway -> downstream 的真实调用边。
"""
from common.base import create_app, REQUEST_COUNT
from common import tracing
import os
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "api-gateway")
START = time.time()

# 只允许代理到已知服务，避免把网关变成任意 SSRF 入口
ALLOWED = [
    "orchestrator", "scenario-mgr", "entity-service", "comms-service",
    "sensor-service", "fire-control", "env-service", "log-service",
    "state-store", "telemetry-gw", "sim-bridge",
    "auth-service", "config-service", "notification-service",
    "platform-service", "chaos-service",
]
CORE_DEPS = [
    "orchestrator", "scenario-mgr", "entity-service", "comms-service",
    "sensor-service", "fire-control", "env-service", "log-service",
    "state-store", "telemetry-gw", "sim-bridge",
]

_proxy_calls = 0


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "api_gateway",
            "uptime_s": round(time.time() - START, 1),
            "proxy_calls": _proxy_calls, "routes": ALLOWED}


@app.get("/gateway/routes")
def routes():
    REQUEST_COUNT.labels(method="GET", endpoint="/gateway/routes").inc()
    return {"routes": [
        {"path": "/gateway/proxy", "target": "<service>", "auth": "optional"},
        {"path": "/gateway/health", "target": "all core services", "auth": "none"},
        {"path": "/gateway/token", "target": "auth-service", "auth": "none"},
    ], "allowed_services": ALLOWED}


@app.post("/gateway/token")
def token(body: dict):
    """转发令牌签发到 auth-service（真实服务间调用）。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/gateway/token").inc()
    status, res = tracing.traced_json_request(
        "POST", "http://auth-service:8080/auth/token", body=body,
        span_name="POST auth-service/auth/token")
    return {"upstream_status": status, "auth": res}


@app.get("/gateway/health")
def health():
    """并发/顺序聚合核心服务健康状态。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/gateway/health").inc()
    deps = {}
    for svc in CORE_DEPS:
        status, body = tracing.traced_json_request(
            "GET", f"http://{svc}:8080/healthz", timeout=2.0,
            span_name=f"GET {svc}/healthz")
        deps[svc] = body.get("status") if status == 200 and isinstance(body, dict) else f"error:{status}"
    ok = sum(1 for v in deps.values() if v == "ok")
    return {"status": "ok" if ok == len(deps) else "degraded",
            "healthy": ok, "total": len(deps), "deps": deps}


@app.post("/gateway/proxy")
def proxy(body: dict):
    """受限反向代理：body = {service, path?, method?, body?}。

    例：{"service": "sensor-service", "path": "/tracks"}
    """
    REQUEST_COUNT.labels(method="POST", endpoint="/gateway/proxy").inc()
    global _proxy_calls
    service = str(body.get("service") or "")
    path = str(body.get("path") or "/")
    method = str(body.get("method") or "GET").upper()
    if service not in ALLOWED:
        return {"ok": False, "error": f"service not allowed: {service}"}
    if not path.startswith("/"):
        return {"ok": False, "error": "path 必须以 / 开头"}
    if method not in ("GET", "POST", "PUT", "DELETE"):
        return {"ok": False, "error": f"method not allowed: {method}"}

    _proxy_calls += 1
    url = f"http://{service}:8080{path}"
    status, res = tracing.traced_json_request(
        method, url, body=body.get("body"),
        span_name=f"{method} {service}{path}")
    return {"ok": status != 0, "upstream": service, "status": status, "response": res}
