# 观测动作：采集基线指标（真实数据）
from wf import register_action
import time
import json
import os
import subprocess

NAMESPACE_DEFAULT = "sim-test"

SIM_SERVICES = [
    "orchestrator", "scenario-mgr", "entity-service", "comms-service",
    "sensor-service", "fire-control", "env-service", "log-service",
    "state-store", "telemetry-gw", "sim-bridge",
]


def _run(cmd, timeout=30, input_text=None):
    r = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def _metrics_server_baseline(namespace):
    """通过 metrics-server (kubectl top pods) 采集真实 CPU/内存"""
    out = _run(["kubectl", "top", "pods", "-n", namespace, "--no-headers"], timeout=30)
    res = {}
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name, cpu, mem = parts[0], parts[1], parts[2]
        svc = name.rsplit("-", 2)[0] if name.count("-") >= 2 else name
        res.setdefault(svc, {"cpu": cpu, "memory": mem})
    return res


def _prom_query(expr, namespace):
    """在集群内通过 orchestrator Pod 查询 Prometheus（集群 DNS 可解析 prometheus:9090）"""
    script = (
        "import json,urllib.request,urllib.parse\n"
        "base='http://prometheus:9090'\n"
        "u=base+'/api/v1/query?query='+urllib.parse.quote(" + repr(expr) + ")\n"
        "print(urllib.request.urlopen(u,timeout=10).read().decode())\n"
    )
    r = subprocess.run(
        ["kubectl", "-n", namespace, "exec", "-i", "deploy/orchestrator", "--", "python", "-"],
        input=script, capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"prom query failed: {r.stderr}")
    resp = json.loads(r.stdout)
    vals = resp.get("data", {}).get("result", [])
    if not vals:
        return 0.0
    return float(vals[0]["value"][1])


def _prom_rates_by_instance(namespace):
    """单次批量查询所有服务的请求率（按 instance 标签分组）。

    相比逐服务查询（每次一次 kubectl exec，~0.4s），这里只做 1 次 exec，
    是高频采样路径上的关键优化。返回 {service: rate}。
    """
    expr = 'sum by (instance) (rate({__name__=~".*_requests_total"}[2m]))'
    script = (
        "import json,urllib.request,urllib.parse\n"
        "base='http://prometheus:9090'\n"
        "u=base+'/api/v1/query?query='+urllib.parse.quote(" + repr(expr) + ")\n"
        "d=json.loads(urllib.request.urlopen(u,timeout=10).read().decode())\n"
        "out={}\n"
        "for r in d['data']['result']:\n"
        "    inst=r['metric'].get('instance','')\n"
        "    out[inst.split(':')[0]]=float(r['value'][1])\n"
        "print(json.dumps(out))\n"
    )
    r = subprocess.run(
        ["kubectl", "-n", namespace, "exec", "-i", "deploy/orchestrator", "--", "python", "-"],
        input=script, capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


@register_action("obs.collect_baseline")
def collect_baseline(ctx, params):
    """采集实验基线指标（真实）：metrics-server 的 CPU/内存 + Prometheus 请求率，保存 baseline.json"""
    print("[OBS] Collecting REAL baseline metrics...")
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)

    resource = _metrics_server_baseline(namespace)
    prometheus_available = True
    rates = {}
    try:
        rates = _prom_rates_by_instance(namespace)  # 单次批量查询
        if not rates:
            raise RuntimeError("empty result")
    except Exception as e:
        prometheus_available = False
        print(f"[OBS] Prometheus unavailable ({e}); request-rate left as 0")

    baseline = {
        "timestamp": time.time(),
        "namespace": namespace,
        "prometheus_available": prometheus_available,
        "services": {},
    }
    for svc in SIM_SERVICES:
        baseline["services"][svc] = {
            "cpu": resource.get(svc, {}).get("cpu", "n/a"),
            "memory": resource.get(svc, {}).get("memory", "n/a"),
            "request_rate_per_s": round(rates.get(svc, 0.0), 4),
        }

    ctx["baseline"] = baseline
    run_dir = ctx.get("run_dir") or f"runs/{ctx['exp_id']}"
    os.makedirs(run_dir, exist_ok=True)
    with open(f"{run_dir}/baseline.json", "w") as f:
        json.dump(baseline, f, indent=2)

    print(f"[OBS] baseline collected: prometheus={prometheus_available}, "
          f"services={len(baseline['services'])}")
    return baseline
