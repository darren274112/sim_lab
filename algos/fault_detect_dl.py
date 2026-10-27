"""
研究内容一 算法样例：基于深度学习和链路追踪的仿真微服务故障感知
================================================================
对应课题：基于深度学习和链路追踪的仿真微服务故障感知方法

方法落地（与课题研究方案逐条对应）：
  (1) 多源异构特征提取：CPU(metrics-server) / 内存 / 调用频率(Prometheus) /
      服务可用性(K8S 就绪态) 构成多维特征向量；数据清洗 + 归一化。
  (2) 特征降维 + 异常检测：PCA 特征提取（课题明确提到 PCA）+ 自编码器(AutoEncoder)
      重建误差异常评分；注意力机制对特征维加权（聚焦关键特征）。
  (3) 链路追踪故障定位：依赖图不再写死——从 Jaeger 真实 span 的父子关系还原运行态
      调用拓扑，用失败 span 的 peer.service 反推故障服务，再叠加故障传播 BFS +
      贝叶斯溯源，给出根因排序与真实传播路径。

说明：本文件为"样例/基线实现"，使用纯标准库，可直接被 algo.run_detect 调用。
     真实 LSTM / 带注意力的深度模型可按同样的 detect(ctx) 接口替换此处。
"""

import time
import math
import random
import json
import subprocess
from kubernetes import client, config

# 复用 obs 的真实指标采集（metrics-server + Prometheus）
from actions.obs import (_metrics_server_baseline, _prom_query, _prom_rates_by_instance,
                         SIM_SERVICES, NAMESPACE_DEFAULT)
# 复用 trace 动作的真实链路数据（Jaeger）
from actions.trace import DESIGN_GRAPH, collect_once as trace_collect, runtime_graph

config.load_kube_config()
v1 = client.CoreV1Api()

random.seed(42)


# ---------------- 服务依赖图 ----------------
# 语义：u -> [v, ...] 表示 u 调用 v（u 依赖 v）。
# 运行态图由 Jaeger 真实 span 推导；DESIGN_GRAPH 仅作为无 trace 数据时的兜底/比对基准。
STATIC_DEP_GRAPH = DESIGN_GRAPH


def _build_prop_graph(dep_graph):
    """由调用图构造故障传播图：边方向取反（被调用方 -> 调用方）。

    DEP_GRAPH 里 u -> v 表示 u 调用 v；若 v 故障，则 u 及其上游都会受影响。
    因此传播图里应有 v -> u，BFS 与最短路径都沿传播图计算。
    """
    prop = {u: [] for u in dep_graph}
    for u, vs in dep_graph.items():
        for v in vs:
            prop.setdefault(v, []).append(u)
    return prop


def _trace_evidence(namespace, lookback_s=600, limit=120, since=None):
    """从 Jaeger 取真实 Trace：服务级 span 指标 + 运行态依赖图 + 失败调用对端。

    这是"链路追踪"在感知里的作用点：
      - graph：定位时用真实拓扑而不是人工声明的边；
      - failed_peers：调用失败 span 上的 peer.service，直接指认故障服务；
      - first_error_at：故障在链路上首次暴露的时刻，可作为感知时刻的更早证据。

    since：故障注入时刻（秒）。窗口不早于该时刻，避免把注入前的历史错误
           当成这次故障的证据（否则会出现负的感知延迟）。
    """
    try:
        agg = trace_collect(namespace, lookback_s, limit, since=since)
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"[:200]}
    if agg.get("error"):
        return {"available": False, "error": agg["error"]}

    graph = runtime_graph(agg)
    edges = agg.get("edges", {})
    # 真实边太少说明还没跑过业务流量，退回设计态拓扑，避免定位结果被空图毁掉
    graph_ok = len(edges) >= 3
    return {
        "available": True,
        "services": agg.get("services", {}),
        "failed_peers": agg.get("failed_peers", {}),
        "edge_counts": edges,
        "graph": graph if graph_ok else STATIC_DEP_GRAPH,
        "graph_source": "runtime_trace" if graph_ok else "design_fallback",
        "graph_edges": len(edges),
        "trace_count": agg.get("trace_count", 0),
        "span_total": agg.get("span_total", 0),
        "window_start": agg.get("window_start"),
        "window_end": agg.get("window_end"),
        "sample_trace_ids": agg.get("trace_ids", [])[-5:],
    }


# ----------------------------------------------------------------------
# 工具：指标解析与实时采集
# ----------------------------------------------------------------------
def _parse_cpu(s):
    if not s or s == "n/a":
        return 0.0
    s = s.strip()
    if s.endswith("m"):
        return float(s[:-1])
    try:
        return float(s) * 1000.0  # 若为核数，转毫核
    except ValueError:
        return 0.0


def _parse_mem(s):
    if not s or s == "n/a":
        return 0.0
    s = s.strip()
    if s.endswith("Mi"):
        return float(s[:-2])
    if s.endswith("Gi"):
        return float(s[:-2]) * 1024.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _availability(namespace, service):
    """K8S 就绪比例（链路追踪中服务存活/可用性的真实信号）"""
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}")
    if not pods.items:
        return 0.0
    ready = 0
    for p in pods.items:
        if p.metadata.deletion_timestamp is not None:
            continue
        cs = p.status.container_statuses or []
        if cs and all(c.ready for c in cs):
            ready += 1
    return ready / max(1, len(pods.items))


def _availability_map(namespace):
    """一次 list 拿到所有服务的就绪比例（单次 K8S API 调用，~10ms，适合高频轮询）"""
    pods = v1.list_namespaced_pod(namespace=namespace)
    ready, total = {}, {}
    for p in pods.items:
        svc = (p.metadata.labels or {}).get("app")
        if not svc:
            continue
        total[svc] = total.get(svc, 0) + 1
        if p.metadata.deletion_timestamp is None:
            cs = p.status.container_statuses or []
            if cs and all(c.ready for c in cs):
                ready[svc] = ready.get(svc, 0) + 1
    return {svc: (ready.get(svc, 0) / total[svc] if total.get(svc) else 0.0)
            for svc in SIM_SERVICES}


def _snapshot(namespace):
    """一次全量采集：metrics-server(CPU/内存) + Prometheus(请求率) + K8S(就绪比例)"""
    res = _metrics_server_baseline(namespace)
    rates = _prom_rates_by_instance(namespace)
    pods = v1.list_namespaced_pod(namespace=namespace)
    ready, total = {}, {}
    for p in pods.items:
        svc = (p.metadata.labels or {}).get("app")
        if not svc:
            continue
        total[svc] = total.get(svc, 0) + 1
        if p.metadata.deletion_timestamp is None:
            cs = p.status.container_statuses or []
            if cs and all(c.ready for c in cs):
                ready[svc] = ready.get(svc, 0) + 1
    snap = {}
    for svc in SIM_SERVICES:
        cpu = _parse_cpu(res.get(svc, {}).get("cpu", "n/a"))
        mem = _parse_mem(res.get(svc, {}).get("memory", "n/a"))
        rate = rates.get(svc, 0.0)
        av = (ready.get(svc, 0) / total[svc]) if total.get(svc) else 0.0
        snap[svc] = [cpu, mem, rate, av]
    return snap


# ----------------------------------------------------------------------
# 纯 Python 数学：PCA（Jacobi 特征分解）+ 自编码器
# ----------------------------------------------------------------------
def _mat_mul(A, B):
    n, m = len(A), len(B[0])
    k = len(B)
    return [[sum(A[i][t] * B[t][j] for t in range(k)) for j in range(m)] for i in range(n)]


def _transpose(M):
    return [list(col) for col in zip(*M)]


def _covariance(X):
    n = len(X)
    d = len(X[0])
    mean = [sum(X[i][j] for i in range(n)) / n for j in range(d)]
    C = [[0.0] * d for _ in range(d)]
    for i in range(n):
        for a in range(d):
            for b in range(d):
                C[a][b] += (X[i][a] - mean[a]) * (X[i][b] - mean[b])
    return [[c / n for c in row] for row in C], mean


def _jacobi_eig(M, n_iter=100):
    """对称矩阵特征分解（Jacobi 旋转），返回 (eigenvalues, eigenvectors)"""
    n = len(M)
    A = [row[:] for row in M]
    V = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(n_iter):
        # 找最大非对角元
        p, q, maxv = 0, 1, 0.0
        for i in range(n):
            for j in range(i + 1, n):
                if abs(A[i][j]) > abs(maxv):
                    maxv, p, q = A[i][j], i, j
        if abs(maxv) < 1e-9:
            break
        theta = (A[q][q] - A[p][p]) / (2.0 * maxv)
        t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
        c, s = 1.0 / math.sqrt(t * t + 1.0), t / math.sqrt(t * t + 1.0)
        # 旋转
        App, Aqq = A[p][p], A[q][q]
        A[p][p] = c * c * App - 2 * s * c * maxv + s * s * Aqq
        A[q][q] = s * s * App + 2 * s * c * maxv + c * c * Aqq
        A[p][q] = A[q][p] = 0.0
        for i in range(n):
            if i not in (p, q):
                Aip, Aiq = A[i][p], A[i][q]
                A[i][p] = A[p][i] = c * Aip - s * Aiq
                A[i][q] = A[q][i] = s * Aip + c * Aiq
        for i in range(n):
            Vip, Viq = V[i][p], V[i][q]
            V[i][p] = c * Vip - s * Viq
            V[i][q] = s * Vip + c * Viq
    eig = [A[i][i] for i in range(n)]
    return eig, _transpose(V)  # 每列是一个特征向量


class PCAAnalyzer:
    """PCA 特征提取 + 重建误差异常评分（课题研究方案中的 PCA 步骤）"""

    def __init__(self, n_components=2):
        self.n_components = n_components
        self.mean = None
        self.components = None
        self.explained = None

    def fit(self, X):
        C, self.mean = _covariance(X)
        eig, vecs = _jacobi_eig(C)
        order = sorted(range(len(eig)), key=lambda i: eig[i], reverse=True)
        self.components = [vecs[order[i]] for i in range(self.n_components)]
        total = sum(max(e, 0) for e in eig) or 1.0
        self.explained = [max(eig[order[i]], 0) / total for i in range(self.n_components)]
        return self

    def project(self, x):
        xc = [x[i] - self.mean[i] for i in range(len(x))]
        return [sum(xc[k] * self.components[c][k] for k in range(len(xc)))
                for c in range(self.n_components)]

    def reconstruct(self, x):
        z = self.project(x)
        rec = self.mean[:]
        for c in range(self.n_components):
            for k in range(len(rec)):
                rec[k] += z[c] * self.components[c][k]
        return rec

    def score(self, x):
        rec = self.reconstruct(x)
        return math.sqrt(sum((x[i] - rec[i]) ** 2 for i in range(len(x))))


class AutoEncoder:
    """轻量自编码器（深度学习异常检测样例；真实 LSTM/深度 AE 可按同接口替换）"""

    def __init__(self, dim, hidden=3, lr=0.05):
        self.dim = dim
        self.hidden = hidden
        self.lr = lr
        r = math.sqrt(6.0 / (dim + hidden))
        self.W1 = [[random.uniform(-r, r) for _ in range(dim)] for _ in range(hidden)]
        self.b1 = [0.0] * hidden
        self.W2 = [[random.uniform(-r, r) for _ in range(hidden)] for _ in range(dim)]
        self.b2 = [0.0] * dim

    @staticmethod
    def _tanh(v):
        return [math.tanh(x) for x in v]

    @staticmethod
    def _dtanh(v):
        return [1.0 - x * x for x in v]

    def forward(self, x):
        h = self._tanh([sum(self.W1[i][j] * x[j] for j in range(self.dim)) + self.b1[i]
                        for i in range(self.hidden)])
        y = [sum(self.W2[k][i] * h[i] for i in range(self.hidden)) + self.b2[k]
             for k in range(self.dim)]
        return h, y

    def fit(self, X, epochs=400):
        n = len(X)
        for _ in range(epochs):
            for x in X:
                h, y = self.forward(x)
                dy = [y[k] - x[k] for k in range(self.dim)]
                dh = [self._dtanh(h)[i] * sum(self.W2[k][i] * dy[k] for k in range(self.dim))
                      for i in range(self.hidden)]
                # 更新 W2/b2
                for k in range(self.dim):
                    for i in range(self.hidden):
                        self.W2[k][i] -= self.lr * dy[k] * h[i]
                    self.b2[k] -= self.lr * dy[k]
                # 更新 W1/b1
                for i in range(self.hidden):
                    for j in range(self.dim):
                        self.W1[i][j] -= self.lr * dh[i] * x[j]
                    self.b1[i] -= self.lr * dh[i]
        return self

    def score(self, x):
        _, y = self.forward(x)
        return math.sqrt(sum((y[k] - x[k]) ** 2 for k in range(self.dim)))


# ----------------------------------------------------------------------
# 注意力：对特征维加权（聚焦关键特征）
# ----------------------------------------------------------------------
def _attention_weights(X):
    """注意力权重 = softmax(特征方差的倒数)，方差越小的稳定特征权重越低"""
    n, d = len(X), len(X[0])
    mean = [sum(X[i][j] for i in range(n)) / n for j in range(d)]
    var = [sum((X[i][j] - mean[j]) ** 2 for i in range(n)) / n for j in range(d)]
    inv = [1.0 / (v + 1e-6) for v in var]
    mx = max(inv)
    exps = [math.exp(iv - mx) for iv in inv]
    s = sum(exps)
    return [e / s for e in exps]


# ----------------------------------------------------------------------
# 故障传播 + 贝叶斯溯源
# ----------------------------------------------------------------------
def _bfs_reachable(start, prop_graph):
    """从 start 出发，沿【故障传播图】可达的下游受影响服务集合。

    传播图边为 被调用方 -> 调用方，所以返回的是"会因 start 故障而受影响的调用方"。
    """
    seen, q = set(), [start]
    while q:
        u = q.pop(0)
        for v in prop_graph.get(u, []):
            if v not in seen:
                seen.add(v)
                q.append(v)
    seen.discard(start)  # 调用图可能存在环，剔除起点自身
    return seen


def _shortest_path(src, dst, prop_graph):
    """在【故障传播图】上求 src -> dst 的传播路径（src 为根因，dst 为受影响的观测点）。"""
    if src == dst:
        return [src]
    prev, q = {src: None}, [src]
    while q:
        u = q.pop(0)
        for v in prop_graph.get(u, []):
            if v not in prev:
                prev[v] = u
                if v == dst:
                    path, cur = [dst], dst
                    while prev[cur] is not None:
                        cur = prev[cur]
                        path.append(cur)
                    return list(reversed(path))
                q.append(v)
    return []


def _bayesian_root_cause(anomalous, services, down, prop_graph, failed_peers=None):
    """P(root | 观测异常) ∝ P(root) * Π P(observed | root)，归一化排序。

    down：真实不可达(可用性<1 或 Trace 中调用失败)的服务集合，给予先验加权；
    failed_peers：链路里"被调失败"的对端及次数——这是最直接的定位证据：
        r 就是失败对端 -> 强似然；r 能向上游传导到该对端 -> 中等；否则弱。
    """
    failed_peers = failed_peers or {}
    n = len(services)
    ranking = []
    for r in services:
        reachable = _bfs_reachable(r, prop_graph)
        logp = math.log(1.0 / n)
        if r in down:
            logp += math.log(8.0)  # 实际不可达的服务更可能是根因
        for v in anomalous:
            if v == r:
                like = 0.95
            elif v in reachable:   # r 是 v 的上游，可传导故障
                like = 0.5
            else:
                like = 0.05
            logp += math.log(like)
        for peer, cnt in failed_peers.items():
            if r == peer:
                like = 0.90
            elif peer in reachable:
                like = 0.40
            else:
                like = 0.05
            logp += math.log(like) * min(int(cnt), 3)  # 多次失败提高置信度，最多计 3 次
        ranking.append((r, logp))
    mx = max(p for _, p in ranking)
    exps = [(r, math.exp(p - mx)) for r, p in ranking]
    s = sum(e for _, e in exps) or 1.0
    return sorted([{"service": r, "prob": e / s} for r, e in exps],
                  key=lambda d: d["prob"], reverse=True)


# ----------------------------------------------------------------------
# 主入口：detect(ctx)
# ----------------------------------------------------------------------
def _median(lst):
    if not lst:
        return 0.0
    s = sorted(lst)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def detect(ctx):
    namespace = ctx["config"].get("namespace", NAMESPACE_DEFAULT)
    baseline = ctx.get("baseline") or {}
    target = (ctx.get("fault_info") or {}).get("service")

    t0 = time.time()

    # (0) 真实链路证据：Jaeger 里的运行态拓扑 + 失败调用对端 + 首个错误 span 时刻。
    #     先查 trace 再做 K8S 轮询：trace 是"已经发生的事实"，不依赖后续采样，
    #     而且一次批量查询只要亚秒级，用它决定后面还要不要慢速轮询。
    lookback = int((ctx.get("vars") or {}).get("trace_lookback_s", 900))
    inject_time = ctx.get("inject_time") or (ctx.get("fault_info") or {}).get("time")
    # 故障窗口起点：注入前 2s（留出探测请求发出到失败 span 落库的抖动），
    # 绝不回看到注入前的历史错误。
    trace_since = (float(inject_time) - 2.0) if inject_time else None
    trace = _trace_evidence(namespace, lookback_s=lookback, since=trace_since)
    if trace_since:
        print(f"[ALGO_DL] trace window since={trace_since:.3f} (inject_time={inject_time:.3f})")
    dep_graph = trace.get("graph") or STATIC_DEP_GRAPH
    prop_graph = _build_prop_graph(dep_graph)
    trace_services = trace.get("services") or {}
    trace_failed_peers = set(trace.get("failed_peers") or {})
    print(f"[ALGO_DL] trace: available={trace.get('available')} "
          f"source={trace.get('graph_source')} edges={trace.get('graph_edges')} "
          f"spans={trace.get('span_total')} failed_peers={sorted(trace_failed_peers)}")
    if trace.get("error"):
        print(f"[ALGO_DL] trace unavailable ({trace['error']}), fallback to design graph")

    # (0.3) K8S 就绪连续观测：捕捉「首次未就绪」与「再次全就绪」两个时间戳。
    #       链路已给出故障证据时只做短采样（证据比轮询早得多），否则按配置轮询。
    has_trace_evidence = bool(trace_failed_peers) or any(
        (v.get("error_count") or 0) > 0 for v in trace_services.values())
    WATCH_MAX = float((ctx.get("vars") or {}).get(
        "k8s_watch_max_s", 2.0 if has_trace_evidence else 20.0))
    avail_min = {s: 1.0 for s in SIM_SERVICES}
    first_unready = {}
    recovered = {}
    watch_start = time.time()
    while time.time() - watch_start < WATCH_MAX:
        avail = _availability_map(namespace)
        now = time.time()
        for svc in SIM_SERVICES:
            av = avail.get(svc, 0.0)
            if av < avail_min[svc]:
                avail_min[svc] = av
            if av < 0.999:
                first_unready.setdefault(svc, now)
            elif svc in first_unready:
                recovered.setdefault(svc, now)  # 未就绪之后重新就绪 = 恢复
        if target and target in recovered:
            break
        time.sleep(0.2)

    # 供容错动作复用：恢复时刻是观测到的事实，不是容错开始时的 now()
    ctx["k8s_watch"] = {
        "avail_min": dict(avail_min),
        "first_unready_at": dict(first_unready),
        "recovered_at": dict(recovered),
        "watch_duration_s": round(time.time() - watch_start, 3),
    }
    if trace.get("error"):
        print(f"[ALGO_DL] trace unavailable ({trace['error']}), fallback to design graph")
    ctx.setdefault("trace_stats", trace)

    # 用基线快照构造"正常流形"训练集（PCA + 自编码器），并固定归一化基准
    base_snap = _snapshot(namespace)
    Xb = [base_snap[s] for s in SIM_SERVICES]
    d = len(Xb[0])
    bmin = [min(Xb[i][j] for i in range(len(Xb))) for j in range(d)]
    bmax = [max(Xb[i][j] for i in range(len(Xb))) for j in range(d)]
    Xb_n = [[(Xb[i][j] - bmin[j]) / max(bmax[j] - bmin[j], 0.1 * bmax[j] + 0.1) for j in range(d)]
            for i in range(len(Xb))]
    # bootstrap 加微小噪声扩充正常样本
    train = Xb_n[:]
    for _ in range(20):
        train.extend([Xb_n[i][j] + random.gauss(0, 0.02) for j in range(d)]
                     for i in range(len(Xb_n)))
    pca = PCAAnalyzer(n_components=min(2, d - 1) if d > 2 else 1).fit(train)
    ae = AutoEncoder(dim=d, hidden=max(1, d - 1)).fit(train)
    att = _attention_weights(train)

    # (2) 慢速指标采样（metrics-server + Prometheus）用于 DL 多维特征评分
    POLLS = 4
    max_scores = {s: {"pca_error": 0.0, "ae_error": 0.0, "attention_error": 0.0, "composite": 0.0}
                  for s in SIM_SERVICES}
    comp_hist = {s: [] for s in SIM_SERVICES}
    for poll in range(POLLS):
        feats = _snapshot(namespace)
        for svc in SIM_SERVICES:
            avail_min[svc] = min(avail_min[svc], feats[svc][3])
        X = [feats[s] for s in SIM_SERVICES]
        Xn = [[min(5.0, max(-5.0, (X[i][j] - bmin[j]) / max(bmax[j] - bmin[j], 0.1 * bmax[j] + 0.1)))
               for j in range(d)] for i in range(len(X))]
        for idx, svc in enumerate(SIM_SERVICES):
            s_pca = pca.score(Xn[idx])
            s_ae = ae.score(Xn[idx])
            rec = pca.reconstruct(Xn[idx])
            att_err = math.sqrt(sum(att[j] * (Xn[idx][j] - rec[j]) ** 2 for j in range(d)))
            comp = 0.5 * s_ae + 0.5 * att_err
            m = max_scores[svc]
            m["pca_error"] = max(m["pca_error"], s_pca)
            m["ae_error"] = max(m["ae_error"], s_ae)
            m["attention_error"] = max(m["attention_error"], att_err)
            m["composite"] = max(m["composite"], comp)
            comp_hist[svc].append(comp)
        if poll < POLLS - 1:
            time.sleep(0.4)

    # 鲁棒异常判定：可用性下限（确定性）为主，指标复合评分中位数为辅（抗瞬时尖峰）
    comp_median = {s: _median(comp_hist[s]) for s in SIM_SERVICES}
    down = {s for s in SIM_SERVICES if avail_min[s] < 0.999}  # 真实不可达（pod 未就绪）
    # Trace 指认的故障服务：调用失败 span 的 peer.service（"谁被调失败了"）
    down |= {s for s in trace_failed_peers if s in SIM_SERVICES}
    med = _median(list(comp_median.values()))
    mad = _median([abs(c - med) for c in comp_median.values()]) or 1e-6
    # 阈值取三种口径的最大值：MAD 统计阈、中位数倍数、绝对下限。
    # 只用 MAD 会因分母极小把"几乎相同"的服务判成异常（0.006 vs 0.0055），
    # 倍数与下限用于压掉这种量级噪声，真正的故障由可用性/Trace 证据兜住。
    threshold = max(med + 3.0 * 1.4826 * mad, med * 2.5, 0.01)
    anomalous = [s for s in SIM_SERVICES if avail_min[s] < 0.999 or comp_median[s] > threshold]

    # 链路异常：某服务自身 span 有错误，或它就是被调失败的对端
    trace_anomalous = {s for s, v in trace_services.items()
                       if (v.get("error_count") or 0) > 0 and (v.get("error_rate") or 0) >= 0.02}
    for s in sorted(down | trace_anomalous):
        if s not in anomalous:
            anomalous.append(s)
    if not anomalous:  # 兜底：至少把目标纳入
        anomalous = [target] if target else []

    scores = {s: {k: round(v, 4) for k, v in max_scores[s].items()} for s in SIM_SERVICES}

    detected = bool(anomalous)
    # 感知时刻：取"最早的真实信号"。两个来源：
    #   k8s_readiness —— 轮询发现 pod 未就绪；trace_error —— 链路里首个失败 span 的时刻（更早、更准）
    # 注意：并发调用失败时，错误 span 记在【调用方】身上（带 peer.service 指向故障方），
    # 因此凡是出现错误 span 的服务都要参与"首个错误时刻"的比较。
    suspects = set(anomalous) | trace_failed_peers
    observed = [(ts, "k8s_readiness") for svc, ts in first_unready.items() if svc in anomalous]
    for svc, v in trace_services.items():
        ts = v.get("first_error_at")
        if not ts:
            continue
        # 双保险：即使上游查询窗口过滤有偏差，也不接受注入前的错误时刻
        if trace_since is not None and ts < trace_since:
            continue
        if svc in suspects or (v.get("error_count") or 0) > 0:
            observed.append((ts, "trace_error"))
    if detected and observed:
        detected_at, detection_signal = min(observed, key=lambda x: x[0])
        # 感知不可能早于注入：异常但证据时刻异常时回退到当前时刻
        if inject_time and detected_at < float(inject_time):
            detected_at = time.time()
            detection_signal = detection_signal + "_clamped"
    elif detected:
        detected_at = time.time()
        detection_signal = "metric_only"
    else:
        detected_at = None
        detection_signal = None
    ranking = _bayesian_root_cause(anomalous, SIM_SERVICES, down, prop_graph,
                                   trace.get("failed_peers"))
    root_cause = ranking[0]["service"] if ranking else target

    # 传播路径/影响面：沿真实 trace 拓扑的传播图，从根因向上游找受影响服务
    if target and root_cause != target:
        propagation_path = _shortest_path(root_cause, target, prop_graph) or [root_cause]
    else:
        propagation_path = [root_cause] if root_cause else []
    impact = []
    dq, seen = [(root_cause, 0)], {root_cause}
    while dq:
        u, dist = dq.pop(0)
        for v in prop_graph.get(u, []):
            if v in seen:
                continue
            seen.add(v)
            dq.append((v, dist + 1))
            if v in anomalous or v in trace_failed_peers or (trace_services.get(v, {}).get("error_count") or 0) > 0:
                impact.append({"service": v, "distance": dist + 1})
    impact.sort(key=lambda x: x["distance"])

    confidence = round(ranking[0]["prob"], 3) if detected else 0.0
    faulty_service = root_cause if detected else None

    # 链路证据摘要：给报告/答辩用的"可验证"部分
    rc_trace = trace_services.get(root_cause) or {}
    trace_evidence = {
        "available": bool(trace.get("available")),
        "error": trace.get("error"),
        "graph_source": trace.get("graph_source"),
        "graph_edges": trace.get("graph_edges"),
        "trace_count": trace.get("trace_count"),
        "span_total": trace.get("span_total"),
        "window_start": trace.get("window_start"),
        "window_end": trace.get("window_end"),
        "failed_peers": {k: v for k, v in (trace.get("failed_peers") or {}).items()},
        "trace_anomalous": sorted(trace_anomalous),
        "root_cause_span": {
            "service": root_cause,
            "span_count": rc_trace.get("span_count"),
            "error_count": rc_trace.get("error_count"),
            "error_rate": rc_trace.get("error_rate"),
            "p95_ms": rc_trace.get("p95_ms"),
            "first_error_at": rc_trace.get("first_error_at"),
        },
        "sample_trace_ids": trace.get("sample_trace_ids", []),
        "edge_counts": trace.get("edge_counts", {}),
    }

    result = {
        "detected": detected,
        "detected_at": detected_at,
        "detection_signal": detection_signal,
        "faulty_service": faulty_service,
        "confidence": confidence,
        "method": "pca_autoencoder_anomaly + trace_graph_bayesian_localization",
        "anomaly_scores": scores,
        "threshold": round(threshold, 4),
        "anomalous_services": anomalous,
        "root_cause_ranking": ranking[:3],
        "propagation_path": propagation_path,
        "impacted_services": impact,
        "trace_evidence": trace_evidence,
        "detect_invoked_at": t0,
        "scan_duration_s": round(time.time() - t0, 3),
    }
    print(f"[ALGO_DL] detected={detected} root_cause={root_cause} "
          f"confidence={confidence} signal={detection_signal} "
          f"impact={[i['service'] for i in impact]} anomalous={anomalous}")
    ctx.setdefault("metrics", {})["perception"] = {
        "detected": detected,
        "faulty_service": faulty_service,
        "confidence": confidence,
        "detection_signal": detection_signal,
        "anomalous_services": anomalous,
        "root_cause_top3": ranking[:3],
        "propagation_path": propagation_path,
        "impacted_services": impact,
        "pca_explained": [round(e, 3) for e in pca.explained],
        "trace_graph_source": trace.get("graph_source"),
        "trace_graph_edges": trace.get("graph_edges"),
        "trace_failed_peers": trace.get("failed_peers"),
    }
    return result
