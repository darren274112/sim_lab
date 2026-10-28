"""仿真结果评估服务：对 runs/<exp_id>/ 的工作流产出做量化评估。

直接读取宿主机挂载的 runs/ 目录，产出感知、容错、一致性、追踪四类指标，
并生成结构化评估报告（JSON）供工作流与平台网关调用。
"""
from common.base import create_app, REQUEST_COUNT
import json
import os
import time
from pathlib import Path
from typing import Any

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "eval-service")

RUNS_DIR = Path(os.getenv("RUNS_DIR", "/runs"))


def _load_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _scan_exp(exp_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    d = RUNS_DIR / exp_id
    if not d.exists():
        return None, None
    return _load_json(d / "state.json"), _load_json(d / "ctx.json")


def _eval_perception(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    steps = state.get("steps") or {}
    det = steps.get("detect") or {}
    ml = det.get("result") or {}
    mlat = steps.get("measure_latency") or steps.get("measure") or {}
    mres = mlat.get("result") or {}
    if not mres and ctx.get("detect_latency"):
        mres = ctx.get("detect_latency") or {}

    det_res = ctx.get("detection_result") or ml
    score = 0.0
    notes = []
    detected = bool(det_res.get("detected") or ml.get("detected"))
    if detected:
        score += 0.4
        notes.append("检测到故障")
    conf = det_res.get("confidence") or ml.get("confidence")
    if isinstance(conf, (int, float)):
        score += min(0.2, float(conf) * 0.2)
    lat = mres.get("evidence_latency_ms") or mres.get("latency_ms")
    if isinstance(lat, (int, float)) and float(lat) >= 0:
        score += 0.2
        notes.append(f"证据延迟 {float(lat):.1f} ms")
    if det_res.get("faulty_service") or ml.get("faulty_service"):
        score += 0.2
        notes.append(f"根因 {det_res.get('faulty_service') or ml.get('faulty_service')}")
    return {
        "detected": detected,
        "faulty_service": det_res.get("faulty_service") or ml.get("faulty_service"),
        "confidence": conf,
        "evidence_latency_ms": lat,
        "detection_signal": det_res.get("detection_signal") or ml.get("detection_signal"),
        "score": round(min(1.0, score), 3),
        "notes": notes,
    }


def _eval_tolerance(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    steps = state.get("steps") or {}
    mrec = steps.get("measure_recovery") or {}
    mres = mrec.get("result") or {}
    r = mres.get("recovery_time_s") if isinstance(mres, dict) else None
    if r is None and ctx.get("recovery_time"):
        rt = ctx.get("recovery_time")
        if isinstance(rt, dict):
            r = rt.get("recovery_time_s")
    score = 0.0
    notes = []
    if isinstance(r, (int, float)) and r >= 0:
        score += 0.6
        notes.append(f"恢复时间 {r:.3f} s")
    tol = ctx.get("tolerance") or (steps.get("tolerate") or {}).get("result") or {}
    if isinstance(tol, dict):
        if tol.get("actions_applied"):
            score += 0.4
            notes.append(f"容错动作 {len(tol.get('actions_applied') or [])} 项")
    return {"recovery_time_s": r, "score": round(min(1.0, score), 3), "notes": notes}


def _eval_consistency(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    steps = state.get("steps") or {}
    sv_b = steps.get("state_verify_base") or {}
    sv_d = steps.get("state_verify_divergent") or {}
    sv_a = steps.get("state_verify_after") or {}
    sr = steps.get("state_repair") or {}
    r_b = sv_b.get("result") or {}
    r_d = sv_d.get("result") or {}
    r_a = sv_a.get("result") or {}
    r_r = sr.get("result") or {}

    score = 0.0
    notes = []
    base_ok = r_b.get("consistent") if isinstance(r_b, dict) else None
    if base_ok is True:
        score += 0.25
        notes.append("基线一致")
    div = r_d.get("consistent") if isinstance(r_d, dict) else None
    div_keys = (r_d.get("divergent_keys") or []) if isinstance(r_d, dict) else []
    if div is False and div_keys:
        score += 0.25
        notes.append(f"观测分叉 {div_keys}")
    rep_cnt = (r_r.get("replicas") or 0) if isinstance(r_r, dict) else 0
    if rep_cnt:
        score += 0.25
        notes.append(f"修复副本 {rep_cnt}")
    aft = r_a.get("consistent") if isinstance(r_a, dict) else None
    if aft is True:
        score += 0.25
        notes.append("修复后一致")
    return {
        "base_consistent": base_ok,
        "divergent": div,
        "divergent_keys": div_keys,
        "repair_replicas": rep_cnt,
        "after_consistent": aft,
        "score": round(min(1.0, score), 3),
        "notes": notes,
    }


def _eval_tracing(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    steps = state.get("steps") or {}
    tr = steps.get("trace") or {}
    vg = steps.get("verify") or {}
    ts = tr.get("result") or {}
    vv = vg.get("result") or {}
    if not ts and ctx.get("trace_stats"):
        ts = ctx.get("trace_stats") or {}
    if not vv and ctx.get("trace_verify"):
        vv = ctx.get("trace_verify") or {}
    score = 0.0
    notes = []
    edges = ts.get("edges") if isinstance(ts, dict) else None
    if isinstance(edges, dict) and edges:
        score += 0.3
        notes.append(f"运行态边 {len(edges)}")
    spans = ts.get("span_total") if isinstance(ts, dict) else None
    if isinstance(spans, int) and spans > 0:
        score += 0.2
    ok = vv.get("ok") if isinstance(vv, dict) else None
    if ok is True:
        score += 0.5
        notes.append("trace.verify 通过")
    return {
        "span_total": spans,
        "edge_count": len(edges) if isinstance(edges, dict) else None,
        "verify_ok": ok,
        "score": round(min(1.0, score), 3),
        "notes": notes,
    }


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "simulation_result_evaluator", "runs_dir": str(RUNS_DIR)}


@app.get("/eval/list")
def list_runs():
    REQUEST_COUNT.labels(method="GET", endpoint="/eval/list").inc()
    items = []
    if not RUNS_DIR.exists():
        return {"runs": []}
    for d in sorted(RUNS_DIR.iterdir()):
        if not d.is_dir():
            continue
        s = _load_json(d / "state.json")
        if not s:
            continue
        items.append({
            "exp_id": s.get("exp_id") or d.name,
            "status": s.get("status"),
            "start_time": s.get("start_time"),
        })
    return {"runs": items}


@app.get("/eval/{exp_id}")
def evaluate(exp_id: str):
    REQUEST_COUNT.labels(method="GET", endpoint="/eval/{exp_id}").inc()
    state, ctx = _scan_exp(exp_id)
    if not state:
        return {"ok": False, "error": "找不到 runs/%s/state.json" % exp_id}
    perception = _eval_perception(state, ctx or {})
    tolerance = _eval_tolerance(state, ctx or {})
    consistency = _eval_consistency(state, ctx or {})
    tracing = _eval_tracing(state, ctx or {})
    overall = round((perception["score"] + tolerance["score"] +
                     consistency["score"] + tracing["score"]) / 4.0, 3)
    grade = "A" if overall >= 0.8 else ("B" if overall >= 0.6 else ("C" if overall >= 0.4 else "D"))
    report = {
        "ok": True,
        "exp_id": exp_id,
        "status": state.get("status"),
        "evaluated_at": time.time(),
        "scores": {
            "perception": perception["score"],
            "tolerance": tolerance["score"],
            "consistency": consistency["score"],
            "tracing": tracing["score"],
            "overall": overall,
            "grade": grade,
        },
        "details": {
            "perception": perception,
            "tolerance": tolerance,
            "consistency": consistency,
            "tracing": tracing,
        },
    }
    return report


@app.get("/eval/{exp_id}/summary")
def evaluate_summary(exp_id: str):
    REQUEST_COUNT.labels(method="GET", endpoint="/eval/{exp_id}/summary").inc()
    r = evaluate(exp_id)
    if not r.get("ok"):
        return r
    return {"ok": True, "exp_id": exp_id, "scores": r["scores"]}
