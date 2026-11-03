"""运行编排：共同修复阶段 + 三条验证臂（R1 逐步回调 / R2 决策图 / R3 故障注入）。

所有 arm 从同一共同检查点 C0 出发；各臂工作区为独立副本；
所有模型调用经统一入口；所有工具经白名单工具层；所有事件进 JSONL。
"""

import json
import os
import shutil
import subprocess

from . import prompts
from .config import FROZEN, config_hash, canonical_config
from .events import EventLog
from .llm import ModelClient, BudgetExceededError
from .tools import ToolLayer, content_hash
from .graph import validate_graph, next_eligible_node, graph_hash, GraphValidationError

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE, ".venv", "bin", "python")


def parse_model_json(content):
    """从模型回复中提取第一个 JSON 对象；失败返回 (None, reason)。"""
    if not content or not content.strip():
        return None, "empty content"
    text = content.strip()
    if "```" in text:
        # 去掉代码围栏
        parts = text.split("```")
        candidates = [p for p in parts]
        text = "\n".join(candidates)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object found"
    snippet = text[start:end + 1]
    try:
        return json.loads(snippet), None
    except json.JSONDecodeError as exc:
        return None, "json decode error: %s" % exc


def receipt_feedback(receipt, note=None):
    """把真实回执压缩成给模型看的用户消息。"""
    payload = {
        "receipt_id": receipt.get("tool_receipt_id"),
        "tool": receipt.get("tool_name"),
        "args": receipt.get("args"),
        "exit_code": receipt.get("exit_code"),
        "stdout_tail": (receipt.get("stdout") or "")[-2500:],
        "stderr_tail": (receipt.get("stderr") or "")[-1500:],
    }
    if receipt.get("injected_fault"):
        payload["injected_fault"] = receipt["injected_fault"]
    if receipt.get("error"):
        payload["tool_error"] = receipt["error"]
    if note:
        payload["note"] = note
    return json.dumps(payload, ensure_ascii=False)


class Session:
    def __init__(self, session_dir):
        self.dir = os.path.abspath(session_dir)
        self.session_id = os.path.basename(self.dir)
        os.makedirs(self.dir, exist_ok=True)
        self.config_snapshot_path = os.path.join(self.dir, "session_config.json")
        if not os.path.exists(self.config_snapshot_path):
            with open(self.config_snapshot_path, "w", encoding="utf-8") as fh:
                json.dump({"frozen_config": FROZEN, "config_hash": config_hash()},
                          fh, ensure_ascii=False, indent=2)
        self.ckpt_dir = os.path.join(self.dir, "checkpoints", "c0")

    # ---------------- infrastructure ----------------
    def _new_run(self, name, arm, fault_inject=False, budget_calls=None,
                 ws_from=None, client_factory=None, tool_factory=None):
        run_id = "%s-%s" % (self.session_id, name)
        run_dir = os.path.join(self.dir, name)
        os.makedirs(run_dir, exist_ok=True)
        ws_dir = os.path.join(run_dir, "workspace")
        if os.path.exists(ws_dir):
            shutil.rmtree(ws_dir)
        shutil.copytree(ws_from, ws_dir,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "dist"))
        el = EventLog(os.path.join(run_dir, "events.jsonl"), run_id, arm,
                      FROZEN["task_id"], config_hash())
        if tool_factory is not None:
            tl = tool_factory(ws_dir, el, run_dir)
        else:
            tl = ToolLayer(ws_dir, event_log=el, fault_inject=fault_inject,
                           receipts_dir=os.path.join(run_dir, "receipts"))
        el.set_workspace_hash(tl.workspace_hash())
        if client_factory is not None:
            client = client_factory(run_id, arm, el, run_dir)
        else:
            client = ModelClient(
                run_id, arm,
                ledger_path=os.path.join(run_dir, "ledger.jsonl"),
                raw_dir=os.path.join(run_dir, "raw"),
                event_log=el,
                budget_calls=budget_calls or FROZEN["budget_arm_calls"],
                budget_tokens=FROZEN["budget_max_total_tokens_per_run"],
            )
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
            json.dump({"run_id": run_id, "arm": arm, "fault_inject": fault_inject,
                       "config_hash": config_hash()}, fh, indent=2)
        return run_id, run_dir, ws_dir, el, tl, client

    def _run_scorer(self, el, ws_dir):
        proc = subprocess.run(
            [VENV_PY, os.path.join(BASE, "tools", "scorer.py"), "--workspace", ws_dir],
            capture_output=True, text=True, timeout=600)
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-4:])
        el.emit("scorer_run", data={"exit_code": proc.returncode, "output_tail": tail[-1200:]})
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    # ---------------- common repair phase ----------------
    def run_common(self, client_factory=None):
        if os.path.exists(os.path.join(self.ckpt_dir, "checkpoint.json")):
            print("[common] checkpoint exists, skip")
            return
        run_id, run_dir, ws_dir, el, tl, client = self._new_run(
            "common", "common", budget_calls=FROZEN["budget_common_calls"],
            ws_from=os.path.join(BASE, "workspace"),
            client_factory=client_factory)
        el.emit("run_start", data={"phase": "common_repair"})
        messages = [
            {"role": "system", "content": prompts.common_system_prompt()},
            {"role": "user", "content": "工作区在当前目录。请开始：先读取相关文件理解契约，"
                                        "然后修复 config_parser.py 并完成验证链。"},
        ]
        done = False
        status = "unknown"
        try:
            while not done:
                content, rec = client.call("common_repair", messages,
                                           route_hint="slow")
                messages.append({"role": "assistant", "content": content})
                parsed, perr = parse_model_json(content)
                if parsed is None:
                    el.emit("fallback", fallback_reason="model_reply_unparseable: %s" % perr)
                    messages.append({"role": "user", "content":
                        "你的回复不是协议要求的单个 JSON 对象（%s）。"
                        "请重新回复：严格按回复协议输出单个 JSON。" % perr})
                    continue
                if parsed.get("phase_complete"):
                    code, out = self._run_scorer(el, ws_dir)
                    if code == 0:
                        done = True
                        status = "repaired"
                        messages.append({"role": "user", "content":
                            "独立评分器：PASS。共同修复阶段结束。\n" + out[-800:]})
                    else:
                        el.emit("fallback", fallback_reason="scorer_failed_after_phase_complete")
                        messages.append({"role": "user", "content":
                            "独立评分器：FAIL。请继续修复。评分器输出：\n" + out[-2500:]})
                    continue
                action = parsed.get("action")
                if not isinstance(action, dict):
                    el.emit("fallback", fallback_reason="missing_action_in_reply")
                    messages.append({"role": "user", "content":
                        "回复缺少 action 对象。请按协议重试。"})
                    continue
                try:
                    receipt = tl.execute(action.get("tool_name"), action.get("args"))
                except Exception as exc:
                    el.emit("fallback", fallback_reason="tool_rejected: %s" % exc)
                    messages.append({"role": "user", "content":
                        "工具调用被拒绝（未执行）：%s。请修正参数后重试。" % exc})
                    continue
                messages.append({"role": "user", "content": receipt_feedback(receipt)})
        except BudgetExceededError as exc:
            el.emit("run_end", data={"status": "budget_exceeded", "reason": str(exc)})
            raise
        el.emit("run_end", data={"status": status, "model_calls": client.summary()})
        if status != "repaired":
            raise RuntimeError("common phase did not repair the task")
        # 保存共同检查点
        from .checkpoint import create_checkpoint
        meta = create_checkpoint(self.ckpt_dir, ws_dir, messages, client.summary(),
                                 run_id, config_hash(), tl.workspace_hash())
        el.emit("checkpoint_saved", data={"checkpoint_id": "c0",
                                          "workspace_hash": meta["workspace_hash"],
                                          "ledger_prefix": meta["ledger_prefix"]})
        print("[common] repaired, checkpoint saved. calls=%s" % client.summary()["model_calls"])

    # ---------------- R1: step-by-step callback baseline ----------------
    def run_r1(self, client_factory=None):
        ckpt = self._load_ckpt()
        run_id, run_dir, ws_dir, el, tl, client = self._new_run(
            "arm_r1", "R1", ws_from=os.path.join(self.ckpt_dir, "workspace"),
            client_factory=client_factory)
        el.emit("run_start", data={"phase": "verification", "mode": "R1_step_by_step",
                                   "checkpoint_workspace_hash": ckpt["workspace_hash"]})
        messages = [
            {"role": "system", "content": prompts.arm_system_prompt("r1")},
        ] + ckpt["conversation"][1:] + [
            {"role": "user", "content": "验证阶段开始（R1 模式）。" + prompts.R1_MODE_RULES.split("# 本模式规则（R1：逐步回调基线）\n\n")[1]},
        ]
        self._step_loop(el, tl, client, messages, arm="R1")
        return client.summary()

    # ---------------- R2 / R3: persistent decision graph ----------------
    def run_graph_arm(self, name, arm, fault_inject, client_factory=None, tool_factory=None):
        ckpt = self._load_ckpt()
        run_id, run_dir, ws_dir, el, tl, client = self._new_run(
            name, arm, fault_inject=fault_inject,
            ws_from=os.path.join(self.ckpt_dir, "workspace"),
            client_factory=client_factory, tool_factory=tool_factory)
        el.emit("run_start", data={"phase": "verification", "mode": arm,
                                   "fault_inject": fault_inject,
                                   "checkpoint_workspace_hash": ckpt["workspace_hash"]})
        mode_rules = prompts.GRAPH_MODE_RULES.split("# 本模式规则（R2/R3：持续决策图与回调替代）\n\n")[1]
        messages = [
            {"role": "system", "content": prompts.arm_system_prompt("graph")},
        ] + ckpt["conversation"][1:] + [
            {"role": "user", "content": "验证阶段开始（%s 模式）。%s" % (arm, mode_rules)},
        ]
        self._graph_loop(el, tl, client, messages, arm=arm, run_dir=run_dir)
        return client.summary()

    # ---------------- shared loops ----------------
    def _load_ckpt(self):
        from .checkpoint import load_checkpoint
        path = os.path.join(self.ckpt_dir, "checkpoint.json")
        if not os.path.exists(path):
            raise RuntimeError("checkpoint c0 missing; run common phase first")
        return load_checkpoint(self.ckpt_dir)

    def _step_loop(self, el, tl, client, messages, arm):
        """R1 循环：每个工具返回后都调用模型，模型每次只决定一个动作。"""
        status = "unknown"
        bypass_count = 0
        try:
            while True:
                content, rec = client.call("arm_decision", messages, route_hint="slow")
                messages.append({"role": "assistant", "content": content})
                parsed, perr = parse_model_json(content)
                if parsed is None:
                    el.emit("fallback", fallback_reason="model_reply_unparseable: %s" % perr)
                    messages.append({"role": "user", "content":
                        "回复必须是单个 JSON 对象（%s）。请重新回复。" % perr})
                    continue
                if parsed.get("phase_complete") or parsed.get("task_complete"):
                    code, out = self._run_scorer(el, tl.ws)
                    if code == 0:
                        status = "completed"
                        break
                    el.emit("fallback", fallback_reason="scorer_failed_after_phase_complete")
                    messages.append({"role": "user", "content":
                        "独立评分器：FAIL（%s 模式要求全部验证真实通过）。请继续。\n%s"
                        % (arm, out[-2000:])})
                    continue
                action = parsed.get("action")
                if not isinstance(action, dict):
                    el.emit("fallback", fallback_reason="missing_action_in_reply")
                    messages.append({"role": "user", "content": "回复缺少 action。请按协议重试。"})
                    continue
                try:
                    receipt = tl.execute(action.get("tool_name"), action.get("args"))
                except Exception as exc:
                    el.emit("fallback", fallback_reason="tool_rejected: %s" % exc)
                    messages.append({"role": "user", "content":
                        "工具调用被拒绝（未执行）：%s" % exc})
                    continue
                messages.append({"role": "user", "content": receipt_feedback(receipt)})
        except BudgetExceededError as exc:
            el.emit("run_end", data={"status": "budget_exceeded", "reason": str(exc),
                                     "model_calls": client.summary()})
            return
        el.emit("run_end", data={"status": status, "bypasses": bypass_count,
                                 "model_calls": client.summary()})

    def _graph_loop(self, el, tl, client, messages, arm, run_dir):
        """R2/R3 循环：GDR 持续决策图 + CCS 认证回调替代 + 守卫失败回退。"""
        graph = None
        node_status = {}
        receipts = {}
        graphs_dir = os.path.join(run_dir, "graphs")
        boundary_seq = 0
        status = "unknown"
        try:
            while True:
                if graph is None:
                    # ---- 慢路径：调用模型 ----
                    content, rec = client.call("arm_decision", messages, route_hint="slow")
                    messages.append({"role": "assistant", "content": content})
                    parsed, perr = parse_model_json(content)
                    if parsed is None:
                        el.emit("fallback", fallback_reason="model_reply_unparseable: %s" % perr)
                        messages.append({"role": "user", "content":
                            "回复必须是单个 JSON 对象（%s）。请重新回复。" % perr})
                        continue
                    if parsed.get("task_complete") or parsed.get("phase_complete"):
                        code, out = self._run_scorer(el, tl.ws)
                        if code == 0:
                            status = "completed"
                            break
                        el.emit("fallback", fallback_reason="scorer_failed_after_task_complete")
                        messages.append({"role": "user", "content":
                            "独立评分器：FAIL。请继续验证。\n%s" % out[-2000:]})
                        continue
                    action = parsed.get("action")
                    if not isinstance(action, dict):
                        el.emit("fallback", fallback_reason="missing_action_in_reply")
                        messages.append({"role": "user", "content": "回复缺少 action。请按协议重试。"})
                        continue
                    try:
                        receipt = tl.execute(action.get("tool_name"), action.get("args"))
                    except Exception as exc:
                        el.emit("fallback", fallback_reason="tool_rejected: %s" % exc)
                        messages.append({"role": "user", "content":
                            "工具调用被拒绝（未执行）：%s" % exc})
                        continue
                    receipts["__root__"] = receipt
                    messages.append({"role": "user", "content": receipt_feedback(
                        receipt, note="root action receipt")})
                    # ---- 尝试接收模型创作的图（必须真实由模型提交）----
                    gobj = parsed.get("graph")
                    if isinstance(gobj, dict):
                        try:
                            g = validate_graph(gobj)
                            g["workspace_hash_at_authoring"] = tl.workspace_hash()
                            g["authored_in_request_id"] = rec["request_id"]
                            g["graph_hash"] = graph_hash(g)
                            os.makedirs(graphs_dir, exist_ok=True)
                            with open(os.path.join(graphs_dir, "%s.json" % g["graph_id"]),
                                      "w", encoding="utf-8") as fh:
                                json.dump(g, fh, ensure_ascii=False, indent=2)
                            graph = g
                            node_status = {n["node_id"]: "pending" for n in g["nodes"]}
                            el.emit("graph_authored",
                                    graph_id=g["graph_id"],
                                    model_request_id=rec["request_id"],
                                    data={"graph": g, "node_count": len(g["nodes"]),
                                          "workspace_hash_at_authoring": g["workspace_hash_at_authoring"]})
                        except GraphValidationError as exc:
                            el.emit("graph_invalidated", graph_id=gobj.get("graph_id"),
                                    fallback_reason="graph_validation_failed: %s" % exc)
                            messages.append({"role": "user", "content":
                                "提交的决策图未通过格式校验（%s）。图已丢弃，继续正常模式。" % exc})
                            graph = None
                else:
                    # ---- GDR：检查剩余节点资格（守卫只消费真实回执）----
                    el.set_workspace_hash(tl.workspace_hash())
                    if tl.workspace_hash() != graph["workspace_hash_at_authoring"]:
                        el.emit("graph_invalidated", graph_id=graph["graph_id"],
                                fallback_reason="workspace_changed_since_authoring")
                        graph = None
                        messages.append({"role": "user", "content":
                            "工作区在图创作后发生了变化，剩余图已失效。请重新评估下一步。"})
                        continue
                    node, reason = next_eligible_node(graph, node_status, receipts)
                    if node is not None:
                        # ---- CCS：认证回调替代（无模型调用）----
                        boundary_seq += 1
                        authored_hash = content_hash(node["args"])
                        receipt = tl.execute(node["tool_name"], node["args"])
                        executed_hash = content_hash(receipt["args"])
                        pred = node["depends_on"]
                        pred_receipt = receipts.get("__root__" if pred == "root" else pred)
                        node_status[node["node_id"]] = "executed"
                        receipts[node["node_id"]] = receipt
                        el.emit("bypass_certificate",
                                graph_id=graph["graph_id"],
                                node_id=node["node_id"],
                                route="edg",
                                model_request_id=None,
                                bypassed_boundary_id="boundary-%s-%02d" % (arm, boundary_seq),
                                prior_receipt_id=pred_receipt.get("tool_receipt_id") if pred_receipt else None,
                                prior_receipt_hash=pred_receipt.get("tool_receipt_hash") if pred_receipt else None,
                                guard_result="pass",
                                guard_reason="authored guard satisfied by real receipt",
                                authored_args_hash=authored_hash,
                                executed_args_hash=executed_hash,
                                tool_receipt_id=receipt["tool_receipt_id"],
                                tool_receipt_hash=receipt["tool_receipt_hash"],
                                data={"tool_name": node["tool_name"], "args": node["args"],
                                      "exit_code": receipt.get("exit_code"),
                                      "stdout_tail": (receipt.get("stdout") or "")[-600:],
                                      "injected_fault": receipt.get("injected_fault")})
                        messages.append({"role": "user", "content": receipt_feedback(
                            receipt, note="auto-executed saved node %s (no model call)" % node["node_id"])})
                        continue
                    if reason == "graph_exhausted":
                        el.emit("graph_exhausted", graph_id=graph["graph_id"],
                                data={"executed_nodes": sorted(node_status)})
                        messages.append({"role": "user", "content":
                            "GRAPH_EXHAUSTED\n" + prompts.FINAL_SUMMARY_RULES})
                        graph = None
                        continue
                    # ---- 守卫失败/未知：图失效 + 回退到模型 ----
                    el.emit("graph_invalidated", graph_id=graph["graph_id"],
                            guard_result="failed" if reason.startswith("guard_failed") else "unknown",
                            fallback_reason=reason)
                    graph = None
                    last_receipt = receipts.get("__root__")
                    messages.append({"role": "user", "content":
                        "GUARD_FAILED: %s\n剩余决策图已失效（不会自动继续）。"
                        "真实回执见上一条消息。请评估并决定下一步"
                        "（可以修复后重新提交动作与图，或完成总结）。" % reason})
                    continue
        except BudgetExceededError as exc:
            el.emit("run_end", data={"status": "budget_exceeded", "reason": str(exc),
                                     "model_calls": client.summary()})
            return
        el.emit("run_end", data={"status": status, "model_calls": client.summary(),
                                 "bypasses": sum(1 for e in el.events
                                                 if e["event_type"] == "bypass_certificate")})
