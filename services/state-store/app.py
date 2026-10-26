from common.base import create_app, REQUEST_COUNT
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "state-store")

# In-memory state store (simulating distributed state)
state = {}
version = 0

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "keys": len(state), "version": version}

@app.get("/state/{key}")
def get_state(key: str):
    REQUEST_COUNT.labels(method="GET", endpoint="/state").inc()
    return {"key": key, "value": state.get(key), "version": version}

@app.put("/state/{key}")
def put_state(key: str, value: dict):
    REQUEST_COUNT.labels(method="PUT", endpoint="/state").inc()
    global version
    version += 1
    state[key] = value
    return {"key": key, "version": version}

@app.get("/snapshot")
def snapshot():
    REQUEST_COUNT.labels(method="GET", endpoint="/snapshot").inc()
    return {"state": state, "version": version}
