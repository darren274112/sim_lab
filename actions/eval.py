# 仿真结果评估动作：调用 eval-service 对 runs/<exp_id> 做量化评估
from wf import register_action
import json
from kubernetes import client, config
from kubernetes.stream import stream

config.load_kube_config()
v1 = client.CoreV1Api()


def _call_eval(ctx, exp_id: str):
    namespace = ctx["config"].get("namespace", "sim-test")
    pods = v1.list_namespaced_pod(namespace, label_selector="app=orchestrator")
    if not pods.items:
        raise RuntimeError("找不到 orchestrator Pod（label app=orchestrator）")
    pod_name = pods.items[0].metadata.name

    script = (
        "import json,urllib.request,sys\n"
        "eid=sys.argv[1]\n"
        "try:\n"
        "    r=urllib.request.urlopen('http://eval-service:8080/eval/'+eid,timeout=15)\n"
        "    print(r.read().decode())\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok':False,'error':str(e)}))\n"
    )
    resp = stream(v1.connect_get_namespaced_pod_exec,
                  pod_name, namespace,
                  command=["python", "-c", script, exp_id],
                  stderr=True, stdin=False, stdout=True, tty=False)
    try:
        return json.loads(resp.strip())
    except json.JSONDecodeError:
        raise RuntimeError(f"eval-service 调用失败，原始输出:\n{resp}")


@register_action("eval.run")
def eval_run(ctx, params):
    """对当前或指定实验ID执行评估，结果写回 ctx。"""
    exp_id = params.get("exp_id") or ctx.get("exp_id")
    if not exp_id:
        raise ValueError("eval.run 需要 exp_id 参数或 ctx.exp_id")
    print(f"[EVAL] Evaluating simulation results for {exp_id} ...")
    report = _call_eval(ctx, exp_id)
    ctx["eval_report"] = report
    if report.get("ok"):
        ctx["eval_scores"] = report.get("scores")
        s = report.get("scores") or {}
        print(f"[EVAL] OK scores={s} grade={s.get('grade')}")
    else:
        print(f"[EVAL] FAILED: {report.get('error')}")
    return report


@register_action("eval.summary")
def eval_summary(ctx, params):
    """输出评估摘要"""
    r = ctx.get("eval_report") or {}
    if not r.get("ok"):
        print("[EVAL] No valid eval report in ctx")
        return {"ok": False}
    s = r.get("scores") or {}
    print("============================================================")
    print("SIMULATION RESULT EVALUATION")
    print("============================================================")
    print(f"exp_id: {r.get('exp_id')}  grade: {s.get('grade')}  overall: {s.get('overall')}")
    print(f"perception: {s.get('perception')}  tolerance: {s.get('tolerance')}  "
          f"consistency: {s.get('consistency')}  tracing: {s.get('tracing')}")
    for k in ["perception", "tolerance", "consistency", "tracing"]:
        d = (r.get("details") or {}).get(k) or {}
        if d.get("notes"):
            print(f"[{k}] {d.get('notes')}")
    print("============================================================")
    return {"ok": True, "scores": s}
