from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "scenario-mgr")

scenarios = {
    "battalion_attack": {"name": "营级进攻", "units": 12, "duration_min": 30},
    "air_defense": {"name": "防空作战", "units": 8, "duration_min": 45},
    "combined_arms": {"name": "合同作战", "units": 20, "duration_min": 60}
}


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "scenarios": list(scenarios.keys())}


@app.get("/scenario/{name}")
def get_scenario(name: str):
    """加载想定：写入状态存储（state-store）并留痕（log-service）。"""
    REQUEST_COUNT.labels(method="GET", endpoint="/scenario").inc()
    sc = scenarios.get(name)
    if sc is None:
        return {"error": "not found"}, 404
    # 交叉调用：计划侧查询当前感知态势（scenario-mgr -> sensor-service，跨分支）
    s_tracks, tracks_body = tracing.traced_json_request(
        "GET", "http://sensor-service:8080/tracks", timeout=2.5,
        span_name="GET sensor-service/tracks")
    s_state, _ = tracing.traced_json_request(
        "PUT", f"http://state-store:8080/state/{name}", body=sc, timeout=1.5,
        span_name="PUT state-store/state")
    s_log, _ = tracing.traced_json_request(
        "POST", "http://log-service:8080/log",
        body={"event": "scenario_loaded", "name": name}, timeout=1.5,
        span_name="POST log-service/log")
    return {**sc, "tracks_status": s_tracks, "tracks": tracks_body,
            "state_status": s_state, "log_status": s_log}
