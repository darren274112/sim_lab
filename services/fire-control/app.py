from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "fire-control")

fire_missions = []
engagements = 0


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "fire_control",
            "engagements": engagements, "missions": len(fire_missions)}


@app.post("/engage")
def engage(target: dict):
    """交战流程：取目标航迹（sensor-service）+ 下达指令（comms-service）。

    两个下游调用都继承当前 span，构成
    orchestrator -> fire-control -> sensor-service -> entity-service/env-service 的真实链路。
    """
    REQUEST_COUNT.labels(method="POST", endpoint="/engage").inc()
    global engagements
    engagements += 1
    mission_id = f"FM-{engagements:04d}"

    s_tracks, tracks_body = tracing.traced_json_request(
        "GET", "http://sensor-service:8080/tracks", timeout=3.0,
        span_name="GET sensor-service/tracks")
    s_cmd, cmd_body = tracing.traced_json_request(
        "POST", "http://comms-service:8080/send",
        body={"mission_id": mission_id, "target": target.get("id"), "route": "cmd_net"},
        timeout=2.0, span_name="POST comms-service/send")
    # 审计留痕：交战决策 -> log-service（fire-control -> log-service）
    s_log, _ = tracing.traced_json_request(
        "POST", "http://log-service:8080/log",
        body={"event": "engage", "mission_id": mission_id,
              "target": target.get("id")}, timeout=1.5,
        span_name="POST log-service/log")

    mission = {
        "target": target.get("id"),
        "status": "engaged" if s_tracks == 200 else "degraded",
        "mission_id": mission_id,
        "tracks_status": s_tracks,
        "comms_status": s_cmd,
        "log_status": s_log,
    }
    fire_missions.append(mission)
    del fire_missions[:-50]
    return {"engaged": True, "mission_id": mission_id, "mission": mission,
            "tracks": tracks_body, "command": cmd_body, "log_status": s_log}


@app.get("/status")
def status():
    REQUEST_COUNT.labels(method="GET", endpoint="/status").inc()
    return {"ready": True, "weapons": "online", "engagements": engagements}
