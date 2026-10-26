# 测量动作：计算故障感知延迟等指标
from wf import register_action


@register_action("measure.detect_latency")
def measure_detect_latency(ctx, params):
    """计算故障感知延迟 = 检测时间 - 故障注入时间"""
    inject_time = ctx.get("inject_time") or ctx.get("timestamps", {}).get("inject")
    detect_time = ctx.get("detect_time") or ctx.get("timestamps", {}).get("detect")

    if inject_time and detect_time:
        latency = detect_time - inject_time
        result = {
            "inject_time": inject_time,
            "detect_time": detect_time,
            "latency_seconds": latency,
            "latency_ms": latency * 1000,
        }
        ctx["metrics"] = ctx.get("metrics", {})
        ctx["metrics"]["detect_latency"] = result
        print(f"\n{'='*50}")
        print(f"DETECT_LATENCY: {latency:.3f}s ({latency*1000:.1f}ms)")
        print(f"DETECTED: {ctx.get('detect_result', {}).get('detected')}")
        print(f"SERVICE: {ctx.get('detect_result', {}).get('faulty_service')}")
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
