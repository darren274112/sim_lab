import time

def detect(ctx):
    """
    Baseline fault detection algorithm - simple threshold based
    """
    print("[ALGO_BASELINE] Scanning for faults...")
    time.sleep(2)  # Simulate processing
    
    # In real scenario, read from Prometheus/logs
    inject_time = ctx.get("inject_time")
    if inject_time:
        detected_at = time.time()
        return {
            "detected": True,
            "detected_at": detected_at,
            "faulty_service": ctx.get("fault_info", {}).get("service", "unknown"),
            "confidence": 0.85,
            "method": "threshold_baseline"
        }
    return {"detected": False, "detected_at": time.time()}
