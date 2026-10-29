from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "env-service")

weather = {"temp": 15, "visibility": 8000, "wind": "5m/s", "condition": "clear"}

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "weather": weather}

@app.get("/weather")
def get_weather():
    """环境参数：持久化到共享状态（env-service -> state-store）。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/weather").inc()
    s_state, _ = tracing.traced_json_request(
        "PUT", "http://state-store:8080/state/env-snapshot",
        body=weather, timeout=1.5, span_name="PUT state-store/state")
    return {**weather, "state_status": s_state}
