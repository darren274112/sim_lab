# 算法动作：动态加载 algos/ 下的算法模块并调用对应函数
from wf import register_action
import importlib
import time


@register_action("algo.run_detect")
def run_detect(ctx, params):
    """加载并运行故障感知算法，自动记录检测时间"""
    algo_name = params.get("algo", "fault_detect_baseline")
    print(f"[ALGO] Running fault detection: {algo_name}")

    mod = importlib.import_module(f"algos.{algo_name}")
    res = mod.detect(ctx)
    ctx["algorithm_result"] = res
    ctx["detect_result"] = res

    detect_time = res.get("detected_at")
    if isinstance(detect_time, (int, float)):
        ctx["detect_time"] = detect_time
        ctx["timestamps"]["detect"] = detect_time
    else:
        ctx["detect_time"] = time.time()
        ctx["timestamps"]["detect"] = ctx["detect_time"]

    print(f"[ALGO] Detection result: {res}")
    return res


@register_action("algo.run_tolerate")
def run_tolerate(ctx, params):
    """加载并运行容错算法"""
    algo_name = params.get("algo", "fault_tolerate_baseline")
    print(f"[ALGO] Running fault tolerance: {algo_name}")

    mod = importlib.import_module(f"algos.{algo_name}")
    res = mod.tolerate(ctx)
    ctx["algorithm_result"] = res
    ctx["tolerate_result"] = res
    return res


@register_action("algo.run_consist")
def run_consist(ctx, params):
    """加载并运行一致性算法"""
    algo_name = params.get("algo", "consist_baseline")
    print(f"[ALGO] Running consistency: {algo_name}")

    mod = importlib.import_module(f"algos.{algo_name}")
    res = mod.consist(ctx)
    ctx["algorithm_result"] = res
    return res
