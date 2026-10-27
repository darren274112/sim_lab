"""
研究内容三 算法样例：基于模糊-排队协同决策的仿真微服务状态一致性维护
================================================================
对应课题：基于模糊-排队协同决策的仿真微服务状态一致性同步方法

方法落地（与课题研究方案逐条对应）：
  (1) 系统状态监控：真实采集节点负载(metrics-server) 与网络延迟(RTT 探针)；
  (2) 事件触发机制：有限状态机侦测 网络分区 / 节点故障 / 数据更新突增 三类事件；
  (3) 动态参数调整：模糊控制(延迟/负载 -> 同步频率/超时/一致性级别) 与
      排队论(M/M/1 估算同步等待) 协同，自适应调整一致性协议参数。

说明：样例实现，纯标准库。模糊推理采用三角形隶属度 + min-max 推理 + 加权平均去模糊；
     可在此接口上替换为真实 Raft/Paxos 参数调节或接入 ETCD/DHT 监控。
"""

import time
import math
import subprocess
from kubernetes import client, config

from actions.obs import (_metrics_server_baseline, _prom_query, _prom_rates_by_instance,
                         SIM_SERVICES, NAMESPACE_DEFAULT)

config.load_kube_config()
v1 = client.CoreV1Api()


# ---------------- 真实监控 ----------------
def _avg_cluster_cpu(namespace):
    res = _metrics_server_baseline(namespace)
    vals = []
    for s in SIM_SERVICES:
        c = res.get(s, {}).get("cpu", "0m")
        try:
            vals.append(float(c.replace("m", "") or 0))
        except ValueError:
            pass
    return sum(vals) / max(1, len(vals))  # 毫核


def _probe_rtt(namespace, svc="state-store"):
    """真实 RTT 探针：在集群内对目标服务 healthz 计时（数据面往返时延）。

    弱网（延迟 + 丢包）下单次请求可能超时，因此连续探测 3 次取最小值；
    全部失败才回退到 0.05s，并通过 rtt_source 明确标注，避免把回退值
    当成真实测量结果写进决策依据。
    """
    try:
        script = ("import urllib.request,time\n"
                  "vals=[]\n"
                  "for _ in range(3):\n"
                  "    t=time.time()\n"
                  "    try:\n"
                  f"        urllib.request.urlopen('http://{svc}:8080/healthz',timeout=2).read()\n"
                  "        vals.append(time.time()-t)\n"
                  "    except Exception:\n"
                  "        pass\n"
                  "print(' '.join('%.4f' % v for v in vals) or 'none')\n")
        r = subprocess.run(
            ["kubectl", "-n", namespace, "exec", "-i", "deploy/orchestrator", "--", "python", "-"],
            input=script, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return 0.05, [], "fallback"
        line = (r.stdout.strip().splitlines() or ["none"])[-1]
        samples = [float(x) for x in line.split() if x != "none"]
        if samples:
            return min(samples), samples, "probe"
        return 0.05, [], "fallback"
    except Exception:
        return 0.05, [], "fallback"


def _total_request_rate(namespace):
    """总数据更新率（单次批量 Prometheus 查询，避免逐服务 exec）"""
    rates = _prom_rates_by_instance(namespace)
    return float(sum(rates.values()))


# ---------------- 模糊控制 ----------------
def _tri(x, a, b, c):
    """三角形隶属度"""
    if x <= a or x >= c:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    return (c - x) / (c - b)


def _fuzzy_consistency(delay_norm, load_norm):
    """输入(延迟,负载)归一化 -> (sync_interval_s, consistency_strength, timeout_ms)"""
    # 隶属度
    d_lo, d_md, d_hi = _tri(delay_norm, 0, 0.2, 0.5), _tri(delay_norm, 0.2, 0.5, 0.8), _tri(delay_norm, 0.5, 0.8, 1.0)
    l_lo, l_md, l_hi = _tri(load_norm, 0, 0.2, 0.5), _tri(load_norm, 0.2, 0.5, 0.8), _tri(load_norm, 0.5, 0.8, 1.0)

    rules = [
        # (delay_mf, load_mf, sync_interval_s, consistency_strength, timeout_ms)
        # 完整 3x3 规则表：同步间隔随「延迟↑ / 负载↑」单调增大，一致性强度单调减弱
        (d_lo, l_lo, 0.05, 0.92, 50),    # 低延迟低负载 -> 高频同步、强一致
        (d_lo, l_md, 0.10, 0.80, 80),    # 低延迟中负载 -> 较高频、较强一致
        (d_lo, l_hi, 0.20, 0.65, 120),   # 低延迟高负载 -> 中频、均衡
        (d_md, l_lo, 0.10, 0.80, 100),   # 中延迟低负载 -> 较高频、较强一致
        (d_md, l_md, 0.30, 0.55, 150),   # 中中 -> 默认
        (d_md, l_hi, 0.50, 0.45, 250),   # 中延迟高负载 -> 低频、偏弱一致
        (d_hi, l_lo, 0.30, 0.50, 300),   # 高延迟低负载 -> 中低频、均衡、高超时
        (d_hi, l_md, 0.60, 0.40, 380),   # 高延迟中负载 -> 低频、弱一致
        (d_hi, l_hi, 1.00, 0.30, 450),   # 高延迟高负载 -> 最低频、最弱一致
    ]
    num_i = num_c = num_t = den = 0.0
    for dmf, lmf, si, cs, to in rules:
        fire = min(dmf, lmf)
        if fire <= 0:
            continue
        num_i += fire * si
        num_c += fire * cs
        num_t += fire * to
        den += fire
    if den <= 0:
        return 0.3, 0.55, 150.0
    return num_i / den, num_c / den, num_t / den


# ---------------- 排队论 M/M/1 ----------------
def _mm1(arrival, service):
    if service <= arrival:
        return {"rho": 1.0, "wait_s": float("inf"), "stable": False}
    rho = arrival / service
    wait = rho / (service - arrival)  # 系统中平均等待时间
    return {"rho": round(rho, 3), "wait_s": round(wait, 4), "stable": True}


# ---------------- 决策曲面（多工况，用于验证自适应行为） ----------------
def _sweep_operating_points():
    """在 (延迟, 负载) 归一化平面上 3×3 取样，输出模糊-排队协同的决策。

    作用：即使实时集群处于健康状态，也能验证"延迟升高 -> 放松非关键一致性、
    负载升高 -> 降低同步频率"的自适应行为是否按预期工作（可复现）。
    """
    rows = []
    for d in (0.1, 0.5, 0.9):
        for l in (0.1, 0.5, 0.9):
            si, cs, to = _fuzzy_consistency(d, l)
            service_rate = max(5.0, 50.0 * (1.0 - l))
            q = _mm1(min(20.0, service_rate * 0.9), service_rate)
            level = "strong" if cs > 0.7 else ("balanced" if cs > 0.4 else "eventual")
            rows.append({
                "delay_norm": d, "load_norm": l,
                "sync_interval_s": round(si, 3),
                "consistency_level": level,
                "consistency_strength": round(cs, 3),
                "timeout_ms": round(to, 1),
                "mm1_wait_s": q.get("wait_s"),
            })
    return rows


# ---------------- 事件触发状态机 ----------------
def _event_triggers(namespace, delay_norm, load_norm, req_rate):
    events = []
    down = set()
    if delay_norm > 0.7:
        events.append("network_partition_suspected")
    # 节点故障：一次 list 全部 Pod 后按服务聚合（避免逐服务调用）
    try:
        pods = v1.list_namespaced_pod(namespace=namespace)
        down = set()
        for p in pods.items:
            svc = (p.metadata.labels or {}).get("app")
            cs = p.status.container_statuses or []
            if svc and cs and not all(c.ready for c in cs):
                down.add(svc)
        if down:
            events.append("node_fault")
    except Exception:
        down = set()
    if req_rate > 1.0:
        events.append("data_update_burst")
    return events, sorted(down)


def consist(ctx):
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)
    t0 = time.time()
    print("[ALGO_FUZZY] Monitoring + fuzzy-queueing consistency adaptation ...")

    # (1) 监控
    avg_cpu = _avg_cluster_cpu(namespace)
    rtt, rtt_samples, rtt_source = _probe_rtt(namespace)
    req_rate = _total_request_rate(namespace)

    delay_norm = min(1.0, rtt / 0.2)
    load_norm = min(1.0, avg_cpu / 300.0)

    # (2) 事件触发
    events, down_services = _event_triggers(namespace, delay_norm, load_norm, req_rate)
    # 真实副本分叉也是事件：来自 state.verify 的实际快照对比，而不是推测
    state_verify = ctx.get("state_verify") or {}
    divergent_keys = state_verify.get("divergent_keys") or []
    if divergent_keys:
        events.append("state_divergence")
    state = "TRIGGERED" if events else "NORMAL"

    # (3) 模糊-排队协同调参
    sync_interval, consistency_strength, timeout_ms = _fuzzy_consistency(delay_norm, load_norm)
    base_params = {"sync_interval_s": round(sync_interval, 3),
                   "consistency_strength": round(consistency_strength, 3),
                   "timeout_ms": round(timeout_ms, 1)}

    # (3b) 事件驱动的参数调整：事件发生时不再"更宽松"，而是收紧一致性
    #      逻辑依据：节点故障/分区期间，有状态服务的副本可能分叉，必须提高同步频率
    #      保证关键数据一致；数据更新突增时缩短同步周期以降低落后时间。
    event_adjustments = []
    if "node_fault" in events:
        if consistency_strength < 0.85:
            consistency_strength = 0.85
            event_adjustments.append("node_fault -> consistency_strength raised to 0.85")
        sync_interval = min(sync_interval, 0.10)
        event_adjustments.append("node_fault -> sync_interval capped at 0.10s")
        timeout_ms = max(timeout_ms, 200.0)
    if "network_partition_suspected" in events:
        timeout_ms = max(timeout_ms, 300.0)
        event_adjustments.append("network_partition -> timeout raised to >=300ms")
    if "data_update_burst" in events:
        sync_interval = min(sync_interval, 0.20)
        event_adjustments.append("data_update_burst -> sync_interval capped at 0.20s")
    if "state_divergence" in events:
        # 已观测到副本分叉：立即收紧同步间隔并提高一致性强度，驱动反熵修复
        sync_interval = min(sync_interval, 0.10)
        consistency_strength = max(consistency_strength, 0.85)
        timeout_ms = max(timeout_ms, 250.0)
        event_adjustments.append(
            f"state_divergence({','.join(divergent_keys)}) -> sync<=0.10s, strength>=0.85")

    # 排队论：到达率=数据更新频率(近似请求率)，服务率=同步容量(随可用 CPU 提升)
    service_rate = max(5.0, 50.0 * (1.0 - load_norm))
    q = _mm1(min(req_rate, service_rate * 0.95), service_rate)
    # 协同：排队等待高时进一步降低非关键同步频率（仅在无故障事件时放宽）
    if q.get("wait_s", 0) and q["wait_s"] > 0.1 and not events:
        sync_interval = min(2.0, sync_interval * 1.5)
        event_adjustments.append("mm1_wait>0.1s -> sync_interval x1.5")

    level = "strong" if consistency_strength > 0.7 else ("balanced" if consistency_strength > 0.4 else "eventual")
    surface = _sweep_operating_points()

    result = {
        "state": state,
        "triggered_events": events,
        "monitor": {
            "avg_cluster_cpu_m": round(avg_cpu, 1),
            "rtt_s": round(rtt, 4),
            "rtt_source": rtt_source,
            "rtt_samples_s": [round(v, 4) for v in rtt_samples],
            "total_request_rate": round(req_rate, 4),
            "delay_norm": round(delay_norm, 3),
            "load_norm": round(load_norm, 3),
        },
        "fuzzy": {
            "sync_interval_s": round(sync_interval, 3),
            "consistency_strength": round(consistency_strength, 3),
            "timeout_ms": round(timeout_ms, 1),
            "consistency_level": level,
        },
        "queue_mm1": q,
        "fuzzy_response_surface": surface,
        "base_fuzzy_params": base_params,
        "event_adjustments": event_adjustments,
        "faulty_services": down_services,
        "state_consistency": {
            "observed_consistent": state_verify.get("consistent"),
            "divergent_keys": divergent_keys,
            "replicas": len(state_verify.get("replicas") or []),
        },
        "decision_time": time.time(),
        "adapt_duration_s": round(time.time() - t0, 3),
    }
    print(f"[ALGO_FUZZY] state={state} level={level} sync_interval={sync_interval:.3f}s "
          f"events={events} mm1_wait={q.get('wait_s')} rtt={rtt:.4f}s "
          f"source={rtt_source} cpu_avg={avg_cpu:.0f}m")
    if rtt_source != "probe":
        print("[ALGO_FUZZY] WARNING: RTT 探测全部失败，使用回退值 0.05s（结果中 rtt_source=fallback）")
    if event_adjustments:
        print(f"[ALGO_FUZZY] 事件驱动调整: {event_adjustments}")
    print("[ALGO_FUZZY] 决策曲面 (delay,load)->interval/level:")
    for r in surface:
        print(f"    d={r['delay_norm']:.1f} l={r['load_norm']:.1f} -> "
              f"{r['sync_interval_s']:.2f}s {r['consistency_level']:<8} "
              f"timeout={r['timeout_ms']:.0f}ms mm1_wait={r['mm1_wait_s']}")
    ctx.setdefault("metrics", {})["consistency"] = {
        "state": state,
        "triggered_events": events,
        "consistency_level": level,
        "sync_interval_s": round(sync_interval, 3),
        "consistency_strength": round(consistency_strength, 3),
        "event_adjustments": event_adjustments,
        "mm1_wait_s": q.get("wait_s"),
        "observed_divergent_keys": divergent_keys,
        "observed_consistent": state_verify.get("consistent"),
    }
    return result
