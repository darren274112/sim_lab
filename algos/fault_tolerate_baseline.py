import time

def tolerate(ctx):
    print("[ALGO_TOLERATE] Deciding fault tolerance actions...")
    time.sleep(1)
    fault_service = ctx.get("fault_info", {}).get("service", "fire-control")
    
    actions = [
        {"type": "degrade", "target": "log-service", "reason": "non-critical"},
        {"type": "scale", "target": "fire-control", "replicas": 2},
    ]
    
    return {
        "actions": actions,
        "decision_time": time.time(),
        "strategy": "baseline"
    }
