from common.base import create_app, REQUEST_COUNT
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "log-service")

logs = []

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "logs_count": len(logs)}

@app.post("/log")
def add_log(entry: dict):
    REQUEST_COUNT.labels(method="POST", endpoint="/log").inc()
    logs.append(entry)
    return {"logged": True}
