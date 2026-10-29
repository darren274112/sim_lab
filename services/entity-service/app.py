from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "entity-service")

entities = [
    {"id": "BT-001", "type": "tank", "side": "blue", "status": "active"},
    {"id": "IFV-001", "type": "ifv", "side": "blue", "status": "active"},
    {"id": "ART-001", "type": "artillery", "side": "blue", "status": "active"},
]

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "entities": len(entities)}

@app.get("/entities")
def list_entities():
    """实体清单：持久化到共享状态（entity-service -> state-store）+ 审计（-> log-service）。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/entities").inc()
    s_state, _ = tracing.traced_json_request(
        "PUT", "http://state-store:8080/state/entity-cache",
        body={"entities": entities}, timeout=1.5,
        span_name="PUT state-store/state")
    s_log, _ = tracing.traced_json_request(
        "POST", "http://log-service:8080/log",
        body={"event": "entity_listed", "count": len(entities)}, timeout=1.5,
        span_name="POST log-service/log")
    return {"entities": entities, "state_status": s_state, "log_status": s_log}
