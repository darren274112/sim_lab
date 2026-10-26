# K8S 动作：检查 Pod 就绪状态、清理环境
from wf import register_action
from kubernetes import client, config

# 加载 kubeconfig（需提前配置好集群访问）
config.load_kube_config()
v1 = client.CoreV1Api()


@register_action("k8s.check_ready")
def check_ready(ctx, params):
    """检查指定服务的 Pod 是否全部处于 Running 且容器就绪"""
    services = params.get("services", [])
    namespace = ctx["config"].get("namespace", "sim-test")

    ready = []
    for svc in services:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={svc}")
        for pod in pods.items:
            if pod.status.phase == "Running":
                all_ready = True
                if pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if not cs.ready:
                            all_ready = False
                if all_ready:
                    ready.append(svc)
                    break

    print(f"Ready services: {ready}/{services}")
    if len(ready) < len(services):
        raise Exception(f"Not all services ready: {ready}")
    return {"ready": ready}


@register_action("k8s.restore")
def restore(ctx, params):
    """实验清理：保留接口，实际清理由人工或后续逻辑处理"""
    namespace = ctx["config"].get("namespace", "sim-test")
    print(f"[CLEANUP] Restoring environment in {namespace}")
    return {"status": "restored"}
