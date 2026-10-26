from common.base import create_app, REQUEST_COUNT
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
    REQUEST_COUNT.labels(method="POST", endpoint="/send").inc()
    return {"sent": True, "route": "cmd_net"}
