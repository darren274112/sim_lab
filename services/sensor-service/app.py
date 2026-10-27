from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "sensor-service")

tracks = []


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "tracks": len(tracks)}


@app.get("/tracks")
def get_tracks():
    """航迹融合：需要实体清单（entity-service）与环境参数（env-service）。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/tracks").inc()
    s_ent, ent_body = tracing.traced_json_request(
        "GET", "http://entity-service:8080/entities", timeout=3.0,
        span_name="GET entity-service/entities")
    s_env, env_body = tracing.traced_json_request(
        "GET", "http://env-service:8080/weather", timeout=3.0,
        span_name="GET env-service/weather")
    entities = (ent_body or {}).get("entities", []) if isinstance(ent_body, dict) else []
    return {
        "tracks": tracks,
        "fused_from": {"entities": len(entities), "entity_status": s_ent,
                       "weather_status": s_env},
        "weather": env_body if isinstance(env_body, dict) else {},
    }
