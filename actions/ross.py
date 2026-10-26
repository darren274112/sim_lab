# ROSS 仿真引擎动作（桥接接口）
from wf import register_action
import json
import time
from kubernetes import client, config
from kubernetes.stream import stream

config.load_kube_config()
v1 = client.CoreV1Api()


def _call_sim_bridge(ctx, fault=None, end_time=20.0, units=10):
    """在 orchestrator Pod 内调用 sim-bridge：可选先注入故障，再 /start，轮询 /status 至完成。

    通过 kubernetes exec 进入 orchestrator（集群内可解析 sim-bridge:8080），
    避免在集群外直接访问集群内部 Service。exec API 需要具体 Pod 名，
    因此先按标签 app=orchestrator 取一个 Running 的 Pod。
    """
    namespace = ctx["config"].get("namespace", "sim-test")
    pods = v1.list_namespaced_pod(namespace, label_selector="app=orchestrator")
    if not pods.items:
        raise RuntimeError("找不到 orchestrator Pod（label app=orchestrator）")
    pod_name = pods.items[0].metadata.name

    payload = json.dumps({
        "end_time": end_time,
        "units": units,
        "fault": fault,
    })
    # 在 orchestrator 内执行的脚本：纯 stdlib，无外部依赖
    script = (
        "import sys,json,time,urllib.request\n"
        "p=json.loads(sys.argv[1])\n"
        "base='http://sim-bridge:8080'\n"
        "def call(u,m='GET'):\n"
        "    req=urllib.request.Request(base+u,method=m)\n"
        "    return json.loads(urllib.request.urlopen(req,timeout=30).read().decode())\n"
        "f=p.get('fault')\n"
        "if f:\n"
        "    call('/inject_fault?fault_type=%s&target_lp=%d&at_time=%s'%(f['type'],int(f['target_lp']),float(f['at_time'])),'POST')\n"
        "start=call('/start?end_time=%s&units=%d'%(p['end_time'],p['units']),'POST')\n"
        "st={}\n"
        "for _ in range(240):\n"
        "    st=call('/status')\n"
        "    if not st.get('running'): break\n"
        "    time.sleep(0.5)\n"
        "print(json.dumps({'start':start,'status':st,'fault':f},ensure_ascii=False))\n"
    )
    resp = stream(v1.connect_get_namespaced_pod_exec,
                  pod_name, namespace,
                  command=["python", "-c", script, payload],
                  stderr=True, stdin=False, stdout=True, tty=False)
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        raise RuntimeError(f"sim-bridge 调用失败，原始输出:\n{resp}")


@register_action("ross.check_ready")
def ross_check_ready(ctx, params):
    """检查 ROSS 仿真引擎状态"""
    print("[ROSS] Checking ROSS simulation engine status...")
    return {"status": "ready", "engine": "ross"}


@register_action("ross.start_sim")
def ross_start_sim(ctx, params):
    """启动想定仿真：真正调用 sim-bridge 的 /start（可选先注入故障），并等待完成。"""
    scenario = params.get("scenario", "battalion_attack")
    end_time = float(params.get("end_time", ctx.get("vars", {}).get("end_time", 20.0)))
    units = int(params.get("units", 10))
    fault = params.get("fault")  # 可选：{"type":"lp_crash","target_lp":0,"at_time":5.0}

    print(f"[ROSS] Starting military simulation scenario '{scenario}' via sim-bridge "
          f"(end_time={end_time}, units={units}, fault={fault})...")
    result = _call_sim_bridge(ctx, fault=fault, end_time=end_time, units=units)

    st = result.get("status", {})
    ctx["ross_running"] = not st.get("running", False)
    ctx["ross_result"] = result
    ctx["ross_status"] = st
    ctx["ross_fault"] = result.get("fault")
    if fault:
        ctx["fault_info"] = {
            "type": fault.get("type"),
            "target_lp": fault.get("target_lp"),
            "time": time.time(),
        }

    rc = st.get("returncode")
    if rc != 0:
        raise Exception(f"ROSS sim exited with code {rc}: {st.get('error')}")
    print(f"[ROSS] Simulation finished: mode={st.get('mode')} events={st.get('events_processed')} "
          f"lp_count={st.get('lp_count')} fault_applied={st.get('fault') is not None}")
    return {"status": "started", "ross": st}
