from common.base import create_app, REQUEST_COUNT
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
    REQUEST_COUNT.labels(method="POST", endpoint="/ingest").inc()
    telemetry.append(data)
    return {"ingested": True}
