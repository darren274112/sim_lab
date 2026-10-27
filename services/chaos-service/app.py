"""
通用混沌工程服务：在集群内通过 K8S API 执行 Pod 删除与副本伸缩。

与 workflow 宿主机侧的 actions/fault.py 互补：
  - actions/fault.py：Pod kill + tc/netem（需要宿主机权限）；
  - chaos-service：集群内 K8S API 编排（需要 RBAC，见 manifests/platform-rbac.yaml）。

仅使用标准库 + ServiceAccount，避免在业务镜像中引入 Kubernetes SDK。
"""
from common.base import create_app, REQUEST_COUNT
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "chaos-service")
API = os.getenv("K8S_API", "https://kubernetes.default.svc")
NAMESPACE = os.getenv("NAMESPACE", "sim-test")
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
TOKEN_FILE = os.path.join(SA_DIR, "token")
CA_FILE = os.path.join(SA_DIR, "ca.crt")

_history = []


def _token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


def _ssl_context():
    try:
        return ssl.create_default_context(cafile=CA_FILE)
    except OSError:
        return ssl.create_default_context()


def _k8s(method, path, body=None, content_type="application/json"):
    token = _token()
    if not token:
        raise RuntimeError("service account token not found (not running in-cluster?)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/json",
                 "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=8, context=_ssl_context()) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})


def _record(kind, detail):
    entry = {"seq": len(_history) + 1, "kind": kind, "at": time.time(), "detail": detail}
    _history.append(entry)
    del _history[:-100]
    return entry


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "chaos",
            "namespace": NAMESPACE, "experiments": len(_history),
            "in_cluster": bool(_token())}


@app.get("/chaos/pods")
def list_pods(service: str | None = None):
    REQUEST_COUNT.labels(method="GET", endpoint="/chaos/pods").inc()
    q = ""
    if service:
        q = "?labelSelector=" + urllib.parse.quote(f"app={service}")
    try:
        status, body = _k8s("GET", f"/api/v1/namespaces/{NAMESPACE}/pods{q}")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    pods = [{"name": i["metadata"]["name"],
             "phase": i.get("status", {}).get("phase"),
             "app": (i["metadata"].get("labels") or {}).get("app"),
             "node": i.get("spec", {}).get("nodeName")}
            for i in body.get("items", [])]
    return {"ok": True, "count": len(pods), "pods": pods}


@app.post("/chaos/pod-kill")
def pod_kill(body: dict):
    """删除目标服务的一个或多个 Running Pod。body = {service, count?}"""
    REQUEST_COUNT.labels(method="POST", endpoint="/chaos/pod-kill").inc()
    service = str(body.get("service") or "")
    count = max(1, min(int(body.get("count") or 1), 10))
    if not service:
        return {"ok": False, "error": "service is required"}
    try:
        status, listing = _k8s(
            "GET", f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector="
                   + urllib.parse.quote(f"app={service}"))
        targets = [p for p in listing.get("items", [])
                   if not p["metadata"].get("deletionTimestamp")
                   and p.get("status", {}).get("phase") == "Running"][:count]
        killed = []
        for pod in targets:
            name = pod["metadata"]["name"]
            _k8s("DELETE", f"/api/v1/namespaces/{NAMESPACE}/pods/{name}")
            killed.append(name)
        entry = _record("pod_kill", {"service": service, "pods": killed})
        return {"ok": bool(killed), "service": service, "killed": killed,
                "experiment": entry}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


@app.post("/chaos/scale")
def scale(body: dict):
    """伸缩 Deployment。body = {deployment, replicas}"""
    REQUEST_COUNT.labels(method="POST", endpoint="/chaos/scale").inc()
    dep = str(body.get("deployment") or "")
    try:
        replicas = int(body.get("replicas"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "replicas must be an int"}
    if not dep or replicas < 0 or replicas > 20:
        return {"ok": False, "error": "deployment required, replicas in [0, 20]"}
    try:
        status, res = _k8s(
            "PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{dep}/scale",
            body={"spec": {"replicas": replicas}},
            content_type="application/merge-patch+json")
        entry = _record("scale", {"deployment": dep, "replicas": replicas})
        return {"ok": status in (200, 201), "deployment": dep, "replicas": replicas,
                "experiment": entry, "spec": res.get("spec")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


@app.get("/chaos/experiments")
def experiments(limit: int = 20):
    REQUEST_COUNT.labels(method="GET", endpoint="/chaos/experiments").inc()
    return {"total": len(_history), "experiments": _history[-max(1, min(limit, 100)):]}


@app.get("/chaos/self-check")
def self_check():
    """验证 RBAC：尝试读取 Pod 列表（只读权限即可）。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/chaos/self-check").inc()
    try:
        status, body = _k8s("GET", f"/api/v1/namespaces/{NAMESPACE}/pods?limit=1")
        return {"ok": status == 200, "can_list_pods": True,
                "namespace": NAMESPACE, "items": len(body.get("items", []))}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
