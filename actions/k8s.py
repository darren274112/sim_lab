# K8S 动作：检查 Pod 就绪状态、清理环境
from wf import register_action
from kubernetes import client, config
import time

# 加载 kubeconfig（需提前配置好集群访问）
config.load_kube_config()
v1 = client.CoreV1Api()


def _ready_services(namespace, services):
    """返回当前至少有一个 Running 且容器就绪 Pod 的服务名列表"""
    ready = []
    for svc in services:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={svc}")
        for pod in pods.items:
            # 正在删除的 Pod 仍可能短暂保持 Ready，不能当作故障恢复依据
            if pod.metadata.deletion_timestamp is not None:
                continue
            if pod.status.phase == "Running":
                all_ready = True
                if pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if not cs.ready:
                            all_ready = False
                if all_ready:
                    ready.append(svc)
                    break
    return ready


@register_action("k8s.check_ready")
def check_ready(ctx, params):
    """轮询检查指定服务的 Pod 是否全部处于 Running 且容器就绪"""
    services = params.get("services", [])
    namespace = ctx["config"].get("namespace", "sim-test")
    timeout = float(params.get("timeout", 60))
    interval = 2.0
    deadline = time.time() + timeout
    last_ready = None

    while True:
        ready = _ready_services(namespace, services)
        if len(ready) == len(services):
            print(f"Ready services: {ready}/{services}")
            return {"ready": ready}

        missing = [svc for svc in services if svc not in ready]
        if ready != last_ready:
            print(f"Waiting for services: {len(ready)}/{len(services)} ready, missing={missing}")
            last_ready = ready

        if time.time() >= deadline:
            print(f"Ready services: {ready}/{services}")
            raise Exception(f"Not all services ready after {timeout:.0f}s: missing={missing}")

        time.sleep(interval)


@register_action("k8s.restore")
def restore(ctx, params):
    """实验清理：保留接口，实际清理由人工或后续逻辑处理"""
    namespace = ctx["config"].get("namespace", "sim-test")
    print(f"[CLEANUP] Restoring environment in {namespace}")
    return {"status": "restored"}
