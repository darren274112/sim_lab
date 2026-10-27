# 故障注入动作：K8S Pod 故障、真实 tc/netem 网络故障、NetworkPolicy 分区
#
# 说明：
#   - pod_kill 使用 K8S API，不依赖宿主机权限；
#   - netem 通过 nsenter 进入目标 Pod 的网络命名空间执行 tc，需要 root +
#     K3S/crictl（或 docker）以及宿主机 tc 命令；
#   - network_partition 使用 K8S NetworkPolicy，需要 CNI 支持策略（K3S 默认支持）。
from wf import register_action
import json
import os
import re
import shutil
import subprocess
import time

from kubernetes import client, config

config.load_kube_config()
v1 = client.CoreV1Api()
apps = client.AppsV1Api()
networking = client.NetworkingV1Api()

_DELAY_RE = re.compile(r"^\d+(?:\.\d+)?(?:ns|us|ms|s)?$")
_LOSS_RE = re.compile(r"^\d+(?:\.\d+)?%$")


# ----------------------------------------------------------------------
# K8S 辅助
# ----------------------------------------------------------------------
def _running_pods(namespace, service):
    """返回目标服务中未处于删除中的 Running Pod。"""
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}")
    return [
        p for p in pods.items
        if p.metadata.deletion_timestamp is None and (p.status.phase or "") == "Running"
    ]


def _container_pid(container_id):
    """把 containerd/docker 的容器 ID 解析成宿主机 PID。"""
    cid = (container_id or "").split("://", 1)[-1].strip()
    if not cid:
        return None

    if shutil.which("k3s"):
        cmd = ["k3s", "crictl", "inspect", cid]
    elif shutil.which("crictl"):
        cmd = ["crictl", "inspect", cid]
    elif shutil.which("docker"):
        cmd = ["docker", "inspect", "-f", "{{.State.Pid}}", cid]
    else:
        return None

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        return int(data["info"]["pid"])
    except (ValueError, KeyError, TypeError):
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None


def _netns(pid, args, check=True):
    cmd = ["nsenter", "-t", str(pid), "-n"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=check)


def _iface(pid):
    """取 Pod 网络命名空间的默认路由网卡，失败时回退 eth0。"""
    try:
        r = _netns(pid, ["ip", "-o", "-4", "route", "show", "default"])
        m = re.search(r"\bdev\s+(\S+)", r.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "eth0"


def _tc_replace(pid, iface, delay, loss):
    args = ["tc", "qdisc", "replace", "dev", iface, "root", "netem"]
    if delay:
        args += ["delay", delay]
    if loss:
        args += ["loss", loss]
    _netns(pid, args)


def _tc_clear(pid, iface):
    try:
        _netns(pid, ["tc", "qdisc", "del", "dev", iface, "root"], check=False)
    except Exception:
        pass


def _save_netem(ctx, rules):
    run_dir = ctx.get("run_dir")
    if not run_dir:
        return
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "netem.json"), "w") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------
# Pod 级故障
# ----------------------------------------------------------------------
@register_action("fault.inject_pod_kill")
def inject_pod_kill(ctx, params):
    """删除目标服务的一个 Pod，模拟服务失效。"""
    service = params.get("service")
    namespace = ctx["config"].get("namespace", "sim-test")
    if not service:
        raise ValueError("fault.inject_pod_kill 需要 service 参数")

    inject_time = time.time()
    ctx["inject_time"] = inject_time
    ctx["timestamps"]["inject"] = inject_time
    ctx["fault_info"] = {"type": "pod_kill", "service": service, "time": inject_time}

    pods = _running_pods(namespace, service)
    if not pods:
        raise RuntimeError(f"service {service} 没有可删除的 Running Pod")

    pod_name = pods[0].metadata.name
    ctx["fault_info"]["pod_name"] = pod_name
    v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
    print(f"[FAULT] Deleted pod {pod_name} (service={service})")
    return {"status": "injected", "pod_name": pod_name, "service": service,
            "inject_time": inject_time}


# ----------------------------------------------------------------------
# 真实网络故障（tc netem）
# ----------------------------------------------------------------------
@register_action("fault.inject_netem")
def inject_netem(ctx, params):
    """对目标服务所有 Pod 注入真实 tc/netem 延迟/丢包。

    params:
      service: 必填
      delay:   例如 200ms
      loss:    例如 10%
    """
    service = params.get("service")
    delay = str(params.get("delay", "100ms"))
    loss = str(params.get("loss", "0%"))
    namespace = ctx["config"].get("namespace", "sim-test")
    if not service:
        raise ValueError("fault.inject_netem 需要 service 参数")
    if not _DELAY_RE.match(delay):
        raise ValueError(f"非法 delay: {delay}")
    if not _LOSS_RE.match(loss):
        raise ValueError(f"非法 loss: {loss}")

    pods = _running_pods(namespace, service)
    if not pods:
        raise RuntimeError(f"service {service} 没有可注入的 Running Pod")

    inject_time = time.time()
    ctx["inject_time"] = inject_time
    ctx["timestamps"]["inject"] = inject_time
    rules = []
    errors = []
    for pod in pods:
        for cs in pod.status.container_statuses or []:
            pid = _container_pid(cs.container_id)
            if not pid:
                errors.append(f"{pod.metadata.name}: 无法解析容器 PID")
                continue
            try:
                iface = _iface(pid)
                _tc_replace(pid, iface, delay, loss)
                rules.append({
                    "service": service, "pod": pod.metadata.name, "pid": pid,
                    "iface": iface, "delay": delay, "loss": loss,
                    "applied_at": inject_time,
                })
                print(f"[FAULT] netem on {pod.metadata.name} ({iface}): delay={delay} loss={loss}")
            except Exception as e:
                errors.append(f"{pod.metadata.name}: {e}")

    if not rules:
        raise RuntimeError("netem 注入失败：" + "; ".join(errors))

    # 同一服务重复注入时，保留最新规则，避免 cleanup 重复/漏清
    old = [r for r in ctx.get("netem_rules", []) if r.get("service") != service]
    ctx["netem_rules"] = old + rules
    ctx["fault_info"] = {"type": "netem", "service": service, "delay": delay,
                         "loss": loss, "time": inject_time, "rules": rules}
    _save_netem(ctx, ctx["netem_rules"])
    return {"status": "injected", "type": "netem", "service": service,
            "delay": delay, "loss": loss, "pods": [r["pod"] for r in rules],
            "errors": errors}


@register_action("fault.clear_netem")
def clear_netem(ctx, params):
    """清理 netem 规则。

    优先使用 ctx 中记录的规则（支持断点续跑）；若没有，则按 service 参数
    重新发现 Pod 并清理。
    """
    rules = list(ctx.get("netem_rules") or [])
    service = params.get("service")
    cleared = 0

    if not rules and service:
        namespace = ctx["config"].get("namespace", "sim-test")
        for pod in _running_pods(namespace, service):
            for cs in pod.status.container_statuses or []:
                pid = _container_pid(cs.container_id)
                if pid:
                    iface = _iface(pid)
                    _tc_clear(pid, iface)
                    cleared += 1

    for rule in rules:
        try:
            _tc_clear(int(rule["pid"]), rule.get("iface", "eth0"))
            cleared += 1
        except Exception as e:
            print(f"[FAULT] clear netem failed for {rule.get('pod')}: {e}")

    ctx["netem_rules"] = []
    _save_netem(ctx, [])
    print(f"[FAULT] netem cleared on {cleared} container(s)")
    return {"status": "cleared", "containers": cleared}


# ----------------------------------------------------------------------
# K8S NetworkPolicy 网络分区（不需要宿主机权限）
# ----------------------------------------------------------------------
def _policy_name(service):
    return "sim-fault-" + re.sub(r"[^a-z0-9-]", "-", service.lower())


@register_action("fault.inject_network_partition")
def inject_network_partition(ctx, params):
    """用 NetworkPolicy 对目标服务做入站/出站隔离。

    params:
      service: 必填
      direction: ingress | egress | both（默认 both）
    """
    service = params.get("service")
    direction = params.get("direction", "both")
    namespace = ctx["config"].get("namespace", "sim-test")
    if not service:
        raise ValueError("fault.inject_network_partition 需要 service 参数")
    if direction not in ("ingress", "egress", "both"):
        raise ValueError("direction 只能是 ingress/egress/both")

    policy_types = []
    ingress = None
    egress = None
    if direction in ("ingress", "both"):
        policy_types.append("Ingress")
        ingress = []  # 空规则 = 拒绝所有入站
    if direction in ("egress", "both"):
        policy_types.append("Egress")
        egress = []

    body = client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(
            name=_policy_name(service),
            labels={"sim/fault": "network-partition", "sim/service": service},
        ),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels={"app": service}),
            policy_types=policy_types,
            ingress=ingress,
            egress=egress,
        ),
    )
    try:
        networking.create_namespaced_network_policy(namespace, body)
    except client.exceptions.ApiException as e:
        if e.status != 409:  # 已存在则替换
            raise
        networking.replace_namespaced_network_policy(_policy_name(service), namespace, body)

    now = time.time()
    ctx["inject_time"] = now
    ctx["timestamps"]["inject"] = now
    ctx["fault_info"] = {"type": "network_partition", "service": service,
                         "direction": direction, "time": now,
                         "policy": _policy_name(service)}
    print(f"[FAULT] NetworkPolicy {_policy_name(service)} applied ({direction})")
    return {"status": "injected", "type": "network_partition", "service": service,
            "direction": direction, "policy": _policy_name(service)}


@register_action("fault.clear_network_partition")
def clear_network_partition(ctx, params):
    """删除 NetworkPolicy 分区规则。"""
    service = params.get("service") or (ctx.get("fault_info") or {}).get("service")
    namespace = ctx["config"].get("namespace", "sim-test")
    if not service:
        raise ValueError("fault.clear_network_partition 需要 service 参数")
    name = _policy_name(service)
    try:
        networking.delete_namespaced_network_policy(name, namespace)
        print(f"[FAULT] NetworkPolicy {name} deleted")
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise
    return {"status": "cleared", "policy": name}
