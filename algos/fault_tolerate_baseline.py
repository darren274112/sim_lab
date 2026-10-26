import time
from kubernetes import client, config

config.load_kube_config()
v1 = client.CoreV1Api()
apps = client.AppsV1Api()


def _all_ready(namespace, service):
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}")
    if not pods.items:
        return False
    for p in pods.items:
        if p.metadata.deletion_timestamp is not None:
            return False
        statuses = p.status.container_statuses or []
        if not statuses or not all(cs.ready for cs in statuses):
            return False
    return True


def tolerate(ctx):
    """
    基于 K8S 的容错基线：
    故障发生后，等待目标服务 Pod 恢复就绪（即 K8S 自愈 / 真实调度恢复），
    必要时触发 rollout restart 强制恢复；记录真实 recovery_time（inject→全就绪）。
    """
    fault = ctx.get("fault_info") or {}
    target = fault.get("service") or "fire-control"
    inject_time = ctx.get("inject_time")
    namespace = ctx["config"].get("namespace", "sim-test")

    print(f"[ALGO_TOLERATE] Ensuring recovery of '{target}' ...")
    actions = []
    t0 = time.time()
    recovered_at = None

    # 第一阶段：等待 K8S 自愈（最多 30s）
    for _ in range(60):
        if _all_ready(namespace, target):
            recovered_at = time.time()
            break
        time.sleep(0.5)

    # 第二阶段：若仍不可用，触发 rollout restart 强制恢复
    if recovered_at is None:
        print(f"[ALGO_TOLERATE] forcing rollout restart on {target}")
        apps.patch_namespaced_deployment(
            name=target, namespace=namespace, body={"spec": {"template": {"metadata": {"annotations": {"restart": str(int(time.time()))}}}}}
        )
        actions.append({"type": "force_restart", "target": target})
        for _ in range(60):
            if _all_ready(namespace, target):
                recovered_at = time.time()
                break
            time.sleep(0.5)

    recovery_time = (recovered_at - inject_time) if (recovered_at and inject_time) else None
    result = {
        "actions": actions,
        "target": target,
        "recovered_at": recovered_at,
        "recovery_time_s": round(recovery_time, 3) if recovery_time is not None else None,
        "strategy": "k8s_self_heal_plus_force_restart",
        "decision_time": time.time(),
    }
    print(f"[ALGO_TOLERATE] result: {result}")
    return result
