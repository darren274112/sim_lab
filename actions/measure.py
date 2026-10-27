# 测量动作：计算故障感知延迟等指标
from wf import register_action


@register_action("measure.detect_latency")
def measure_detect_latency(ctx, params):
    """计算故障感知延迟。

    拆成三个口径，避免用"批处理算法耗时"冒充"感知延迟"：
      evidence_latency_ms —— 注入 -> 最早可观测证据（首个失败 span / 首次未就绪）出现
      scan_duration_ms   —— 感知算法自身扫描耗时
      end_to_end_ms      —— 注入 -> 算法得出结论（含调度到算法执行开始的等待）

    负值保护：证据时刻早于注入时刻只可能来自陈旧数据或时钟问题，
    此时保留原始值并标记 anomaly，同时把对外延迟钳到 0，避免"负延迟"被当成有效指标。
    """
    inject_time = ctx.get("inject_time") or ctx.get("timestamps", {}).get("inject")
    detect_time = ctx.get("detect_time") or ctx.get("timestamps", {}).get("detect")
    det = ctx.get("detect_result") or {}
    scan = det.get("scan_duration_s")
    invoked = det.get("detect_invoked_at")
    evidence_at = det.get("detected_at")

    if inject_time and detect_time:
        conclusion_latency = detect_time - inject_time
        raw_evidence = (evidence_at - inject_time) if evidence_at else conclusion_latency
        anomaly = raw_evidence < 0 or conclusion_latency < 0
        evidence_latency = max(0.0, raw_evidence)
        if anomaly:
            print(f"[MEASURE] WARNING negative latency detected "
                  f"(evidence={raw_evidence:.3f}s conclusion={conclusion_latency:.3f}s). "
                  f"Check trace window / clock; clamping to 0.")
        result = {
            "inject_time": inject_time,
            "detect_time": detect_time,
            "evidence_at": evidence_at,
            "evidence_latency_ms": round(evidence_latency * 1000, 1),
            "latency_seconds": evidence_latency,
            "latency_ms": evidence_latency * 1000,
            "raw_evidence_latency_s": round(raw_evidence, 3),
            "conclusion_latency_s": round(conclusion_latency, 3),
            "anomaly": anomaly,
            "detection_signal": det.get("detection_signal"),
            "scan_duration_ms": round(scan * 1000, 1) if scan is not None else None,
            "end_to_end_ms": (round((invoked - inject_time + scan) * 1000, 1)
                              if invoked and scan is not None else None),
        }
        ctx["metrics"] = ctx.get("metrics", {})
        ctx["metrics"]["detect_latency"] = result
        print(f"\n{'='*50}")
        print(f"DETECT_LATENCY: {evidence_latency:.3f}s ({evidence_latency*1000:.1f}ms)  "
              f"signal={result['detection_signal']}  anomaly={anomaly}")
        if result["scan_duration_ms"] is not None:
            print(f"  scan={result['scan_duration_ms']:.1f}ms  "
                  f"conclusion={result['conclusion_latency_s']}s  "
                  f"end_to_end={result['end_to_end_ms']}ms")
        print(f"DETECTED: {det.get('detected')}")
        print(f"SERVICE: {det.get('faulty_service')}")
        print(f"{'='*50}\n")
        return result
    else:
        print(f"[MEASURE] Missing timestamps: inject={inject_time}, detect={detect_time}")
        return {"error": "missing timestamps"}


@register_action("measure.recovery_time")
def measure_recovery_time(ctx, params):
    """计算故障恢复时间 = 容错动作记录的 recovered_at - inject_time（不含感知时间）"""
    res = ctx.get("tolerate_result") or {}
    rt = res.get("recovery_time_s")
    if rt is not None:
        ctx["metrics"] = ctx.get("metrics", {})
        ctx["metrics"]["recovery_time"] = {"recovery_time_s": rt}
        print(f"\n{'='*50}")
        print(f"RECOVERY_TIME: {rt:.3f}s")
        print(f"STRATEGY: {res.get('strategy')}")
        print(f"{'='*50}\n")
        return {"recovery_time_s": rt}
    else:
        print("[MEASURE] No recovery_time available (tolerate did not record one)")
        return {"error": "no recovery_time"}


@register_action("measure.summary")
def measure_summary(ctx, params):
    """汇总并打印本次实验中各算法样例的关键结果"""
    metrics = ctx.get("metrics", {})
    print(f"\n{'='*60}")
    print("EXPERIMENT SUMMARY — 三项研究内容算法样例结果")
    print(f"{'='*60}")
    if "detect_latency" in metrics:
        dl = metrics["detect_latency"]
        print(f"[感知延迟] 证据出现 {dl['evidence_latency_ms']:.1f} ms "
              f"(signal={dl.get('detection_signal')})  "
              f"扫描 {dl.get('scan_duration_ms')} ms  "
              f"端到端 {dl.get('end_to_end_ms')} ms")
    if "recovery_time" in metrics:
        print(f"[恢复时间] {metrics['recovery_time']['recovery_time_s']:.3f} s")
    if "perception" in metrics:
        p = metrics["perception"]
        print(f"[内容一 故障感知] detected={p['detected']} faulty={p['faulty_service']} "
              f"confidence={p['confidence']} signal={p.get('detection_signal')}")
        print(f"             root_cause_top3={[r['service'] for r in p['root_cause_top3']]} "
              f"path={p['propagation_path']}")
        if p.get("impacted_services"):
            print(f"             impacted={[(i['service'], i['distance']) for i in p['impacted_services']]}")
        if p.get("trace_failed_peers"):
            print(f"             trace_failed_calls={p['trace_failed_peers']}")
    tr = ctx.get("trace_stats") or {}
    if tr.get("available"):
        diff = tr.get("graph_diff") or {}
        edges = (len(diff.get("runtime_edges", [])) if diff else tr.get("graph_edges"))
        print(f"[链路追踪] traces={tr.get('trace_count')} spans={tr.get('span_total')} "
              f"edges={edges} graph_source={tr.get('graph_source') or 'runtime_trace'} "
              f"coverage={diff.get('coverage')}")
    if "tolerance" in metrics:
        t = metrics["tolerance"]
        print(f"[内容二 容错] strategy={t['strategy']} q={t['q_values']} "
              f"degrade={t['degradation_plan']} recovery={t['recovery_time_s']}s")
        acts = t.get("actions_applied") or []
        if acts:
            print(f"             actions={[a.get('type') for a in acts]}")
    if "consistency" in metrics:
        c = metrics["consistency"]
        print(f"[内容三 一致性] level={c['consistency_level']} sync={c['sync_interval_s']}s "
              f"state={c['state']} events={c['triggered_events']} mm1_wait={c['mm1_wait_s']}")
        print(f"             观测分叉键={c.get('observed_divergent_keys')} "
              f"观测一致={c.get('observed_consistent')}")
        if c.get("event_adjustments"):
            print(f"             adjustments={c['event_adjustments']}")
    if "state_consistency" in metrics:
        sc = metrics["state_consistency"]
        print(f"[状态一致性校验] replicas={sc['replicas']} consistent={sc['consistent']} "
              f"divergent={sc['divergent_keys']} unreachable={sc['unreachable']}")
    print(f"{'='*60}\n")
    return metrics
