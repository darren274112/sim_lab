# 算法模板 - 复制此文件并重命名为你的算法文件名
import time


def detect(ctx):
    """
    故障感知算法
    ctx 中包含: inject_time, fault_info, timestamps, vars, config 等
    """
    detected_at = time.time()
    # TODO: 实现你的故障感知逻辑
    return {
        "detected": True,
        "detected_at": detected_at,
        "faulty_service": ctx.get("fault_info", {}).get("service"),
        "confidence": 0.9,
        "method": "your_method_name",
    }


def tolerate(ctx):
    """
    容错算法
    ctx 中包含: fault_info, algorithm_result 等
    """
    decision_time = time.time()
    # TODO: 实现你的容错决策逻辑
    return {
        "actions": [],
        "decision_time": decision_time,
        "strategy": "your_strategy",
    }


def consist(ctx):
    """
    一致性算法
    ctx 中包含: fault_info, algorithm_result 等
    """
    decision_time = time.time()
    # TODO: 实现你的一致性维护逻辑
    return {
        "actions": [],
        "decision_time": decision_time,
        "strategy": "your_strategy",
    }
