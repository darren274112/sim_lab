from common.base import create_app, REQUEST_COUNT
import httpx
import os
import asyncio

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "orchestrator")

call_chain = []

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "workflow_orchestrator", "chain": call_chain}

@app.post("/trigger")
async def trigger(scenario: str = "battalion_attack"):
    REQUEST_COUNT.labels(method="GET", endpoint="/trigger").inc()
    # Simulate calling downstream services
    deps = ["scenario-mgr", "entity-service", "comms-service", "sensor-service", "fire-control", "sim-bridge"]
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for dep in deps:
            try:
                r = await client.get(f"http://{dep}:8080/healthz")
                results[dep] = "ok"
                call_chain.append({"to": dep, "status": "ok"})
            except Exception as e:
                results[dep] = f"error: {e}"
                call_chain.append({"to": dep, "status": "fail"})
    return {"scenario": scenario, "results": results}

@app.get("/chain")
def get_chain():
    return {"chain": call_chain[-20:]}
