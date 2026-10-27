"""通用通知服务：接收并按级别缓存平台/实验事件，供工作流查询。

生产环境可替换为邮件/Webhook/消息队列；这里的接口与语义保持一致，
便于实验工作流验证"故障感知 -> 通知"链路。
"""
from common.base import create_app, REQUEST_COUNT
import os
import threading
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "notification-service")
MAX_ITEMS = int(os.getenv("NOTIFY_MAX_ITEMS", "200"))

_lock = threading.RLock()
_items = []
_seq = 0
LEVELS = ("info", "warn", "error")


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    with _lock:
        return {"service": SERVICE_NAME, "role": "notification",
                "pending": len(_items), "sequence": _seq}


@app.post("/notify")
def notify(body: dict):
    """发送通知：body = {level, title, message, source?}"""
    REQUEST_COUNT.labels(method="POST", endpoint="/notify").inc()
    global _seq
    level = str(body.get("level") or "info").lower()
    if level not in LEVELS:
        level = "info"
    with _lock:
        _seq += 1
        item = {
            "id": _seq,
            "level": level,
            "title": str(body.get("title") or "")[:200],
            "message": str(body.get("message") or "")[:2000],
            "source": str(body.get("source") or "unknown"),
            "at": time.time(),
        }
        _items.append(item)
        del _items[:-MAX_ITEMS]
    return {"ok": True, "notification": item}


@app.get("/notifications")
def list_notifications(level: str | None = None, limit: int = 50):
    REQUEST_COUNT.labels(method="GET", endpoint="/notifications").inc()
    with _lock:
        data = list(_items)
    if level:
        data = [i for i in data if i["level"] == level]
    return {"total": len(data), "items": data[-max(1, min(limit, MAX_ITEMS)):]}


@app.get("/notify/stats")
def stats():
    REQUEST_COUNT.labels(method="GET", endpoint="/notify/stats").inc()
    with _lock:
        counts = {lvl: sum(1 for i in _items if i["level"] == lvl) for lvl in LEVELS}
        return {"pending": len(_items), "by_level": counts, "sequence": _seq}


@app.post("/notifications/clear")
def clear():
    REQUEST_COUNT.labels(method="POST", endpoint="/notifications/clear").inc()
    with _lock:
        cleared = len(_items)
        _items.clear()
    return {"ok": True, "cleared": cleared}
