# 分布式状态操作：写入 / 读取 / 一致性校验 / 反熵修复
#
# 直接从 orchestrator Pod 内访问 `state-store-headless` 的各个副本 IP，
# 这样可以定向写单个副本制造分叉，也可以逐个读取对比是否收敛。
# 全部操作都走真实 HTTP，不依赖任何模拟数据。
from wf import register_action
import json
import subprocess
import time

HEADLESS_HOST = "state-store-headless"
STATE_PORT = 8080

_COMMON = r'''
import json, socket, urllib.request

HOST = __HOST__
PORT = __PORT__


def replicas():
    infos = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
    seen, out = set(), []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return sorted(out)


def call(ip, method, path, body=None, timeout=4):
    url = "http://%s:%d%s" % (ip, PORT, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def safe(ip, method, path, body=None):
    try:
        return call(ip, method, path, body)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
'''

_WRITE = _COMMON + r'''
KEY = __KEY__
VALUE = __VALUE__
TARGET = __TARGET__
REPLICATE = __REPLICATE__

ips = replicas()
if TARGET == "one":
    ips = ips[:1]
out = []
for ip in ips:
    res = safe(ip, "PUT", "/state/%s?replicate=%s" % (KEY, "1" if REPLICATE else "0"), VALUE)
    res["ip"] = ip
    out.append(res)
print(json.dumps({"key": KEY, "targets": len(ips), "replicate": REPLICATE,
                  "replicas": out}))
'''

_READ = _COMMON + r'''
KEY = __KEY__
ips = replicas()
out = []
for ip in ips:
    res = safe(ip, "GET", "/state/%s" % KEY)
    res["ip"] = ip
    out.append(res)
print(json.dumps({"key": KEY, "replicas": out}))
'''

_VERIFY = _COMMON + r'''
ips = replicas()
snaps, unreachable = [], []
for ip in ips:
    res = safe(ip, "GET", "/snapshot")
    if res.get("error"):
        unreachable.append({"ip": ip, "error": res["error"]})
    else:
        res["ip"] = ip
        snaps.append(res)

divergent = set()
per_key = {}
if len(snaps) >= 2:
    for key in set().union(*(set(s.get("state") or {}) for s in snaps)):
        ranks = {}
        for s in snaps:
            e = (s.get("state") or {}).get(key)
            ranks[s["ip"]] = None if e is None else [e.get("version"),
                                                     e.get("updated_at"),
                                                     e.get("origin")]
        if len({json.dumps(v, sort_keys=True) for v in ranks.values()}) > 1:
            divergent.add(key)
            per_key[key] = ranks

digests = {s["ip"]: s.get("digest") for s in snaps}
consistent = bool(snaps) and not unreachable and not divergent and len(set(digests.values())) <= 1
print(json.dumps({
    "replicas": [{"ip": s["ip"], "replica": s.get("replica"),
                  "digest": s.get("digest"),
                  "keys": len(s.get("state") or {})} for s in snaps],
    "unreachable": unreachable,
    "consistent": consistent,
    "divergent_keys": sorted(divergent),
    "per_key": per_key,
    "timestamp": __NOW__,
}))
'''

_REPAIR = _COMMON + r'''
ips = replicas()
before = []
for ip in ips:
    before.append({"ip": ip, "result": safe(ip, "GET", "/consistency")})
synced = []
for ip in ips:
    synced.append({"ip": ip, "result": safe(ip, "POST", "/sync")})
after = []
for ip in ips:
    after.append({"ip": ip, "result": safe(ip, "GET", "/consistency")})
print(json.dumps({"replicas": len(ips), "before": before, "sync": synced,
                  "after": after, "timestamp": __NOW__}))
'''

_RESET = _COMMON + r'''
ips = replicas()
out = []
for ip in ips:
    res = safe(ip, "POST", "/admin/reset")
    res["ip"] = ip
    out.append(res)
print(json.dumps({"replicas": out, "timestamp": __NOW__}))
'''


def _exec(script, namespace="sim-test", timeout=60):
    r = subprocess.run(
        ["kubectl", "-n", namespace, "exec", "-i", "deploy/orchestrator", "--", "python", "-"],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"state operation failed: {r.stderr[-500:]}")
    lines = r.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("state operation returned empty output")
    return json.loads(lines[-1])


@register_action("state.write")
def write(ctx, params):
    """写入键值。target=all 写所有副本；target=one 只写一个副本（制造分叉）。"""
    key = params.get("key")
    if not key:
        raise ValueError("state.write 需要 key 参数")
    value = params.get("value")
    if value is None:
        value = {"counter": 0}
    target = params.get("target", "all")
    replicate = bool(params.get("replicate", True))
    namespace = ctx["config"].get("namespace", "sim-test")

    script = (_WRITE
              .replace("__HOST__", repr(HEADLESS_HOST))
              .replace("__PORT__", str(STATE_PORT))
              .replace("__KEY__", repr(str(key)))
              .replace("__VALUE__", repr(value))
              .replace("__TARGET__", repr(target))
              .replace("__REPLICATE__", repr(replicate)))
    res = _exec(script, namespace)
    res["timestamp"] = time.time()
    ctx.setdefault("state_ops", []).append({"op": "write", **res})
    ok = [r for r in res["replicas"] if not r.get("error")]
    print(f"[STATE] write key={key} target={target} replicate={replicate} "
          f"ok={len(ok)}/{len(res['replicas'])} "
          f"versions={[r.get('version') for r in ok]}")
    return res


@register_action("state.read")
def read(ctx, params):
    """从每个副本读取同一个键，用于观察复制/收敛状态。"""
    key = params.get("key")
    if not key:
        raise ValueError("state.read 需要 key 参数")
    namespace = ctx["config"].get("namespace", "sim-test")
    script = (_READ
              .replace("__HOST__", repr(HEADLESS_HOST))
              .replace("__PORT__", str(STATE_PORT))
              .replace("__KEY__", repr(str(key))))
    res = _exec(script, namespace)
    print(f"[STATE] read key={key}: " +
          ", ".join(f"{r['ip']} v{r.get('version')}={r.get('value')}" for r in res["replicas"]))
    return res


@register_action("state.verify")
def verify(ctx, params):
    """对比所有副本的快照，判断是否一致并列出分歧键。"""
    namespace = ctx["config"].get("namespace", "sim-test")
    script = (_VERIFY
              .replace("__HOST__", repr(HEADLESS_HOST))
              .replace("__PORT__", str(STATE_PORT))
              .replace("__NOW__", "%.3f" % time.time()))
    res = _exec(script, namespace)
    ctx["state_verify"] = res
    ctx.setdefault("metrics", {})["state_consistency"] = {
        "consistent": res["consistent"],
        "divergent_keys": res["divergent_keys"],
        "replicas": len(res["replicas"]),
        "unreachable": len(res["unreachable"]),
    }
    print(f"[STATE] verify: replicas={len(res['replicas'])} "
          f"consistent={res['consistent']} divergent={res['divergent_keys']} "
          f"unreachable={len(res['unreachable'])}")
    return res


@register_action("state.repair")
def repair(ctx, params):
    """主动反熵修复：每个副本拉取 peer 快照并合并，然后再次校验。"""
    namespace = ctx["config"].get("namespace", "sim-test")
    script = (_REPAIR
              .replace("__HOST__", repr(HEADLESS_HOST))
              .replace("__PORT__", str(STATE_PORT))
              .replace("__NOW__", "%.3f" % time.time()))
    res = _exec(script, namespace, timeout=int(params.get("timeout_s", 60)))
    res["timestamp"] = time.time()
    ctx.setdefault("state_ops", []).append({"op": "repair", **res})
    after_ok = [r for r in res["after"] if (r.get("result") or {}).get("consistent")]
    print(f"[STATE] repair: replicas={res['replicas']} "
          f"consistent_after={len(after_ok)}/{res['replicas']}")
    return res


@register_action("state.reset")
def reset(ctx, params):
    """清空所有副本状态，保证实验从干净状态开始。"""
    namespace = ctx["config"].get("namespace", "sim-test")
    script = (_RESET
              .replace("__HOST__", repr(HEADLESS_HOST))
              .replace("__PORT__", str(STATE_PORT))
              .replace("__NOW__", "%.3f" % time.time()))
    res = _exec(script, namespace)
    print(f"[STATE] reset on {len(res['replicas'])} replica(s)")
    return res
