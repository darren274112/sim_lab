from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "comms-service")

networks = {"cmd_net": {"status": "up", "load": 0.3}, "tac_net": {"status": "up", "load": 0.4}}

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "networks": networks}

@app.post("/send")
def send(msg: dict):
    """下达指令：指令携带遥测（comms-service -> telemetry-gw）+ 审计留痕（-> log-service）。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/send").inc()
    # 指令携带遥测：comms-service -> telemetry-gw（telemetry-gw 再落 state-store / log-service）
    s_tel, _ = tracing.traced_json_request(
        "POST", "http://telemetry-gw:8080/ingest",
        body={"kind": "command", **msg}, timeout=1.5,
        span_name="POST telemetry-gw/ingest")
    # 审计留痕：comms-service -> log-service
    s_log, _ = tracing.traced_json_request(
        "POST", "http://log-service:8080/log",
        body={"event": "command_sent", "mission_id": msg.get("mission_id"),
              "route": msg.get("route")}, timeout=1.5,
        span_name="POST log-service/log")
    return {"sent": True, "route": "cmd_net",
            "telemetry_status": s_tel, "log_status": s_log}
