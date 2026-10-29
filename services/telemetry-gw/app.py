from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "telemetry-gw")

telemetry = []

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "records": len(telemetry)}

@app.post("/ingest")
def ingest(data: dict):
    """遥测落库：入本地队列 + 持久化共享状态（-> state-store）+ 审计（-> log-service）。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/ingest").inc()
    telemetry.append(data)
    s_state, _ = tracing.traced_json_request(
        "PUT", "http://state-store:8080/state/telemetry-latest",
        body=data, timeout=1.5, span_name="PUT state-store/state")
    s_log, _ = tracing.traced_json_request(
        "POST", "http://log-service:8080/log",
        body={"event": "telemetry_ingested", "seq": len(telemetry)}, timeout=1.5,
        span_name="POST log-service/log")
    return {"ingested": True, "state_status": s_state, "log_status": s_log}
