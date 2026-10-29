from common.base import create_app, REQUEST_COUNT
from common import tracing
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "orchestrator")

call_chain = []

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "workflow_orchestrator",
            "chain": call_chain[-20:], "tracing": tracing.stats()}


@app.post("/trigger")
def trigger(scenario: str = "battalion_attack"):
    """下发作战想定：只向下委派 fire-control / scenario-mgr 两个决策入口，
    其余服务经嵌套调用链自然触达，形成 5 层嵌套 DAG（非平铺）：

      orchestrator -> fire-control  -> sensor-service -> entity-service -> state-store
                             |     -> comms-service  -> telemetry-gw   -> state-store
                             +     -> log-service
      orchestrator -> scenario-mgr  -> sensor-service -> entity-service -> state-store
                             |     -> state-store
                             +     -> log-service

    存活就绪由 workflow 的 k8s.check_ready 负责，这里不再对全部依赖做平铺
    healthz 探测，以保证运行态 trace 是干净的嵌套业务链路（研究的分析对象）。
    """
    REQUEST_COUNT.labels(method="POST", endpoint="/trigger").inc()
    span = tracing.current_span()
    trace_id = span.trace_id if span else None

    # 1) 想定加载：scenario-mgr -> sensor-service / state-store / log-service
    s1, scenario_body = tracing.traced_json_request(
        "GET", f"http://scenario-mgr:8080/scenario/{scenario}",
        timeout=5.0, span_name="GET scenario-mgr/scenario")
    call_chain.append({"to": "scenario-mgr", "op": "scenario", "status": s1,
                       "trace_id": trace_id})

    # 2) 火控交战：fire-control -> sensor-service / comms-service / log-service
    #    （comms-service -> telemetry-gw -> state-store/log-service，遥测由此嵌套落库）
    s2, engage_body = tracing.traced_json_request(
        "POST", "http://fire-control:8080/engage",
        body={"id": "TGT-1042", "type": "armor", "scenario": scenario},
        timeout=6.0, span_name="POST fire-control/engage")
    call_chain.append({"to": "fire-control", "op": "engage", "status": s2,
                       "trace_id": trace_id})

    del call_chain[:-60]  # 只保留最近 60 条，避免长跑内存泄漏
    return {
        "scenario": scenario,
        "trace_id": trace_id,
        "scenario_data": scenario_body,
        "engage": engage_body,
    }


@app.get("/chain")
def get_chain():
    return {"chain": call_chain[-20:]}
