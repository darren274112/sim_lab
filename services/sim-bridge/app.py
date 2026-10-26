from common.base import create_app, REQUEST_COUNT
import os
import subprocess
import json

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "sim-bridge")

ross_status = {"running": False, "mode": "mock", "lp_count": 0, "events_processed": 0, "gvt": 0.0}
ross_process = None

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "ross": ross_status}

@app.post("/start")
def start(scenario: str = "battalion_attack", use_real_ross: bool = False):
    REQUEST_COUNT.labels(method="POST", endpoint="/start").inc()
    global ross_process
    
    if use_real_ross and os.path.exists("/app/ross-sim/build/mil_sim"):
        try:
            ross_process = subprocess.Popen(
                ["/app/ross-sim/build/mil_sim", "--synch=3", "--end=100.0", "--nlp=24"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            ross_status["mode"] = "real"
            ross_status["running"] = True
            ross_status["lp_count"] = 24
            return {"started": True, "scenario": scenario, "mode": "real", "pid": ross_process.pid}
        except Exception as e:
            return {"started": False, "error": str(e)}
    else:
        ross_status["running"] = True
        ross_status["mode"] = "mock"
        ross_status["lp_count"] = 24
        return {"started": True, "scenario": scenario, "mode": "mock"}

@app.get("/status")
def status():
    REQUEST_COUNT.labels(method="GET", endpoint="/status").inc()
    if ross_status["running"]:
        ross_status["events_processed"] += 150
        ross_status["gvt"] = ross_status["events_processed"] * 0.01
    return ross_status

@app.post("/inject_fault")
def inject_fault(fault_type: str = "comm_loss", target_lp: int = 21):
    REQUEST_COUNT.labels(method="POST", endpoint="/inject_fault").inc()
    return {"injected": True, "fault_type": fault_type, "target_lp": target_lp}

@app.get("/lp_status")
def lp_status():
    REQUEST_COUNT.labels(method="GET", endpoint="/lp_status").inc()
    return {
        "units": [{"lp": i, "side": i%2, "health": 100 if i%3!=0 else 0} for i in range(20)],
        "sensors": [{"lp": 20, "tracks": 8}],
        "fire_control": [{"lp": 21, "missions": 5, "rounds": 15, "degraded": False}],
        "comm": [{"lp": 22, "status": "UP"}],
        "terrain": [{"lp": 23, "visibility": 8000}]
    }
