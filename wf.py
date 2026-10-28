#!/usr/bin/env python3
# 简易工作流引擎：按 YAML 配置顺序执行步骤，支持断点续跑、单步调试、跳步等
import argparse
import json
import signal
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path

ACTIONS = {}


def register_action(name):
    """动作注册装饰器，动作函数需签名 (ctx, params)"""
    def decorator(fn):
        ACTIONS[name] = fn
        return fn
    return decorator


class StepTimeout(Exception):
    """步骤执行超时（由 workflow 的 timeout 字段触发）"""


def _jsonable(obj):
    """把上下文转换成可落盘的 JSON；不支持的复杂对象降级为字符串。"""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        if isinstance(obj, dict):
            return {str(k): _jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_jsonable(v) for v in obj]
        return str(obj)


def _run_with_timeout(fn, timeout_s):
    """在 Linux 主线程上用 SIGALRM 给单个步骤加超时。

    动作内部的 subprocess 仍应自带 timeout；这里兜底防止某个动作无限阻塞。
    """
    if not timeout_s or timeout_s <= 0:
        return fn()

    def _handler(signum, frame):
        raise StepTimeout(f"step timed out after {timeout_s:.0f}s")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)



class WorkflowEngine:
    def __init__(self, config_path, exp_id=None):
        self.config_path = config_path
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        # 实验ID及运行目录
        self.exp_id = exp_id or f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.run_dir = Path("runs") / self.exp_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.state_file = self.run_dir / "state.json"
        self.ctx_file = self.run_dir / "ctx.json"
        # 上下文对象，传递给所有动作和算法。
        # 关键字段（inject_time / fault_info / k8s_watch 等）会落盘，
        # 这样 `--from <step>` 断点续跑时不会丢失注入时刻。
        self.ctx = {
            "exp_id": self.exp_id,
            "config": self.config,
            "vars": self.config.get("vars", {}),
            "start_time": time.time(),
            "timestamps": {},
            "run_dir": str(self.run_dir),
        }
        self.ctx = self.load_ctx()

        self.state = self.load_state()

    def load_ctx(self):
        """加载上次运行落盘的上下文；配置/路径等运行期字段始终以本次为准。"""
        if not self.ctx_file.exists():
            return self.ctx
        try:
            with open(self.ctx_file) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return self.ctx

        saved.update({
            "exp_id": self.exp_id,
            "config": self.config,
            "vars": self.config.get("vars", {}),
            "run_dir": str(self.run_dir),
        })
        saved.setdefault("timestamps", {})
        saved.setdefault("start_time", time.time())
        return saved

    def save_ctx(self):
        """把上下文落盘到 runs/<exp_id>/ctx.json。"""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.ctx_file, "w") as f:
            json.dump(_jsonable(self.ctx), f, indent=2, ensure_ascii=False)

    def load_state(self):
        """加载或初始化实验状态"""
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        else:
            state = {
                "exp_id": self.exp_id,
                "status": "PENDING",
                "steps": {}
            }
            for step in self.config.get("steps", []):
                state["steps"][step["id"]] = {
                    "id": step["id"],
                    "name": step["name"],
                    "status": "PENDING",
                    "start_time": None,
                    "end_time": None,
                    "error": None,
                    "result": None
                }
            self.save_state(state)
            return state

    def save_state(self, state=None):
        """保存状态到 runs/<exp_id>/state.json"""
        if state is None:
            state = self.state
        state["updated_at"] = datetime.now().isoformat()
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def resolve_vars(self, obj):
        """递归解析参数中的 ${vars.xxx}、${timestamps.xxx} 等变量引用"""
        if isinstance(obj, str):
            if obj.startswith("${") and obj.endswith("}"):
                var_path = obj[2:-1]
                parts = var_path.split(".")
                val = self.ctx
                for p in parts:
                    if p == "vars" and "vars" in val:
                        val = val["vars"]
                    elif isinstance(val, dict) and p in val:
                        val = val[p]
                    else:
                        return obj
                return val
            return obj
        elif isinstance(obj, dict):
            return {k: self.resolve_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.resolve_vars(v) for v in obj]
        return obj

    def run_step(self, step):
        """执行单个步骤：查找动作并调用，记录耗时和结果"""
        step_id = step["id"]
        action_name = step["action"]

        if step_id not in self.state["steps"]:
            self.state["steps"][step_id] = {"status": "PENDING"}

        step_state = self.state["steps"][step_id]
        # 已成功且非 always_run 则跳过
        if step_state["status"] == "SUCCEEDED" and not step.get("always_run", False):
            print(f"[SKIP] {step_id}: {step['name']} (already succeeded)")
            return True

        print(f"[RUN] {step_id}: {step['name']}")
        step_state["status"] = "RUNNING"
        step_state["start_time"] = time.time()
        self.save_state()

        try:
            if action_name not in ACTIONS:
                raise Exception(f"Action {action_name} not found")

            params = self.resolve_vars(step.get("params", {}))
            timeout = step.get("timeout")
            result = _run_with_timeout(
                lambda: ACTIONS[action_name](self.ctx, params),
                float(timeout) if timeout else None,
            )

            step_state["status"] = "SUCCEEDED"
            step_state["end_time"] = time.time()
            step_state["result"] = _jsonable(result)
            step_state["duration"] = step_state["end_time"] - step_state["start_time"]

            # 可选：将步骤开始时间记录到上下文（如 inject_time）
            if step.get("record_time"):
                self.ctx["timestamps"][step["record_time"]] = step_state["start_time"]
                self.ctx[step["record_time"]] = step_state["start_time"]

            self.save_ctx()
            self.save_state()
            print(f"[OK] {step_id} completed in {step_state['duration']:.2f}s")
            return True
        except StepTimeout as e:
            step_state["status"] = "FAILED"
            step_state["end_time"] = time.time()
            step_state["error"] = str(e)
            step_state["duration"] = step_state["end_time"] - step_state["start_time"]
            self.save_ctx()
            self.save_state()
            print(f"[FAIL] {step_id}: {e}")
            return False
        except Exception as e:
            step_state["status"] = "FAILED"
            step_state["end_time"] = time.time()
            step_state["error"] = str(e)
            if "start_time" in step_state and step_state["start_time"]:
                step_state["duration"] = step_state["end_time"] - step_state["start_time"]
            self.save_ctx()
            self.save_state()
            print(f"[FAIL] {step_id}: {e}")
            return False

    def _validate_steps(self, steps):
        if not isinstance(steps, list) or not steps:
            raise ValueError("config.steps 不能为空")
        seen = set()
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("每个 step 必须是字典")
            for key in ("id", "name", "action"):
                if not step.get(key):
                    raise ValueError(f"step 缺少字段 {key}: {step}")
            if step["id"] in seen:
                raise ValueError(f"step id 重复: {step['id']}")
            seen.add(step["id"])

    def run(self, from_step=None, skip_steps=None, single_step=None):
        """运行工作流：支持从某步开始、跳过步骤、单步调试"""
        skip_steps = skip_steps or []
        steps = self.config.get("steps", [])
        self._validate_steps(steps)

        self.state["status"] = "RUNNING"
        self.save_ctx()
        self.save_state()

        # 确定起始索引，断点续跑时标记之前未成功的步骤为 SKIPPED
        start_idx = 0
        if from_step:
            found = False
            for i, s in enumerate(steps):
                if s["id"] == from_step:
                    start_idx = i
                    found = True
                    break
            if not found:
                raise ValueError(f"--from 指定的 step 不存在: {from_step}")
            for i in range(start_idx):
                sid = steps[i]["id"]
                if self.state["steps"].get(sid, {}).get("status") != "SUCCEEDED":
                    if sid not in skip_steps:
                        self.state["steps"][sid]["status"] = "SKIPPED"

        # 单步调试模式
        if single_step:
            for step in steps:
                if step["id"] == single_step:
                    success = self.run_step(step)
                    self.state["status"] = "COMPLETED" if success else "FAILED"
                    self.save_ctx()
                    self.save_state()
                    return success
            raise ValueError(f"--step 指定的 step 不存在: {single_step}")

        # 顺序执行
        for i, step in enumerate(steps[start_idx:], start_idx):
            step_id = step["id"]
            if step_id in skip_steps:
                print(f"[SKIP] {step_id}: {step['name']} (skipped by user)")
                self.state["steps"][step_id]["status"] = "SKIPPED"
                self.save_state()
                continue

            if self.state["steps"].get(step_id, {}).get("status") == "SUCCEEDED" and not step.get("always_run"):
                continue

            success = self.run_step(step)
            if not success and not step.get("continue_on_error", False) and not step.get("always_run", False):
                print(f"Workflow failed at step {step_id}")
                self.state["status"] = "FAILED"
                self.save_ctx()
                self.save_state()
                # 执行 always_run 步骤（如 cleanup）
                for s in steps[i + 1 :]:
                    if s.get("always_run"):
                        self.run_step(s)
                return False

        self.state["status"] = "COMPLETED"
        self.save_ctx()
        self.save_state()
        print(f"\nWorkflow completed: {self.exp_id}")
        return True
    
    def status(self):
        """打印实验状态概览"""
        print(f"Experiment: {self.exp_id}")
        print(f"Status: {self.state['status']}")
        print(f"\nSteps:")
        for step_id, step_state in self.state["steps"].items():
            status = step_state["status"]
            dur = step_state.get("duration")
            dur_str = f"({dur:.2f}s)" if dur else ""
            print(f"  [{status:10}] {step_id} {dur_str}")
            if step_state.get("error"):
                print(f"               Error: {step_state['error']}")
        if self.ctx_file.exists():
            print(f"\nContext : {self.ctx_file}")


def main():
    parser = argparse.ArgumentParser(description="Simple Workflow Engine for Simulation Testing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--id", dest="exp_id")
    run_parser.add_argument("--from", dest="from_step")
    run_parser.add_argument("--skip")
    run_parser.add_argument("--step", dest="single_step")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--id", required=True)

    args = parser.parse_args()

    # 以 `python wf.py` 方式运行时，__main__ 和 actions 中 import 的 wf 是
    # 两个不同的模块对象，会导致动作注册到另一份 ACTIONS 里。这里统一别名。
    sys.modules["wf"] = sys.modules["__main__"]

    # 注册所有动作（按需加载）
    import actions.k8s  # noqa
    import actions.fault  # noqa
    import actions.obs  # noqa
    import actions.algo  # noqa
    import actions.measure  # noqa
    import actions.ross  # noqa
    import actions.trace  # noqa
    import actions.state  # noqa
    import actions.eval  # noqa

    if args.command == "run":
        skip = args.skip.split(",") if args.skip else []
        engine = WorkflowEngine(args.config, args.exp_id)
        try:
            success = engine.run(from_step=args.from_step, skip_steps=skip, single_step=args.single_step)
        except ValueError as e:
            print(f"[ERROR] {e}")
            raise SystemExit(2)
        raise SystemExit(0 if success else 1)
    elif args.command == "status":
        state_file = Path("runs") / args.id / "state.json"
        if not state_file.exists():
            print(f"[ERROR] 实验不存在或尚未运行: {args.id} ({state_file})")
            raise SystemExit(2)
        engine = WorkflowEngine.__new__(WorkflowEngine)
        engine.exp_id = args.id
        engine.run_dir = Path("runs") / args.id
        engine.state_file = state_file
        engine.ctx_file = engine.run_dir / "ctx.json"
        engine.state = engine.load_state()
        engine.status()

if __name__ == "__main__":
    main()
