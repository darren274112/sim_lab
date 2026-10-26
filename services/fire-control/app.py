from common.base import create_app, REQUEST_COUNT
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "fire-control")

fire_missions = []
engagements = 0

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "fire_control", "engagements": engagements, "missions": len(fire_missions)}

@app.post("/engage")
def engage(target: dict):
    REQUEST_COUNT.labels(method="POST", endpoint="/engage").inc()
    global engagements
    engagements += 1
    fire_missions.append({"target": target.get("id"), "status": "engaged"})
    return {"engaged": True, "mission_id": f"FM-{engagements:04d}"}

@app.get("/status")
def status():
    REQUEST_COUNT.labels(method="GET", endpoint="/status").inc()
    return {"ready": True, "weapons": "online", "engagements": engagements}
