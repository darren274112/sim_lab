"""
分布式状态存储（可复制 + 反熵同步）
====================================
这是"研究内容三 状态一致性"的真实实验载体：

  - 无状态服务不落数据，一致性实验没有观测对象，因此 state-store 采用
    多副本（manifest 中 replicas: 3）+ 每个 Pod 独立内存状态；
  - 写入默认异步复制到其它副本，复制失败不影响本地写（AP 倾向）；
  - 后台反熵线程周期性拉取 peer 快照并按 LWW（版本号 -> 更新时间 -> 副本ID）
    合并，网络恢复后自动收敛；
  - 提供 /consistency、/sync、/repair 供实验动作验证"是否一致、能否修复"。

副本发现：优先 PEERS 环境变量（逗号分隔的 host:port），否则解析无头
Service `state-store-headless` 的全部 A 记录。这样既支持 K8S 部署，也支持
本地多进程调试。
"""
from common.base import create_app, REQUEST_COUNT
import hashlib
import json
import os
import socket
import threading
import time
import urllib.request

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "state-store")
PORT = int(os.getenv("PORT", "8080"))

REPLICA_ID = os.getenv("REPLICA_ID") or socket.gethostname()
PEERS_ENV = os.getenv("PEERS", "").strip()
HEADLESS_HOST = os.getenv("HEADLESS_HOST", "state-store-headless")
ANTI_ENTROPY_INTERVAL = float(os.getenv("ANTI_ENTROPY_INTERVAL", "5"))
PEER_TIMEOUT = float(os.getenv("PEER_TIMEOUT", "1.5"))

_lock = threading.RLock()
_state = {}          # key -> {"value":..., "version":int, "updated_at":float, "origin":str}
_stats = {
    "replicated_out": 0,
    "replicated_in": 0,
    "replicate_errors": 0,
    "anti_entropy_rounds": 0,
    "anti_entropy_merged": 0,
    "last_sync_at": None,
    "last_sync_error": None,
}


# ----------------------------------------------------------------------
# 副本发现
# ----------------------------------------------------------------------
def _own_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return None


def discover_peers():
    """返回其它副本的 base URL 列表（不含自身）。"""
    if PEERS_ENV:
        targets = [p.strip() for p in PEERS_ENV.split(",") if p.strip()]
    else:
        targets = []
        try:
            infos = socket.getaddrinfo(HEADLESS_HOST, PORT, type=socket.SOCK_STREAM)
            seen = set()
            for info in infos:
                ip = info[4][0]
                if ip not in seen:
                    seen.add(ip)
                    targets.append(f"{ip}:{PORT}")
        except OSError as e:
            _stats["last_sync_error"] = f"dns: {e}"[:200]
    me = _own_ip()
    peers = []
    for t in targets:
        host = t.rsplit(":", 1)[0]
        if me and host == me:
            continue
        peers.append(t if t.startswith("http") else f"http://{t}")
    return peers


# ----------------------------------------------------------------------
# 合并规则（LWW）
# ----------------------------------------------------------------------
def _rank(entry):
    """(version, updated_at, origin) 字典序，用于确定性收敛。"""
    return (int(entry.get("version", 0)),
            float(entry.get("updated_at", 0)),
            str(entry.get("origin", "")))


def _merge_entry(key, incoming):
    """把远端条目并入本地；返回 True 表示本地发生了变化。"""
    if not isinstance(incoming, dict):
        return False
    with _lock:
        cur = _state.get(key)
        if cur is None or _rank(incoming) > _rank(cur):
            _state[key] = {
                "value": incoming.get("value"),
                "version": int(incoming.get("version", 0)),
                "updated_at": float(incoming.get("updated_at", 0)),
                "origin": str(incoming.get("origin", "")),
            }
            return True
        return False


def _merge_snapshot(snapshot):
    merged = 0
    for key, entry in (snapshot.get("state") or {}).items():
        if _merge_entry(key, entry):
            merged += 1
    return merged


def _digest():
    """状态摘要：键 + 版本 + 来源，用于跨副本快速比对。"""
    with _lock:
        blob = json.dumps(
            {k: [v.get("version"), v.get("origin"), v.get("updated_at")]
             for k, v in sorted(_state.items())},
            sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ----------------------------------------------------------------------
# HTTP 辅助
# ----------------------------------------------------------------------
def _get_json(url, timeout=PEER_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url, body=None, timeout=PEER_TIMEOUT):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _push_to_peers(entry_for_key):
    """异步把一条更新推给所有 peer；失败只计数（AP）。"""
    def _run():
        for base in discover_peers():
            try:
                _post_json(base + "/replicate", entry_for_key, timeout=PEER_TIMEOUT)
                with _lock:
                    _stats["replicated_out"] += 1
            except Exception as e:
                with _lock:
                    _stats["replicate_errors"] += 1
                    _stats["last_sync_error"] = f"{base}: {type(e).__name__}: {e}"[:200]
    threading.Thread(target=_run, name="replicate", daemon=True).start()


def _anti_entropy_loop():
    while True:
        time.sleep(ANTI_ENTROPY_INTERVAL)
        merged_total = 0
        for base in discover_peers():
            try:
                snap = _get_json(base + "/snapshot")
                merged_total += _merge_snapshot(snap)
            except Exception as e:
                with _lock:
                    _stats["last_sync_error"] = f"{base}: {type(e).__name__}: {e}"[:200]
        with _lock:
            _stats["anti_entropy_rounds"] += 1
            _stats["anti_entropy_merged"] += merged_total
            _stats["last_sync_at"] = time.time()


threading.Thread(target=_anti_entropy_loop, name="anti-entropy", daemon=True).start()


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    with _lock:
        return {"service": SERVICE_NAME, "replica": REPLICA_ID, "keys": len(_state),
                "max_version": max((v["version"] for v in _state.values()), default=0),
                "digest": _digest(), "peers": discover_peers()}


@app.get("/state/{key}")
def get_state(key: str):
    REQUEST_COUNT.labels(method="GET", endpoint="/state").inc()
    with _lock:
        entry = _state.get(key)
        return {"key": key, "replica": REPLICA_ID,
                "value": entry.get("value") if entry else None,
                "version": entry.get("version", 0) if entry else 0,
                "origin": entry.get("origin") if entry else None,
                "updated_at": entry.get("updated_at") if entry else None,
                "found": entry is not None}


@app.put("/state/{key}")
def put_state(key: str, value: dict, replicate: bool = True):
    """写入一个键。replicate=false 时只写本地，用于制造副本分叉实验。"""
    REQUEST_COUNT.labels(method="PUT", endpoint="/state").inc()
    now = time.time()
    with _lock:
        cur = _state.get(key)
        entry = {
            "key": key,
            "value": value,
            "version": (int(cur["version"]) + 1) if cur else 1,
            "updated_at": now,
            "origin": REPLICA_ID,
        }
        _state[key] = entry
    if replicate:
        _push_to_peers(entry)
    return {"key": key, "version": entry["version"], "origin": REPLICA_ID,
            "replica": REPLICA_ID, "replicated": bool(replicate)}


@app.post("/replicate")
def replicate(body: dict):
    """内部接口：接收 peer 推送的单条更新。"""
    entry = dict(body or {})
    key = entry.pop("key", None)
    if not key:
        return {"ok": False, "error": "missing key"}
    changed = _merge_entry(key, entry)
    if changed:
        with _lock:
            _stats["replicated_in"] += 1
    return {"ok": True, "changed": changed, "replica": REPLICA_ID}


@app.get("/snapshot")
def snapshot():
    REQUEST_COUNT.labels(method="GET", endpoint="/snapshot").inc()
    with _lock:
        return {"replica": REPLICA_ID, "state": json.loads(json.dumps(_state)),
                "version": max((v["version"] for v in _state.values()), default=0),
                "digest": _digest(), "timestamp": time.time()}


@app.get("/replicas")
def replicas():
    REQUEST_COUNT.labels(method="GET", endpoint="/replicas").inc()
    with _lock:
        return {"replica": REPLICA_ID, "peers": discover_peers(),
                "keys": len(_state), "digest": _digest(),
                "stats": dict(_stats)}


@app.get("/consistency")
def consistency():
    """对比本地与所有 peer 的状态，返回是否一致以及分歧键。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/consistency").inc()
    local = snapshot()
    peers = {}
    divergent = set()
    reachable = 0
    for base in discover_peers():
        try:
            remote = snapshot_from(base)
            reachable += 1
        except Exception as e:
            peers[base] = {"reachable": False, "error": f"{type(e).__name__}: {e}"[:120]}
            continue
        peers[base] = {
            "reachable": True,
            "replica": remote.get("replica"),
            "digest": remote.get("digest"),
            "keys": len(remote.get("state") or {}),
        }
        for key, entry in (remote.get("state") or {}).items():
            mine = (local["state"].get(key) or {})
            if (entry.get("version"), entry.get("updated_at"), entry.get("origin")) != \
               (mine.get("version"), mine.get("updated_at"), mine.get("origin")):
                divergent.add(key)
        for key in local["state"]:
            if key not in (remote.get("state") or {}):
                divergent.add(key)
    consistent = reachable == len(peers) and not divergent
    return {"replica": REPLICA_ID, "consistent": consistent,
            "digest": local["digest"], "peers": peers,
            "divergent_keys": sorted(divergent), "timestamp": time.time()}


def snapshot_from(base):
    return _get_json(base + "/snapshot")


@app.post("/sync")
def sync():
    """主动反熵：拉取所有 peer 快照并合并。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/sync").inc()
    merged = 0
    errors = []
    for base in discover_peers():
        try:
            merged += _merge_snapshot(_get_json(base + "/snapshot"))
        except Exception as e:
            errors.append(f"{base}: {type(e).__name__}: {e}"[:160])
    with _lock:
        _stats["anti_entropy_rounds"] += 1
        _stats["anti_entropy_merged"] += merged
        _stats["last_sync_at"] = time.time()
    return {"replica": REPLICA_ID, "merged": merged, "errors": errors,
            "digest": _digest(), "timestamp": time.time()}


@app.post("/repair")
def repair():
    """执行一次同步并返回修复前后的分歧情况，供实验动作记录。"""
    before = consistency()
    merged = 0
    for base in discover_peers():
        try:
            merged += _merge_snapshot(_get_json(base + "/snapshot"))
        except Exception:
            pass
    after = consistency()
    return {"replica": REPLICA_ID, "merged": merged,
            "before_consistent": before["consistent"],
            "after_consistent": after["consistent"],
            "before_divergent": before["divergent_keys"],
            "after_divergent": after["divergent_keys"],
            "digest": after["digest"], "timestamp": time.time()}


@app.post("/admin/reset")
def reset():
    """清空副本状态（实验前复位用）。"""
    with _lock:
        _state.clear()
    return {"status": "reset", "replica": REPLICA_ID, "timestamp": time.time()}
