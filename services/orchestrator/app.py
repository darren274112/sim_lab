from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "orchestrator")

call_chain = []

ALL_DEPS = ["scenario-mgr", "entity-service", "comms-service", "sensor-service",
            "fire-control", "env-service", "log-service", "state-store",
            "telemetry-gw", "sim-bridge"]


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "workflow_orchestrator",
            "chain": call_chain[-20:], "tracing": tracing.stats()}


@app.post("/trigger")
def trigger(scenario: str = "battalion_attack"):
    """下发作战想定：先做全依赖存活探测，再跑一条 4 级真实业务链路。

    所有下游调用都会带上 traceparent，因此一次 /trigger 在 Jaeger 里是
    一条包含 15+ 个 span 的完整链路（orchestrator -> fire-control ->
    sensor-service -> entity-service/env-service）。
    """
    REQUEST_COUNT.labels(method="POST", endpoint="/trigger").inc()
    span = tracing.current_span()
    trace_id = span.trace_id if span else None

    health = {}
    for dep in ALL_DEPS:
        status, _ = tracing.traced_json_request("GET", f"http://{dep}:8080/healthz")
        health[dep] = "ok" if status == 200 else f"error:{status}"
        call_chain.append({"to": dep, "op": "healthz", "status": health[dep],
                           "trace_id": trace_id})

    # 1) 想定加载（scenario-mgr -> state-store / log-service）
    s1, scenario_body = tracing.traced_json_request(
        "GET", f"http://scenario-mgr:8080/scenario/{scenario}",
        span_name="GET scenario-mgr/scenario")
    call_chain.append({"to": "scenario-mgr", "op": "scenario", "status": s1,
                       "trace_id": trace_id})

    # 2) 火控交战（fire-control -> sensor-service -> entity-service/env-service）
    s2, engage_body = tracing.traced_json_request(
        "POST", "http://fire-control:8080/engage",
        body={"id": "TGT-1042", "type": "armor", "scenario": scenario},
        timeout=2.0, span_name="POST fire-control/engage")
    call_chain.append({"to": "fire-control", "op": "engage", "status": s2,
                       "trace_id": trace_id})

    # 3) 遥测落库
    s3, _ = tracing.traced_json_request(
        "POST", "http://telemetry-gw:8080/ingest",
        body={"scenario": scenario, "trace_id": trace_id, "engage": engage_body},
        span_name="POST telemetry-gw/ingest")
    call_chain.append({"to": "telemetry-gw", "op": "ingest", "status": s3,
                       "trace_id": trace_id})

    del call_chain[:-60]  # 只保留最近 60 条，避免长跑内存泄漏
    return {
        "scenario": scenario,
        "trace_id": trace_id,
        "health": health,
        "scenario_data": scenario_body,
        "engage": engage_body,
        "telemetry_status": s3,
    }


@app.get("/chain")
def get_chain():
    return {"chain": call_chain[-20:]}
