"""通用平台服务：暴露 K3S 运行环境信息并聚合平台组件状态。

它把"平台能力"变成可查询的服务：节点/Pod 身份、cgroup 资源限制、
平台组件健康、以及当前集群启用的 K3S 平台特性清单。
"""
from common.base import create_app, REQUEST_COUNT
from common import tracing
import os
import socket
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "platform-service")
START = time.time()

NODE_NAME = os.getenv("NODE_NAME", "unknown")
POD_NAME = os.getenv("POD_NAME", socket.gethostname())
NAMESPACE = os.getenv("NAMESPACE", "sim-test")
VERSION = os.getenv("PLATFORM_VERSION", "1.0.0")

PLATFORM_DEPS = ["api-gateway", "auth-service", "config-service", "notification-service"]


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _cgroup_limits():
    """兼容 cgroup v1/v2 的容器资源限制读取。"""
    mem = _read("/sys/fs/cgroup/memory.max") or _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    cpu = _read("/sys/fs/cgroup/cpu.max") or _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    try:
        mem_bytes = int(mem) if mem and mem != "max" else None
    except ValueError:
        mem_bytes = None
    cpu_quota = None
    if cpu:
        parts = cpu.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                cpu_quota = round(int(parts[0]) / max(1, int(parts[1])), 3)
            except ValueError:
                cpu_quota = None
        elif cpu.isdigit() and int(cpu) > 0:
            cpu_quota = round(int(cpu) / 100000.0, 3)
    return {"memory_mb": round(mem_bytes / 1024 / 1024, 1) if mem_bytes else None,
            "cpu_cores": cpu_quota}


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "platform",
            "node": NODE_NAME, "pod": POD_NAME, "namespace": NAMESPACE,
            "version": VERSION, "uptime_s": round(time.time() - START, 1)}


@app.get("/platform/info")
def info():
    REQUEST_COUNT.labels(method="GET", endpoint="/platform/info").inc()
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "identity": {
            "node": NODE_NAME,
            "pod": POD_NAME,
            "namespace": NAMESPACE,
            "hostname": socket.gethostname(),
            "ip": socket.gethostbyname(socket.gethostname()),
        },
        "cgroup": _cgroup_limits(),
        "kernel": os.uname().release,
        "python": os.sys.version.split()[0],
        "uptime_s": round(time.time() - START, 1),
    }


@app.get("/platform/capabilities")
def capabilities():
    REQUEST_COUNT.labels(method="GET", endpoint="/platform/capabilities").inc()
    return {
        "runtime": "k3s",
        "features": {
            "rbac": True,
            "ingress": True,
            "hpa": True,
            "pod_disruption_budget": True,
            "resource_quota": True,
            "limit_range": True,
            "network_policy": True,
            "netem_fault_injection": "host action (workflow)",
            "distributed_tracing": "OTLP/HTTP -> Jaeger",
            "metrics": "Prometheus /metrics",
        },
    }


@app.get("/platform/health")
def health():
    """聚合平台服务健康状态（真实服务间调用，会出现在 trace 中）。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/platform/health").inc()
    deps = {}
    for svc in PLATFORM_DEPS:
        status, body = tracing.traced_json_request(
            "GET", f"http://{svc}:8080/healthz", timeout=2.0,
            span_name=f"GET {svc}/healthz")
        deps[svc] = body.get("status") if status == 200 and isinstance(body, dict) else f"error:{status}"
    ok = sum(1 for v in deps.values() if v == "ok")
    return {"status": "ok" if ok == len(deps) else "degraded",
            "healthy": ok, "total": len(deps), "deps": deps}


@app.get("/platform/config")
def config_view():
    """读取配置中心的默认想定，验证平台服务间配置联动。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/platform/config").inc()
    status, body = tracing.traced_json_request(
        "GET", "http://config-service:8080/config/scenario.default",
        span_name="GET config-service/config/scenario.default")
    return {"upstream_status": status, "config": body}
