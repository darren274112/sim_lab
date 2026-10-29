from common.base import create_app, REQUEST_COUNT
from common import tracing
from fastapi import HTTPException
import os
import re
import json
import subprocess
import threading
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "sim-bridge")

MIL_SIM_PATH = os.getenv("MIL_SIM_PATH", "/app/ross-sim/build/mil_sim")
ROSS_LOG_PATH = os.getenv("ROSS_LOG_PATH", "/tmp/ross-sim.log")
ROSS_FAULT_PATH = os.getenv("ROSS_FAULT_PATH", "/tmp/ross_fault.json")

VALID_FAULT_TYPES = {"lp_crash", "comm_loss"}

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
ross_fault = None  # 待生效故障：{"type", "target_lp", "at_time", "injected_at"}


def _load_fault():
    """启动时从磁盘恢复未清掉的故障配置（Pod 重启后保持）"""
    global ross_fault
    try:
        with open(ROSS_FAULT_PATH) as f:
            ross_fault = json.load(f)
    except (OSError, ValueError):
        ross_fault = None


_load_fault()


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


def _run_ross(cmd, run_id, env=None):
    """后台运行 mil_sim，结束后回收退出码和统计信息"""
    global ross_process
    try:
        with open(ROSS_LOG_PATH, "w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, env=env)
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
        "pending_fault": ross_fault,
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
            "fault": ross_fault,
        })

    # --synch=1 为顺序仿真；当前 mil_sim 的反向计算未实现，不能用乐观同步
    cmd = [
        MIL_SIM_PATH,
        "--synch=1",
        f"--end={end_time}",
        f"--units={units}",
    ]
    # 若有待生效故障，将其作为环境变量注入 mil_sim（由 C 侧 fault_init 读取）
    env = dict(os.environ)
    if ross_fault:
        env["ROSS_FAULT_TYPE"] = ross_fault["type"]
        env["ROSS_FAULT_TARGET"] = str(ross_fault["target_lp"])
        env["ROSS_FAULT_AT"] = str(ross_fault["at_time"])
    threading.Thread(target=_run_ross, args=(cmd, rid, env), daemon=True).start()

    # ROSS 引擎状态/事件上报到共享存储（sim-bridge -> state-store / log-service）
    tracing.traced_json_request(
        "PUT", "http://state-store:8080/state/ross-run",
        body={"run_id": rid, "scenario": scenario, "end_time": end_time,
              "lp_count": units + 4}, timeout=1.5,
        span_name="PUT state-store/state")
    tracing.traced_json_request(
        "POST", "http://log-service:8080/log",
        body={"event": "ross_started", "run_id": rid, "scenario": scenario},
        timeout=1.5, span_name="POST log-service/log")
    return {
        "started": True,
        "scenario": scenario,
        "mode": "real",
        "fault": ross_fault,
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
def inject_fault(fault_type: str = "lp_crash", target_lp: int = 0, at_time: float = 5.0):
    """向 ROSS 仿真注入故障。

    - lp_crash: 目标 LP（gid）在 at_time 后停止处理任何事件，视为宕机。
    - comm_loss: 通信 LP（gid=units+2）在 at_time 后中断，COMM_MSG 全部丢弃。
    故障会在下一次 /start 时通过环境变量注入 mil_sim，并持续生效直到 /clear_fault。
    """
    REQUEST_COUNT.labels(method="POST", endpoint="/inject_fault").inc()
    global ross_fault
    if fault_type not in VALID_FAULT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的故障类型: {fault_type}，可选: {sorted(VALID_FAULT_TYPES)}",
        )
    if target_lp < 0:
        raise HTTPException(status_code=400, detail="target_lp 必须为非负整数")
    ross_fault = {
        "type": fault_type,
        "target_lp": int(target_lp),
        "at_time": float(at_time),
        "injected_at": time.time(),
    }
    try:
        with open(ROSS_FAULT_PATH, "w") as f:
            json.dump(ross_fault, f)
    except OSError:
        pass
    return {"status": "injected", "fault": ross_fault}


@app.post("/clear_fault")
def clear_fault():
    """清除待生效的故障配置"""
    REQUEST_COUNT.labels(method="POST", endpoint="/clear_fault").inc()
    global ross_fault
    ross_fault = None
    try:
        os.remove(ROSS_FAULT_PATH)
    except OSError:
        pass
    return {"status": "cleared"}


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
        "fault": ross_fault,
        "units": [{"lp": i, "type": "unit", "side": i % 2} for i in range(units)],
        "sensors": [{"lp": units + 1, "type": "sensor"}],
        "fire_control": [{"lp": units, "type": "fire_control"}],
        "comm": [{"lp": units + 2, "type": "comm"}],
        "terrain": [{"lp": units + 3, "type": "terrain"}],
    }
