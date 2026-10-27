# 链路追踪动作：从 Jaeger 读取真实 Trace，汇总服务级指标并推导运行态依赖图
"""
与"静态拓扑图"的区别：
  DESIGN_GRAPH  —— 设计态拓扑（人工声明，用于比对）
  trace graph   —— 运行态拓扑，由真实 span 的父子关系（CHILD_OF）统计得出

采集口径：一次 Jaeger 查询拉取每个服务最近的 trace，在 Pod 内完成聚合，
只回传紧凑 JSON（避免大量 trace 数据跨 kubectl exec 传输）。
"""
from wf import register_action
import json
import os
import subprocess
import time

NAMESPACE_DEFAULT = "sim-test"
JAEGER_BASE = os.getenv("JAEGER_BASE", "http://jaeger:16686")

SIM_SERVICES = [
    "orchestrator", "scenario-mgr", "entity-service", "comms-service",
    "sensor-service", "fire-control", "env-service", "log-service",
    "state-store", "telemetry-gw", "sim-bridge",
]

# 设计态拓扑：u -> [v...] 表示 u 调用 v（与 services/*/app.py 中的真实调用一致）
DESIGN_GRAPH = {
    "orchestrator": ["scenario-mgr", "fire-control", "telemetry-gw", "entity-service",
                     "comms-service", "sensor-service", "env-service", "log-service",
                     "state-store", "sim-bridge"],
    "scenario-mgr": ["state-store", "log-service"],
    "fire-control": ["sensor-service", "comms-service"],
    "sensor-service": ["entity-service", "env-service"],
    "entity-service": [],
    "comms-service": [],
    "env-service": [],
    "log-service": [],
    "state-store": [],
    "telemetry-gw": [],
    "sim-bridge": [],
}


def _exec_py(script, namespace, timeout=60):
    """在 orchestrator Pod 内执行一段 python（集群 DNS 可解析 jaeger:16686）"""
    r = subprocess.run(
        ["kubectl", "-n", namespace, "exec", "-i", "deploy/orchestrator", "--", "python", "-"],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"jaeger query failed: {r.stderr[-500:]}")
    out = r.stdout.strip().splitlines()
    if not out:
        raise RuntimeError("jaeger query returned empty output")
    return json.loads(out[-1])


_AGG_SCRIPT = r'''
import json, urllib.request, urllib.parse

BASE = __BASE__
SERVICES = __SERVICES__
LIMIT = __LIMIT__
LOOKBACK = __LOOKBACK__
WINDOW_START = __WINDOW_START__
WINDOW_END = __WINDOW_END__

def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=15).read().decode())

result = {"services": {}, "edges": {}, "trace_ids": [], "errors": []}
try:
    known = get("/api/services")["data"]
except Exception as e:
    print(json.dumps({"error": "services api failed: %s" % e}))
    raise SystemExit(0)

span_svc, durations, errs, ops = {}, {}, {}, {}
err_first, err_last, failed_peers = {}, {}, {}


def span_service(sp, procs):
    """解析 span 所属服务：新版 Jaeger 把 process 放在 trace.processes[processID]，
    老版直接内联在 span.process。两者都兼容。"""
    pid = sp.get("processID")
    if pid and pid in procs:
        return (procs.get(pid) or {}).get("serviceName")
    return (sp.get("process") or {}).get("serviceName")


# 按 traceID 去重：多个服务查到的往往是同一条 trace，重复累加会把 span 数虚高
trace_map = {}
# Jaeger 1.57 的 /api/traces 在本环境会忽略 lookback 参数（实测仍返回数小时前
# 的 trace），因此显式传 start/end（微秒时间戳），并在聚合阶段按窗口过滤 span。
# 旧实现用 lookback 导致历史错误 span 被当作本次故障证据，感知延迟出现负值。
for svc in SERVICES:
    if svc not in known:
        continue
    q = "/api/traces?service=%s&limit=%d&start=%d&end=%d" % (
        urllib.parse.quote(svc), LIMIT,
        int(WINDOW_START * 1e6), int(WINDOW_END * 1e6))
    try:
        data = get(q)["data"]
    except Exception:
        continue
    for tr in data:
        trace_map[tr.get("traceID")] = tr
traces = list(trace_map.values())

edges = {}
trace_ids = set()
span_total = 0

# 第一遍：登记 spanID -> service。父子边依赖完整映射，两遍扫描保证"子 span 先出现"也不丢边。
for tr in traces:
    procs = tr.get("processes") or {}
    for sp in tr.get("spans", []):
        span_svc[sp.get("spanID")] = span_service(sp, procs) or "?"

# 第二遍：耗时 / 错误 / 操作 / 调用边（严格按窗口过滤，避免历史 span 污染）
for tr in traces:
    procs = tr.get("processes") or {}
    in_window_spans = []
    for sp in tr.get("spans", []):
        ts = sp.get("startTime", 0) / 1e6  # 微秒 -> 秒
        if ts < WINDOW_START or ts > WINDOW_END:
            continue
        in_window_spans.append((sp, ts))
    if not in_window_spans:
        continue
    trace_ids.add(tr.get("traceID"))
    for sp, ts in in_window_spans:
        span_total += 1
        svc = span_svc.get(sp.get("spanID")) or span_service(sp, procs) or "?"
        tags = {t.get("key"): t.get("value") for t in (sp.get("tags") or [])}
        durations.setdefault(svc, []).append(sp.get("duration", 0) / 1000.0)  # -> ms
        try:
            code = int(tags.get("http.status_code") or 0)
        except (TypeError, ValueError):
            code = 0
        bad = (str(tags.get("error")).lower() == "true"
               or tags.get("otel.status_code") == "ERROR"
               or code >= 500)
        if bad:
            errs[svc] = errs.get(svc, 0) + 1
            err_first[svc] = min(err_first.get(svc, 1e18), ts)
            err_last[svc] = max(err_last.get(svc, 0), ts)
            peer = tags.get("peer.service")
            if peer:
                failed_peers[peer] = failed_peers.get(peer, 0) + 1
        ops.setdefault(svc, {}).setdefault(sp.get("operationName", "?"), 0)
        ops[svc][sp.get("operationName", "?")] += 1
        for ref in sp.get("references") or []:
            if ref.get("refType") == "CHILD_OF":
                parent = span_svc.get(ref.get("spanID"))
                if parent and parent != svc:
                    k = parent + "->" + svc
                    edges[k] = edges.get(k, 0) + 1

def pct(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    i = min(len(s) - 1, int(round((len(s) - 1) * p)))
    return round(s[i], 3)

for svc, ds in durations.items():
    n = len(ds)
    result["services"][svc] = {
        "span_count": n,
        "error_count": errs.get(svc, 0),
        "error_rate": round(errs.get(svc, 0) / n, 4) if n else 0.0,
        "mean_ms": round(sum(ds) / n, 3) if n else 0.0,
        "p50_ms": pct(ds, 0.50),
        "p95_ms": pct(ds, 0.95),
        "max_ms": round(max(ds), 3) if ds else 0.0,
        "first_error_at": round(err_first[svc], 3) if svc in err_first else None,
        "last_error_at": round(err_last[svc], 3) if svc in err_last else None,
        "operations": sorted(ops.get(svc, {}).items(), key=lambda kv: -kv[1])[:6],
    }

result["edges"] = edges
result["failed_peers"] = failed_peers
result["trace_ids"] = sorted(trace_ids)[-40:]
result["trace_count"] = len(trace_ids)
result["span_total"] = span_total
result["lookback_s"] = LOOKBACK
result["window_start"] = WINDOW_START
result["window_end"] = WINDOW_END
result["jaeger_services"] = known
print(json.dumps(result))
'''


def collect_once(namespace=NAMESPACE_DEFAULT, lookback_s=3600, limit=100, since=None):
    """查询 Jaeger 并聚合出服务级链路指标 + 运行态调用边

    since: 可选的绝对起始时间戳（秒）。用于故障窗口查询：窗口起点取
           max(now - lookback_s, since)，确保不会把窗口前的历史错误算进本次故障。
    """
    now = time.time()
    window_start = now - max(1, int(lookback_s))
    if since is not None:
        window_start = max(window_start, float(since))
    window_end = now
    script = (_AGG_SCRIPT
              .replace("__BASE__", repr(JAEGER_BASE))
              .replace("__SERVICES__", repr(SIM_SERVICES))
              .replace("__LIMIT__", str(limit))
              .replace("__LOOKBACK__", str(int(lookback_s)))
              .replace("__WINDOW_START__", repr(float(window_start)))
              .replace("__WINDOW_END__", repr(float(window_end))))
    return _exec_py(script, namespace)


def runtime_graph(agg):
    """由真实 span 父子关系还原的依赖图 {service: [callee...]}"""
    g = {s: [] for s in SIM_SERVICES}
    for edge in (agg.get("edges") or {}):
        if "->" not in edge:
            continue
        parent, child = edge.split("->", 1)
        g.setdefault(parent, [])
        if child not in g[parent]:
            g[parent].append(child)
    return g


def graph_diff(runtime_g):
    """运行态图 vs 设计态图：返回缺失边/多出边（反映插桩覆盖情况）"""
    design_edges = {(u, v) for u, vs in DESIGN_GRAPH.items() for v in vs}
    runtime_edges = {(u, v) for u, vs in runtime_g.items() for v in vs}
    return {
        "design_edges": sorted(f"{u}->{v}" for u, v in design_edges),
        "runtime_edges": sorted(f"{u}->{v}" for u, v in runtime_edges),
        "missing_in_runtime": sorted(f"{u}->{v}" for u, v in design_edges - runtime_edges),
        "extra_in_runtime": sorted(f"{u}->{v}" for u, v in runtime_edges - design_edges),
        "coverage": round(len(design_edges & runtime_edges) / max(1, len(design_edges)), 3),
    }


def _save(run_dir, name, obj):
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return path


@register_action("trace.collect")
def collect(ctx, params):
    """采集真实 Trace：每服务 span 数/错误率/p95 + 运行态依赖边，落盘 trace_stats.json"""
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)
    # 默认回看窗口：注入时刻 - 60s 起（实验关心故障窗口内的链路），最多 30 分钟
    lookback = int(params.get("lookback_s", 900))
    limit = int(params.get("limit", 120))
    since = params.get("since")

    print(f"[TRACE] querying Jaeger ({JAEGER_BASE}) lookback={lookback}s limit={limit} since={since}")
    t0 = time.time()
    agg = collect_once(namespace, lookback, limit, since=since)
    if agg.get("error"):
        print(f"[TRACE] collection failed: {agg['error']}")
        ctx["trace_stats"] = {"available": False, "error": agg["error"]}
        return ctx["trace_stats"]

    g = runtime_graph(agg)
    diff = graph_diff(g)
    stats = {
        "available": True,
        "jaeger": JAEGER_BASE,
        "query_duration_s": round(time.time() - t0, 3),
        "trace_count": agg.get("trace_count", 0),
        "span_total": agg.get("span_total", 0),
        "services": agg.get("services", {}),
        "edges": agg.get("edges", {}),
        "failed_peers": agg.get("failed_peers", {}),
        "sample_trace_ids": agg.get("trace_ids", [])[-10:],
        "runtime_graph": g,
        "graph_diff": diff,
        "lookback_s": agg.get("lookback_s"),
        "window_start": agg.get("window_start"),
        "window_end": agg.get("window_end"),
        "timestamp": time.time(),
    }
    ctx["trace_stats"] = stats
    ctx["trace_graph"] = g

    run_dir = ctx.get("run_dir") or f"runs/{ctx['exp_id']}"
    _save(run_dir, "trace_stats.json", stats)
    _save(run_dir, "trace_graph.json", {"nodes": sorted(g), "edges": diff["runtime_edges"],
                                        "edge_counts": agg.get("edges", {})})

    svc_line = ", ".join(f"{s}={v['span_count']}spans/{v['p95_ms']}ms"
                         for s, v in list(stats["services"].items())[:6])
    print(f"[TRACE] traces={stats['trace_count']} spans={stats['span_total']} "
          f"edges={len(diff['runtime_edges'])} coverage={diff['coverage']}")
    print(f"[TRACE] services: {svc_line}")
    return stats


@register_action("trace.graph")
def graph(ctx, params):
    """仅输出运行态依赖图与设计态拓扑的差异（插桩覆盖校验）"""
    stats = ctx.get("trace_stats")
    if not stats or not stats.get("available"):
        stats = collect(ctx, params)
    g = stats["runtime_graph"]
    diff = stats["graph_diff"]
    print("[TRACE] runtime graph (from real spans):")
    for u, vs in sorted(g.items()):
        if vs:
            print(f"        {u} -> {', '.join(sorted(vs))}")
    if diff["missing_in_runtime"]:
        print(f"[TRACE] WARNING 设计态有但运行时未观测到的边: {diff['missing_in_runtime']}")
    print(f"[TRACE] edge coverage = {diff['coverage']}")
    return {"runtime_graph": g, "diff": diff}


@register_action("trace.verify")
def verify(ctx, params):
    """校验追踪链路是否真实落库：Jaeger 服务列表 + 最近 trace 是否包含多级 span"""
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)
    agg = collect_once(namespace, int(params.get("lookback_s", 900)), 20)
    known = agg.get("jaeger_services", [])
    report = {
        "jaeger_services": known,
        "expected_services": SIM_SERVICES,
        "missing_services": [s for s in SIM_SERVICES if s not in known],
        "trace_count": agg.get("trace_count", 0),
        "span_total": agg.get("span_total", 0),
        "ok": bool(agg.get("trace_count")) and bool(known),
    }
    print(f"[TRACE] verify: services={len(known)} traces={report['trace_count']} "
          f"spans={report['span_total']} ok={report['ok']}")
    ctx["trace_verify"] = report
    return report


# ----------------------------------------------------------------------
# 业务流量探针：让 Trace 有真实数据可采
# ----------------------------------------------------------------------
_TRAFFIC_SCRIPT = r'''
import json, time, urllib.request
N = __N__
GAP = __GAP__
ok = bad = 0
last = []
for i in range(N):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8080/trigger?scenario=battalion_attack",
            data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=__TIMEOUT__) as r:
            body = json.loads(r.read().decode())
        st = ((body.get("engage") or {}).get("mission") or {}).get("status")
        last.append({"i": i, "status": st,
                     "fire_control": (body.get("health") or {}).get("fire-control")})
        if st == "engaged":
            ok += 1
        else:
            bad += 1
    except Exception as e:
        bad += 1
        last.append({"i": i, "error": str(e)[:100]})
    if i < N - 1:
        time.sleep(GAP)
print(json.dumps({"requests": N, "engaged": ok, "degraded_or_failed": bad, "last": last[-3:]}))
'''


@register_action("traffic.probe")
def traffic_probe(ctx, params):
    """从 orchestrator Pod 内部发起真实业务流量（POST /trigger），
    让链路追踪有 span 可采、并暴露故障期间的失败调用。"""
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)
    n = int(params.get("requests", 6))
    gap = float(params.get("interval_s", 0.2))
    timeout = float(params.get("timeout_s", 6))

    script = (_TRAFFIC_SCRIPT
              .replace("__N__", str(n))
              .replace("__GAP__", str(gap))
              .replace("__TIMEOUT__", str(timeout)))
    r = subprocess.run(
        ["kubectl", "-n", namespace, "exec", "-i", "deploy/orchestrator", "--", "python", "-"],
        input=script, capture_output=True, text=True, timeout=30 + n * int(timeout),
    )
    if r.returncode != 0:
        print(f"[TRAFFIC] probe failed: {r.stderr[-300:]}")
        return {"error": r.stderr[-300:]}
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": "unparsable output", "raw": r.stdout[-300:]}

    res["timestamp"] = time.time()
    ctx.setdefault("traffic", []).append(res)
    print(f"[TRAFFIC] requests={res['requests']} engaged={res['engaged']} "
          f"degraded_or_failed={res['degraded_or_failed']}")
    return res

