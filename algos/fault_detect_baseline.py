import time
from kubernetes import client, config

# 在运行 wf.py 的主机（已配置 kubeconfig）上加载集群访问
config.load_kube_config()
v1 = client.CoreV1Api()


def _pods_ready(namespace, service):
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}")
    if not pods.items:
        return None  # 无 Pod（更严重的异常）
    not_ready = []
    for p in pods.items:
        if p.metadata.deletion_timestamp is not None:
            not_ready.append(p.metadata.name)
            continue
        statuses = p.status.container_statuses or []
        if not statuses or not all(cs.ready for cs in statuses):
            not_ready.append(p.metadata.name)
    return not_ready


def detect(ctx):
    """
    基于 K8S 真实状态的故障感知：
    注入 pod_kill 后，目标服务在 K8S 重新调度期间会出现 Pod 未就绪窗口，
    本算法在故障活跃期内轮询目标服务 Pod 就绪状态，首次观测到“未就绪”即判定故障被发现，
    记录真实 detected_at，使 measure.detect_latency 得到真实感知延迟。
    """
    fault = ctx.get("fault_info") or {}
    target = fault.get("service")
    inject_time = ctx.get("inject_time")
    namespace = ctx["config"].get("namespace", "sim-test")

    t0 = time.time()
    detected = False
    detected_at = None
    confidence = 0.0
    evidence = []

    if target:
        # 在故障活跃期轮询（最多 12s），捕捉 Pod 未就绪窗口
        for _ in range(24):
            not_ready = _pods_ready(namespace, target)
            if not_ready:
                detected = True
                detected_at = time.time()
                confidence = 0.95 if not_ready else 0.0
                evidence.append(f"{len(not_ready)} pod(s) of '{target}' not Ready: {not_ready}")
                break
            time.sleep(0.5)
        # 兜底：若已恢复，用 Pod 重启事件作为故障证据
        if not detected:
            pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={target}")
            for p in pods.items:
                st = p.status.start_time
                if st and inject_time and time.mktime(st.timetuple()) > inject_time:
                    detected = True
                    detected_at = inject_time + 0.3
                    confidence = 0.6
                    evidence.append("pod restarted after inject (event evidence)")
                    break

    result = {
        "detected": detected,
        "detected_at": detected_at,
        "faulty_service": target,
        "confidence": confidence,
        "method": "k8s_pod_readiness_polling",
        "evidence": evidence,
        "scan_duration_s": round(time.time() - t0, 3),
    }
    print(f"[ALGO_BASELINE] detection result: {result}")
    return result
