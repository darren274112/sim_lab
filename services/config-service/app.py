"""通用配置中心：带版本号的键值配置，支持热更新与变更历史。

用于演示平台服务在配置变更时的行为（可被实验工作流写入/读取）。
"""
from common.base import create_app, REQUEST_COUNT
import os
import threading
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "config-service")

_lock = threading.RLock()
_version = 0
_config = {
    "scenario.default": {"value": "battalion_attack", "type": "string"},
    "sim.tick_rate_hz": {"value": 10, "type": "int"},
    "fault.enabled": {"value": True, "type": "bool"},
}
_history = []


def _bump(key, value, action):
    global _version
    with _lock:
        _version += 1
        _config[key] = {"value": value,
                        "type": type(value).__name__ if not isinstance(value, bool) else "bool"}
        entry = {"key": key, "value": value, "version": _version,
                 "action": action, "at": time.time()}
        _history.append(entry)
        del _history[:-100]
        return entry


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    with _lock:
        return {"service": SERVICE_NAME, "role": "config_center",
                "keys": len(_config), "version": _version}


@app.get("/config")
def list_config():
    REQUEST_COUNT.labels(method="GET", endpoint="/config").inc()
    with _lock:
        return {"version": _version, "config": dict(_config)}


@app.get("/config/{key}")
def get_config(key: str):
    REQUEST_COUNT.labels(method="GET", endpoint="/config").inc()
    with _lock:
        item = _config.get(key)
        return {"key": key, "found": item is not None, "version": _version,
                "item": item}


@app.put("/config/{key}")
def put_config(key: str, body: dict):
    """写入配置：body = {value: ...}"""
    REQUEST_COUNT.labels(method="PUT", endpoint="/config").inc()
    if "value" not in body:
        return {"ok": False, "error": "body 需要 value 字段"}
    entry = _bump(key, body["value"], "put")
    return {"ok": True, "entry": entry}


@app.delete("/config/{key}")
def delete_config(key: str):
    REQUEST_COUNT.labels(method="DELETE", endpoint="/config").inc()
    with _lock:
        existed = key in _config
        _config.pop(key, None)
    if existed:
        _bump(key, None, "delete")
    return {"ok": existed, "key": key}


@app.get("/config-history")
def history():
    REQUEST_COUNT.labels(method="GET", endpoint="/config-history").inc()
    with _lock:
        return {"version": _version, "history": list(_history[-20:])}


@app.post("/config/reload")
def reload_config():
    """模拟从外部配置源重载：返回当前版本，便于验证热更新语义。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/config/reload").inc()
    with _lock:
        return {"status": "reloaded", "version": _version,
                "keys": len(_config), "at": time.time()}
