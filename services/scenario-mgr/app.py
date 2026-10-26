from common.base import create_app, REQUEST_COUNT
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
    REQUEST_COUNT.labels(method="GET", endpoint="/scenario").inc()
    if name in scenarios:
        return scenarios[name]
    return {"error": "not found"}, 404
