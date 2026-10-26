from common.base import create_app, REQUEST_COUNT
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
    REQUEST_COUNT.labels(method="GET", endpoint="/entities").inc()
    return {"entities": entities}
