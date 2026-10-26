from common.base import create_app, REQUEST_COUNT
from fastapi import HTTPException
import os
import re
import subprocess
import threading
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "sim-bridge")

MIL_SIM_PATH = os.getenv("MIL_SIM_PATH", "/app/ross-sim/build/mil_sim")
ROSS_LOG_PATH = os.getenv("ROSS_LOG_PATH", "/tmp/ross-sim.log")

ross_status = {
    "running": False,
    "engine": "ross",
    "mode": "real",
    "lp_count": 0,
    "events_processed": 0,
    "end_time": 0.0,
    "returncode": None,
    "pid": None,
}
ross_process = None
ross_run_id = 0
ross_lock = threading.Lock()


def _ross_available():
    return os.path.isfile(MIL_SIM_PATH) and os.access(MIL_SIM_PATH, os.X_OK)


def _parse_log(text):
    """从 ROSS 输出中提取统计信息"""
    result = {}
    m = re.search(r"Total Events Processed\s+(\d+)", text)
    if m:
        result["events_processed"] = int(m.group(1))
    m = re.search(r"Simulation End Time\s+([0-9.]+)", text)
    if m:
        result["end_time"] = float(m.group(1))
    m = re.search(r"Total LPs\s+(\d+)", text)
    if m:
        result["lp_count"] = int(m.group(1))
    return result


def _run_ross(cmd, run_id):
    """后台运行 mil_sim，结束后回收退出码和统计信息"""
    global ross_process
    try:
        with open(ROSS_LOG_PATH, "w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
            with ross_lock:
                if run_id != ross_run_id:
                    proc.terminate()
                    return
                ross_process = proc
                ross_status["pid"] = proc.pid
            rc = proc.wait()
    except Exception as exc:
        with ross_lock:
            if run_id != ross_run_id:
                return
            ross_process = None
            ross_status["running"] = False
            ross_status["returncode"] = -1
            ross_status["error"] = str(exc)
        return

    try:
        with open(ROSS_LOG_PATH) as log:
            output = log.read()
    except OSError:
        output = ""

    with ross_lock:
        if run_id != ross_run_id:
            return
        ross_process = None
        ross_status["running"] = False
        ross_status["returncode"] = rc
        ross_status["finished_at"] = time.time()
        ross_status.update(_parse_log(output))
        if rc != 0:
            ross_status["error"] = f"mil_sim exited with code {rc}"
        ross_status["output_tail"] = output.strip().splitlines()[-10:]
    print(f"[sim-bridge] mil_sim exited rc={rc}")


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {
        "service": SERVICE_NAME,
        "real_ross_available": _ross_available(),
        "mil_sim_path": MIL_SIM_PATH,
        "ross": ross_status,
    }


@app.post("/start")
def start(scenario: str = "battalion_attack",
          end_time: float = 100.0, units: int = 20):
    """启动 ROSS 仿真。sim-bridge 不再提供 mock 模式。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/start").inc()
    global ross_process, ross_run_id

    if not _ross_available():
        raise HTTPException(
            status_code=503,
            detail=f"ROSS 可执行文件不存在或不可执行: {MIL_SIM_PATH}",
        )

    with ross_lock:
        if ross_process is not None and ross_process.poll() is None:
            ross_process.terminate()
            try:
                ross_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ross_process.kill()
        ross_process = None
        ross_run_id += 1
        rid = ross_run_id
        ross_status.clear()
        ross_status.update({
            "running": True,
            "engine": "ross",
            "mode": "real",
            "lp_count": units + 4,
            "events_processed": 0,
            "end_time": end_time,
            "returncode": None,
            "pid": None,
            "error": None,
        })

    # --synch=1 为顺序仿真；当前 mil_sim 的反向计算未实现，不能用乐观同步
    cmd = [
        MIL_SIM_PATH,
        "--synch=1",
        f"--end={end_time}",
        f"--units={units}",
    ]
    threading.Thread(target=_run_ross, args=(cmd, rid), daemon=True).start()
    return {
        "started": True,
        "scenario": scenario,
        "mode": "real",
        "command": cmd,
        "log": ROSS_LOG_PATH,
    }


@app.get("/status")
def status():
    REQUEST_COUNT.labels(method="GET", endpoint="/status").inc()
    with ross_lock:
        return dict(ross_status)


@app.get("/ross/log")
def ross_log():
    REQUEST_COUNT.labels(method="GET", endpoint="/ross/log").inc()
    try:
        with open(ROSS_LOG_PATH) as log:
            return {"log": log.read()}
    except OSError:
        return {"log": ""}


@app.post("/inject_fault")
def inject_fault(fault_type: str = "comm_loss", target_lp: int = 21):
    REQUEST_COUNT.labels(method="POST", endpoint="/inject_fault").inc()
    raise HTTPException(
        status_code=501,
        detail="ROSS 运行时故障注入尚未实现；当前只能通过 K8S 对微服务注入故障",
    )


@app.get("/lp_status")
def lp_status():
    REQUEST_COUNT.labels(method="GET", endpoint="/lp_status").inc()
    units = max(0, int(ross_status.get("lp_count", 24)) - 4) or 20
    # 只返回 LP 布局和仿真统计，不伪造 LP 内部状态
    return {
        "engine": "ross",
        "mode": "real",
        "running": ross_status["running"],
        "lp_count": ross_status["lp_count"],
        "events_processed": ross_status["events_processed"],
        "units": [{"lp": i, "type": "unit", "side": i % 2} for i in range(units)],
        "sensors": [{"lp": units + 1, "type": "sensor"}],
        "fire_control": [{"lp": units, "type": "fire_control"}],
        "comm": [{"lp": units + 2, "type": "comm"}],
        "terrain": [{"lp": units + 3, "type": "terrain"}],
    }
