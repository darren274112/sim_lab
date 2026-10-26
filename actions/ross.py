# ROSS 仿真引擎动作（桥接接口）
from wf import register_action


@register_action("ross.check_ready")
def ross_check_ready(ctx, params):
    """检查 ROSS 仿真引擎状态"""
    print("[ROSS] Checking ROSS simulation engine status...")
    return {"status": "ready", "engine": "ross"}


@register_action("ross.start_sim")
def ross_start_sim(ctx, params):
    """启动想定仿真"""
    print("[ROSS] Starting military simulation scenario...")
    ctx["ross_running"] = True
    return {"status": "started"}
