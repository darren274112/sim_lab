# 观测动作：采集基线指标
from wf import register_action
import time
import json
import os


@register_action("obs.collect_baseline")
def collect_baseline(ctx, params):
    """采集实验基线指标并保存到 runs/<exp_id>/baseline.json"""
    print("[OBS] Collecting baseline metrics...")
    baseline = {
        "timestamp": time.time(),
        "cpu": {"avg": 0.3},
        "memory": {"avg": 0.4},
        "latency": {"avg": 50},
    }
    ctx["baseline"] = baseline
    run_dir = ctx.get("run_dir") or f"runs/{ctx['exp_id']}"
    os.makedirs(run_dir, exist_ok=True)
    with open(f"{run_dir}/baseline.json", "w") as f:
        json.dump(baseline, f, indent=2)
    return baseline
