"""基础设施测试：用明确标注的假模型客户端验证执行器、日志与审计。

这些测试不调用任何真实模型；工具层执行的是真实命令（真实回执）。
假客户端的所有账本记录带 synthetic: true，正式审计会在无 --allow-synthetic 时拒绝。
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tests_infra"))

from harness.runner import Session
from harness.graph import validate_graph, evaluate_guard, next_eligible_node, GraphValidationError
from harness.llm import BudgetExceededError
from fakes import fake_client_factory, ShiftingToolLayer, G_FULL, COMPLETE

VENV_PY = os.path.join(BASE, ".venv", "bin", "python")
REFERENCE = open(os.path.join(BASE, "reference", "solution_config_parser.py"),
                 encoding="utf-8").read()


@pytest.fixture()
def workspace_tmp(tmp_path):
    src = os.path.join(BASE, "workspace")
    ws = tmp_path / "ws"
    shutil.copytree(src, ws, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "dist"))
    return str(ws)


@pytest.fixture()
def repaired_session(tmp_path, workspace_tmp):
    """已带共同检查点（用参考答案直接修复）的会话。"""
    session = Session(str(tmp_path / "session"))
    # 直接构造 c0：把参考答案写入 workspace 副本，再走真实 checkpoint 逻辑
    ws_fixed = str(tmp_path / "ws_fixed")
    shutil.copytree(workspace_tmp, ws_fixed, dirs_exist_ok=True)
    with open(os.path.join(ws_fixed, "config_parser.py"), "w", encoding="utf-8") as fh:
        fh.write(REFERENCE)
    from harness.checkpoint import create_checkpoint
    from harness.config import config_hash
    from harness.tools import ToolLayer
    tl = ToolLayer(ws_fixed)
    create_checkpoint(session.ckpt_dir, ws_fixed,
                      [{"role": "system", "content": "sys"},
                       {"role": "user", "content": "u"}],
                      {"model_calls": 0, "synthetic": True},
                      "setup-run", config_hash(), tl.workspace_hash())
    return session


def load_events(run_dir):
    with open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def run_audit(run_dir, expect_mode, allow_synthetic=True):
    cmd = [VENV_PY, os.path.join(BASE, "tools", "audit.py"),
           "--run-dir", run_dir, "--expect-mode", expect_mode]
    if allow_synthetic:
        cmd.append("--allow-synthetic")
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


# ---------------- graph.py 单元 ----------------
class TestGraphValidation:
    def base_node(self, **kw):
        node = {"node_id": "n1", "tool_name": "run_command",
                "args": {"argv": ["python", "-m", "pytest", "tests/unit", "-q"]},
                "depends_on": "root", "guard": {"exit_code": 0}}
        node.update(kw)
        return {"graph_id": "g1", "nodes": [node]}

    def test_valid_chain(self):
        g = self.base_node()
        g["nodes"].append({"node_id": "n2", "tool_name": "run_command",
                           "args": {"argv": ["python", "-m", "pytest", "tests/integration", "-q"]},
                           "depends_on": "n1", "guard": {"exit_code": 0}})
        out = validate_graph(g)
        assert len(out["nodes"]) == 2 and out["nodes"][1]["order"] == 1

    def test_rejects_unknown_guard_key(self):
        g = self.base_node()
        g["nodes"][0]["guard"] = {"stdout_must_look_nice": "x"}
        with pytest.raises(GraphValidationError):
            validate_graph(g)

    def test_rejects_forward_dependency(self):
        g = self.base_node()
        g["nodes"][0]["depends_on"] = "n2"
        with pytest.raises(GraphValidationError):
            validate_graph(g)

    def test_rejects_nonliteral_args(self):
        g = self.base_node()
        g["nodes"][0]["args"] = {"argv": "python -m pytest"}  # 不是列表
        with pytest.raises(GraphValidationError):
            validate_graph(g)

    def test_rejects_unknown_tool(self):
        g = self.base_node()
        g["nodes"][0]["tool_name"] = "rm_rf"
        with pytest.raises(GraphValidationError):
            validate_graph(g)

    def test_rejects_empty_guard(self):
        g = self.base_node()
        g["nodes"][0]["guard"] = {}
        with pytest.raises(GraphValidationError):
            validate_graph(g)

    def test_duplicate_node_id(self):
        g = self.base_node()
        g["nodes"].append(dict(g["nodes"][0]))
        with pytest.raises(GraphValidationError):
            validate_graph(g)


class TestGuards:
    R = {"exit_code": 0, "stdout": "ARTIFACT VERIFY: PASS", "stderr": ""}

    def test_pass(self):
        assert evaluate_guard({"exit_code": 0,
                               "stdout_contains": "ARTIFACT VERIFY: PASS"}, self.R)[0] == "pass"

    def test_failed_exit(self):
        assert evaluate_guard({"exit_code": 1}, self.R)[0] == "failed"

    def test_failed_contains(self):
        assert evaluate_guard({"stdout_contains": "NOPE"}, self.R)[0] == "failed"

    def test_unknown_missing_field(self):
        assert evaluate_guard({"exit_code": 0}, {"stdout": "x"})[0] == "unknown"


class TestNextEligible:
    def graph(self):
        g = validate_graph({"graph_id": "g", "nodes": [
            {"node_id": "n1", "tool_name": "run_command",
             "args": {"argv": ["python", "-m", "pytest", "tests/unit", "-q"]},
             "depends_on": "root", "guard": {"exit_code": 0}},
            {"node_id": "n2", "tool_name": "run_command",
             "args": {"argv": ["python", "-m", "pytest", "tests/integration", "-q"]},
             "depends_on": "n1", "guard": {"exit_code": 0}}]})
        return g

    def test_waits_for_predecessor(self):
        g = self.graph()
        status = {}
        receipts = {"__root__": {"exit_code": 0, "stdout": "", "stderr": ""}}
        node, reason = next_eligible_node(g, status, receipts)
        assert node["node_id"] == "n1"
        node2, reason2 = next_eligible_node(g, {"n1": "executed"}, receipts)
        assert node2 is None and "waiting" in reason2

    def test_guard_failure_stops(self):
        g = self.graph()
        status = {}
        receipts = {"__root__": {"exit_code": 1, "stdout": "", "stderr": "boom"}}
        node, reason = next_eligible_node(g, status, receipts)
        assert node is None and "guard_failed" in reason

    def test_exhausted(self):
        g = self.graph()
        receipts = {"__root__": {"exit_code": 0}, "n1": {"exit_code": 0},
                    "n2": {"exit_code": 0}}
        node, reason = next_eligible_node(g, {"n1": "executed", "n2": "executed"}, receipts)
        assert node is None and reason == "graph_exhausted"


# ---------------- 端到端夹具运行（假模型 + 真实工具层） ----------------
FIX = "python"



class TestEndToEnd:
    def test_common_phase_and_checkpoint(self, tmp_path, workspace_tmp):
        session = Session(str(tmp_path / "s"))
        script = [
            json.dumps({"thought": "write fix", "action": {
                "tool_name": "write_file",
                "args": {"path": "config_parser.py", "content": REFERENCE}}}),
            json.dumps({"phase_complete": True, "summary": "fixed"}),
        ]
        session.run_common(client_factory=fake_client_factory(script))
        ckpt_path = os.path.join(session.ckpt_dir, "checkpoint.json")
        assert os.path.exists(ckpt_path)
        meta = json.load(open(ckpt_path))
        assert meta["workspace_hash"].startswith("sha256:")
        # 事件日志包含 checkpoint_saved
        events = load_events(os.path.join(session.dir, "common"))
        assert any(e["event_type"] == "checkpoint_saved" for e in events)

    def test_r2_full_chain_with_bypasses(self, repaired_session):
        session = repaired_session
        session.run_r1(client_factory=fake_client_factory([
            json.dumps({"thought": "run unit", "action": {"tool_name": "run_command",
                        "args": {"argv": [FIX, "-m", "pytest", "tests/unit", "-q"]}}}),
            json.dumps({"thought": "run integration", "action": {"tool_name": "run_command",
                        "args": {"argv": [FIX, "-m", "pytest", "tests/integration", "-q"]}}}),
            json.dumps({"thought": "build", "action": {"tool_name": "run_command",
                        "args": {"argv": [FIX, "tools/build_artifact.py", "--out", "dist/package.zip"]}}}),
            json.dumps({"thought": "verify", "action": {"tool_name": "run_command",
                        "args": {"argv": [FIX, "tools/verify_artifact.py", "--file", "dist/package.zip"]}}}),
            json.dumps({"phase_complete": True, "summary": "done"}),
        ]))
        r1_dir = os.path.join(session.dir, "arm_r1")
        events = load_events(r1_dir)
        assert not any(e["event_type"] == "bypass_certificate" for e in events)
        code, out = run_audit(r1_dir, "R1")
        assert code == 0, out

    def test_r2_graph_bypass_and_audit(self, repaired_session):
        session = repaired_session
        session.run_graph_arm("arm_r2", "R2", fault_inject=False,
                              client_factory=fake_client_factory([G_FULL, COMPLETE]))
        r2_dir = os.path.join(session.dir, "arm_r2")
        events = load_events(r2_dir)
        bypasses = [e for e in events if e["event_type"] == "bypass_certificate"]
        # 同一张图跨反馈连续执行了 3 个节点，且全程无模型调用
        assert len(bypasses) == 3
        assert all(b["graph_id"] == "fake-g1" for b in bypasses)
        assert all(b.get("model_request_id") is None for b in bypasses)
        # 每个节点恰好执行一次；参数哈希一致
        assert len({b["node_id"] for b in bypasses}) == 3
        assert all(b["authored_args_hash"] == b["executed_args_hash"] for b in bypasses)
        # 前置回执引用存在
        receipt_ids = {e["tool_receipt_id"] for e in events if e["event_type"] == "tool_execute"}
        for b in bypasses:
            assert b.get("prior_receipt_id") in receipt_ids or b["node_id"] == "n_int"
        # 审计通过
        code, out = run_audit(r2_dir, "R2")
        assert code == 0, out
        # 回放生成 + 审计第 10 项
        p = subprocess.run([VENV_PY, os.path.join(BASE, "tools", "replay.py"),
                            "--run-dir", r2_dir], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        code, out = run_audit(r2_dir, "R2")
        assert code == 0, out
        assert "10_replay_matches_events" in out

    def test_r3_fault_blocks_downstream_and_falls_back(self, repaired_session):
        session = repaired_session
        retry_action = json.dumps({
            "thought": "integration failed; retrying integration then rebuilding",
            "action": {"tool_name": "run_command",
                       "args": {"argv": [FIX, "-m", "pytest", "tests/integration", "-q"]}},
            "graph": {"graph_id": "fake-g2", "nodes": [
                {"node_id": "r_build", "tool_name": "run_command",
                 "args": {"argv": [FIX, "tools/build_artifact.py", "--out", "dist/package.zip"]},
                 "depends_on": "root", "guard": {"exit_code": 0}},
                {"node_id": "r_verify", "tool_name": "run_command",
                 "args": {"argv": [FIX, "tools/verify_artifact.py", "--file", "dist/package.zip"]},
                 "depends_on": "r_build",
                 "guard": {"exit_code": 0, "stdout_contains": "BUILD OK"}},
            ]}}, ensure_ascii=False)
        session.run_graph_arm("arm_r3", "R3", fault_inject=True,
                              client_factory=fake_client_factory([G_FULL, retry_action, COMPLETE]))
        r3_dir = os.path.join(session.dir, "arm_r3")
        events = load_events(r3_dir)
        # 注入故障事件存在，且注入回执真实非零
        faults = [e for e in events if e["event_type"] == "fault_injected"]
        assert len(faults) == 1
        fault_seq = faults[0]["sequence"]
        receipts_dir = os.path.join(r3_dir, "receipts")
        injected_receipts = []
        for f in os.listdir(receipts_dir):
            r = json.load(open(os.path.join(receipts_dir, f)))
            if r.get("injected_fault"):
                injected_receipts.append(r)
        assert len(injected_receipts) == 1
        assert injected_receipts[0]["exit_code"] != 0
        # 故障后旧图（fake-g1）不得再自动执行任何节点（故障受害者节点本身除外）
        post_fault_bypasses = [e for e in events
                               if e["event_type"] == "bypass_certificate"
                               and e["sequence"] > fault_seq
                               and not (e.get("data") or {}).get("injected_fault")]
        assert all(b["graph_id"] != "fake-g1" for b in post_fault_bypasses)
        # 故障产生了 graph_invalidated，且回退后有真实（此处为合成的）模型请求
        assert any(e["event_type"] == "graph_invalidated" and e["sequence"] > fault_seq
                   for e in events)
        # 新图 fake-g2 在故障后被创作并跨反馈执行
        new_graph_bypasses = [b for b in post_fault_bypasses if b["graph_id"] == "fake-g2"]
        assert len(new_graph_bypasses) == 2
        # 审计
        code, out = run_audit(r3_dir, "R3")
        assert code == 0, out

    def test_workspace_change_invalidates_graph(self, repaired_session):
        session = repaired_session
        tool_factory = lambda ws, el, run_dir: ShiftingToolLayer(
            ws, event_log=el, receipts_dir=os.path.join(run_dir, "receipts"), shift_after=3)
        session.run_graph_arm("arm_r2", "R2", fault_inject=False,
                              client_factory=fake_client_factory([G_FULL, COMPLETE]),
                              tool_factory=tool_factory)
        r2_dir = os.path.join(session.dir, "arm_r2")
        events = load_events(r2_dir)
        inv = [e for e in events if e["event_type"] == "graph_invalidated"
               and "workspace_changed" in (e.get("fallback_reason") or "")]
        assert inv, "expected workspace-change invalidation"
        # 该场景专测失效路径，图未跨反馈执行属预期，审计用显式豁免
        cmd = [VENV_PY, os.path.join(BASE, "tools", "audit.py"),
               "--run-dir", r2_dir, "--expect-mode", "R2",
               "--allow-synthetic", "--allow-no-graph-reuse"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, p.stdout + p.stderr

    def test_audit_rejects_tampered_bypass(self, repaired_session):
        session = repaired_session
        session.run_graph_arm("arm_r2", "R2", fault_inject=False,
                              client_factory=fake_client_factory([G_FULL, COMPLETE]))
        r2_dir = os.path.join(session.dir, "arm_r2")
        ev_path = os.path.join(r2_dir, "events.jsonl")
        lines = open(ev_path, encoding="utf-8").read().splitlines()
        tampered = []
        for l in lines:
            e = json.loads(l)
            if e["event_type"] == "bypass_certificate":
                e["model_request_id"] = "req-forged-001"  # 伪造：bypass 声称有模型请求
            tampered.append(json.dumps(e, ensure_ascii=False))
        open(ev_path, "w", encoding="utf-8").write("\n".join(tampered) + "\n")
        code, out = run_audit(r2_dir, "R2")
        assert code != 0
        assert "6_bypass_has_no_model_request" in out and "FAIL" in out

    def test_audit_rejects_synthetic_in_formal_mode(self, repaired_session):
        session = repaired_session
        session.run_graph_arm("arm_r2", "R2", fault_inject=False,
                              client_factory=fake_client_factory([G_FULL, COMPLETE]))
        r2_dir = os.path.join(session.dir, "arm_r2")
        cmd = [VENV_PY, os.path.join(BASE, "tools", "audit.py"),
               "--run-dir", r2_dir, "--expect-mode", "R2"]  # 无 --allow-synthetic
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode != 0
        assert "S_no_synthetic_responses" in p.stdout

    def test_budget_enforced(self, tmp_path, workspace_tmp):
        session = Session(str(tmp_path / "s2"))
        # 无限"继续"的脚本会耗尽预算：脚本只提供 phase_complete 触发 scorer 失败循环？
        # 更直接：脚本回复缺失 action 的坏回复，直到预算耗尽
        script = [json.dumps({"thought": "empty"})] * 40
        with pytest.raises(BudgetExceededError):
            session.run_common(client_factory=fake_client_factory(script))
