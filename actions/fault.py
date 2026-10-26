# 故障注入动作：基于 K8S API 的真实故障注入
from wf import register_action
import time
from kubernetes import client, config

config.load_kube_config()
v1 = client.CoreV1Api()


@register_action("fault.inject_pod_kill")
def inject_pod_kill(ctx, params):
    """删除目标服务的一个 Pod，模拟服务失效"""
    service = params.get("service")
    namespace = ctx["config"].get("namespace", "sim-test")

    inject_time = time.time()
    ctx["inject_time"] = inject_time
    ctx["timestamps"]["inject"] = inject_time
    ctx["fault_info"] = {"type": "pod_kill", "service": service, "time": inject_time}

    print(f"[FAULT] Injecting pod_kill on {service} at {inject_time}")

    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}")
    if not pods.items:
        raise Exception(f"No pod found for service {service}")

    pod_name = pods.items[0].metadata.name
    ctx["fault_info"]["pod_name"] = pod_name
    v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
    print(f"[FAULT] Deleted pod {pod_name}")
    return {"status": "injected", "pod_name": pod_name, "service": service}


@register_action("fault.inject_netem")
def inject_netem(ctx, params):
    """网络仿真故障注入（预留接口，实际需配合 tc/ChaosMesh 等）"""
    service = params.get("service")
    delay = params.get("delay", "100ms")
    loss = params.get("loss", "5%")

    inject_time = time.time()
    ctx["inject_time"] = inject_time
    ctx["fault_info"] = {"type": "netem", "service": service, "delay": delay, "loss": loss}

    print(f"[FAULT] Injecting network delay {delay}, loss {loss} on {service}")
    return {"status": "injected", "type": "netem"}


@register_action("fault.clear_netem")
def clear_netem(ctx, params):
    """清理网络仿真规则（预留接口）"""
    print("[FAULT] Clearing network emulation rules")
    return {"status": "cleared"}
