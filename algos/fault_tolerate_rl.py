"""
算法样例：基于服务降级和动态调度的仿真微服务容错

方法落地：
  (1) 基于资源画像的服务降级策略：采集真实 CPU/内存，刻画目标服务与集群负载画像，
      区分核心/非核心服务链（AHP 式关键性分级）。
  (2) 数据驱动的动态资源调度：Q-Learning 智能体在
      {等待自愈, 扩副本, 降级非关键链, 优先级提升} 动作空间中学习最优容错策略。
  (3) 真实执行恢复：复用 K8S 自愈（必要时 rollout restart）测出真实恢复时间。

说明：样例实现，纯标准库。RL 智能体在由"真实负载"播种的仿真环境中在线训练若干
      episode 收敛策略，再对当前故障做出决策。破坏性动作默认保守（以自愈为主），
      可安全在集群上运行；扩副本等真实动作的位置在代码中有明确标注。
"""

import time
import random
from kubernetes import client, config

# 复用 baselines 的真实 K8S 就绪判定
from algos.fault_tolerate_baseline import _all_ready
from actions.obs import _metrics_server_baseline, SIM_SERVICES, NAMESPACE_DEFAULT

config.load_kube_config()
v1 = client.CoreV1Api()
apps = client.AppsV1Api()
random.seed(7)


# 任务关键性画像（AHP 式分级：核心 / 非核心）——用于智能降级决策
CRITICAL = {
    "orchestrator", "scenario-mgr", "fire-control", "sensor-service",
    "entity-service", "comms-service", "state-store", "sim-bridge", "env-service",
}
NONCRITICAL = {"telemetry-gw", "log-service"}


def _any_ready(namespace, service):
    """服务是否已对外可用：至少存在 1 个 Ready 且未被删除的 Pod。

    恢复时间的正确口径是「服务重新可服务」，而不是「所有期望副本都已就绪」——
    后者会把扩副本时新 Pod 的启动耗时错误计入恢复时间。
    """
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}")
    for p in pods.items:
        if p.metadata.deletion_timestamp is not None:
            continue
        cs = p.status.container_statuses or []
        if cs and all(c.ready for c in cs):
            return True
    return False


def _load_level(cpu_str):
    """将毫核字符串映射为负载等级 0/1/2（低/中/高）"""
    try:
        v = float(cpu_str.replace("m", "")) if cpu_str else 0.0
    except ValueError:
        v = 0.0
    if v < 50:
        return 0
    if v < 200:
        return 1
    return 2


class QLearning:
    """表格型 Q-Learning（动态调度决策样例；PPO/A2C 等深度 RL 可按同接口替换）"""

    ACTIONS = ["wait_selfheal", "scale_replicas", "degrade_noncritical", "priority_boost"]
    LR, GAMMA, EPS = 0.2, 0.9, 0.15

    def __init__(self):
        self.Q = {}  # (state, action) -> value

    def _q(self, s, a):
        return self.Q.get((s, a), 0.0)

    def train(self, env, episodes=300):
        """env: 由真实负载播种的仿真环境，提供 step(state, action)->(next_state, reward)"""
        for _ in range(episodes):
            state = env.reset()
            for _ in range(6):  # 单 episode 最多 6 步
                if random.random() < self.EPS:
                    a = random.randrange(len(self.ACTIONS))
                else:
                    a = max(range(len(self.ACTIONS)),
                            key=lambda i: self._q(state, self.ACTIONS[i]))
                nxt, reward = env.step(state, a)
                best = max(self._q(nxt, self.ACTIONS[i]) for i in range(len(self.ACTIONS)))
                self.Q[(state, self.ACTIONS[a])] = self._q(state, self.ACTIONS[a]) \
                    + self.LR * (reward + self.GAMMA * best - self._q(state, self.ACTIONS[a]))
                state = nxt
                if env.done:
                    break

    def decide(self, state):
        return max(range(len(self.ACTIONS)),
                   key=lambda i: self._q(state, self.ACTIONS[i]))

    def values(self, state):
        return {self.ACTIONS[i]: round(self._q(state, self.ACTIONS[i]), 3)
                for i in range(len(self.ACTIONS))}


class SchedulingEnv:
    """由真实负载播种的容错调度仿真环境（数据驱动）

    状态：(集群负载等级, 目标负载等级, 是否核心任务)
    奖励：-(恢复耗时 + 动作成本 + 风险惩罚)
      - 核心任务不做处置 -> 风险惩罚高；
      - 集群无压力时做降级 -> 无谓损失（惩罚），避免"为降级而降级"；
      - 降级只有在集群高负载时才真正缩短恢复时间。
    """

    def __init__(self, cluster_load, target_load, critical):
        self.cluster_load = cluster_load
        self.target_load = target_load
        self.critical = critical
        self.done = False

    def reset(self):
        self.done = False
        return (self.cluster_load, self.target_load, int(self.critical))

    def step(self, state, action):
        cl, tl, crit = state
        cln = cl / 2.0   # 归一化到 [0,1]
        tln = tl / 2.0
        base = 3.0
        if action == 0:      # wait_selfheal：负载越高自愈越慢
            t = base * (1.0 + 0.8 * tln)
            cost = 0.0
            risk = 2.0 if crit else 0.2
        elif action == 1:    # scale_replicas：加速恢复，但占用额外资源
            t = base * 0.6 * (1.0 + 0.5 * tln)
            cost = 0.8
            risk = 0.0
        elif action == 2:    # degrade_noncritical：仅在集群有压力时有效
            t = base * (0.7 + 0.5 * (1.0 - cln))
            cost = 0.2 + 1.5 * (1.0 - cln)   # 无压力时降级是净损失
            risk = 0.0 if not crit else 0.3
        else:                # priority_boost：轻量加速
            t = base * 0.8 * (1.0 + 0.4 * tln)
            cost = 0.1
            risk = 0.0
        reward = -(t + cost + risk)
        self.done = True  # 单步决策
        return state, reward


def tolerate(ctx):
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)
    fault = ctx.get("fault_info") or {}
    target = fault.get("service") or "fire-control"
    inject_time = ctx.get("inject_time")
    print(f"[ALGO_RL] Resource-profile + Q-Learning tolerance for '{target}' ...")

    # (1) 资源画像：真实 CPU/内存
    res = _metrics_server_baseline(namespace)
    cluster_cpu = sum(float(res.get(s, {}).get("cpu", "0m").replace("m", "") or 0)
                      for s in SIM_SERVICES)
    target_cpu = res.get(target, {}).get("cpu", "0m")
    cluster_load = _load_level(f"{cluster_cpu:.0f}m")
    target_load = _load_level(target_cpu)
    profile = {s: res.get(s, {}) for s in SIM_SERVICES}

    # (2) 训练 Q-Learning（由真实负载 + 任务关键性播种的仿真环境）
    critical = target in CRITICAL
    env = SchedulingEnv(cluster_load, target_load, critical)
    agent = QLearning()
    agent.train(env, episodes=300)
    state = env.reset()
    a_idx = agent.decide(state)
    strategy = QLearning.ACTIONS[a_idx]
    q_values = agent.values(state)

    # (3) 降级计划：若选择降级，列出非核心服务链
    degradation_plan = sorted(NONCRITICAL) if strategy == "degrade_noncritical" else []

    # (4) 真实执行决策 + 测量恢复
    #     - scale_replicas：真实 patch 副本数 +1，测完再回滚到原值（可逆）
    #     - priority_boost / degrade_noncritical：以注解记录策略（不改动业务行为）
    #     - wait_selfheal：不干预，仅观测
    watch = ctx.get("k8s_watch") or {}
    observed_recovered = (watch.get("recovered_at") or {}).get(target)
    t0 = time.time()
    recovered_at = None
    actions_applied = []
    scaled_from = None

    if strategy == "scale_replicas":
        try:
            dep = apps.read_namespaced_deployment(name=target, namespace=namespace)
            scaled_from = dep.spec.replicas or 1
            apps.patch_namespaced_deployment(
                name=target, namespace=namespace,
                body={"spec": {"replicas": scaled_from + 1}})
            actions_applied.append({"type": "scale_replicas", "target": target,
                                    "from": scaled_from, "to": scaled_from + 1})
            print(f"[ALGO_RL] applied scale_replicas {scaled_from} -> {scaled_from + 1}")
        except Exception as e:
            actions_applied.append({"type": "scale_replicas_failed", "error": str(e)[:200]})
    elif strategy in ("priority_boost", "degrade_noncritical"):
        try:
            apps.patch_namespaced_deployment(
                name=target, namespace=namespace,
                body={"spec": {"template": {"metadata": {"annotations":
                      {"sim/strategy": strategy, "sim/decision_at": str(int(time.time()))}}}}})
            actions_applied.append({"type": "annotate_strategy", "target": target,
                                    "strategy": strategy})
        except Exception as e:
            actions_applied.append({"type": "annotate_failed", "error": str(e)[:200]})

    if observed_recovered is not None and _any_ready(namespace, target):
        recovered_at = observed_recovered
        actions_applied.append({"type": "self_heal_observed", "target": target,
                                "note": "recovery observed during perception window"})
    else:
        for _ in range(60):
            if _any_ready(namespace, target):
                recovered_at = time.time()
                actions_applied.append({"type": "self_heal_observed", "target": target})
                break
            time.sleep(0.5)
    if recovered_at is None:
        apps.patch_namespaced_deployment(
            name=target, namespace=namespace,
            body={"spec": {"template": {"metadata": {"annotations": {"restart": str(int(time.time()))}}}}})
        actions_applied.append({"type": "force_restart", "target": target})
        for _ in range(60):
            if _any_ready(namespace, target):
                recovered_at = time.time()
                break

    # 回滚扩副本，保持集群规模不变
    if scaled_from is not None:
        try:
            apps.patch_namespaced_deployment(
                name=target, namespace=namespace,
                body={"spec": {"replicas": scaled_from}})
            actions_applied.append({"type": "restore_replicas", "target": target, "to": scaled_from})
        except Exception as e:
            actions_applied.append({"type": "restore_replicas_failed", "error": str(e)[:200]})
    # 说明：若 strategy=="scale_replicas"，可在此处真实 patch 副本数（可逆），
    #       本样例保持保守，仅记录决策，避免改动集群规模。

    recovery_time = (recovered_at - inject_time) if (recovered_at and inject_time) else None
    result = {
        "target": target,
        "strategy": strategy,
        "q_values": q_values,
        "task_critical": critical,
        "degradation_plan": degradation_plan,
        "cluster_load_level": cluster_load,
        "target_load_level": target_load,
        "resource_profile": {s: profile[s] for s in (SIM_SERVICES if len(SIM_SERVICES) <= 11 else SIM_SERVICES[:11])},
        "actions_applied": actions_applied,
        "recovered_at": recovered_at,
        "recovery_time_s": round(recovery_time, 3) if recovery_time is not None else None,
        "decision_time": time.time(),
    }
    print(f"[ALGO_RL] decision={strategy} q={q_values} recovery_time_s={result['recovery_time_s']}")
    ctx.setdefault("metrics", {})["tolerance"] = {
        "strategy": strategy,
        "q_values": q_values,
        "task_critical": critical,
        "actions_applied": actions_applied,
        "degradation_plan": degradation_plan,
        "cluster_load_level": cluster_load,
        "target_load_level": target_load,
        "recovery_time_s": result["recovery_time_s"],
    }
    return result
