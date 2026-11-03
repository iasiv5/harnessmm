"""基础设施测试夹具：假模型客户端与可变工作区工具层。

⚠ 这些夹具只能用于 tests_infra/（基础设施验证），绝不用于正式运行。
所有合成记录带 "synthetic": true 标记，正式审计（无 --allow-synthetic）会拒绝。
"""

import json
import os

from harness.tools import ToolLayer
from harness.llm import BudgetExceededError
from harness.config import FROZEN


class FakeModelClient:
    """按脚本回放预设回复的假客户端；只用于基础设施测试。

    与真实客户端一致：超过预算抛 BudgetExceededError（默认用公共阶段预算）。"""

    def __init__(self, run_id, arm, event_log, run_dir, script,
                 budget_calls=None):
        self.run_id = run_id
        self.arm = arm
        self.event_log = event_log
        self.ledger_path = os.path.join(run_dir, "ledger.jsonl")
        self.script = list(script)
        self.budget_calls = budget_calls or FROZEN["budget_common_calls"]
        self.calls = 0
        self.total_tokens = 0
        self.records = []

    def call(self, purpose, messages, max_tokens=None, route_hint=None):
        if self.calls >= self.budget_calls:
            raise BudgetExceededError(
                "fake client budget exhausted: %d" % self.budget_calls)
        self.calls += 1
        request_id = "req-%s-%03d" % (self.run_id, self.calls)
        if self.script:
            content = self.script.pop(0)
        else:
            content = json.dumps({"task_complete": True, "summary": "fake script exhausted"})
        record = {
            "request_id": request_id,
            "run_id": self.run_id,
            "arm": self.arm,
            "purpose": purpose,
            "route_hint": route_hint or "slow",
            "status": "ok",
            "synthetic": True,
            "provider_request_id": "fake-provider-%s" % request_id,
            "usage": None,
            "retries": [],
            "content_full": content,
            "latency_ms": 0,
        }
        self.records.append(record)
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.event_log.emit("model_request", model_request_id=request_id,
                            provider_request_id=record["provider_request_id"],
                            purpose=purpose, route=record["route_hint"],
                            latency_ms=0, data={"synthetic": True})
        return content, record

    def summary(self):
        return {"model_calls": self.calls, "synthetic": True}


def fake_client_factory(script):
    def factory(run_id, arm, event_log, run_dir):
        return FakeModelClient(run_id, arm, event_log, run_dir, script)
    return factory


class ShiftingToolLayer(ToolLayer):
    """workspace_hash() 在第 N 次调用后返回不同值，用于模拟工作区变更。"""

    def __init__(self, *args, shift_after=1, **kwargs):
        super().__init__(*args, **kwargs)
        self._hash_calls = 0
        self._shift_after = shift_after

    def workspace_hash(self):
        self._hash_calls += 1
        base = super().workspace_hash()
        if self._hash_calls > self._shift_after:
            return base + "-SHIFTED"
        return base


# 供测试复用的合法图脚本（守卫只引用前置回执中真实存在的标记）
G_FULL = json.dumps({
    "thought": "unit passes; authoring graph for integration/build/verify",
    "action": {"tool_name": "run_command",
               "args": {"argv": ["python", "-m", "pytest", "tests/unit", "-q"]}},
    "graph": {"graph_id": "fake-g1", "rationale": "all args fixed by task spec",
              "nodes": [
                  {"node_id": "n_int", "tool_name": "run_command",
                   "args": {"argv": ["python", "-m", "pytest", "tests/integration", "-q"]},
                   "depends_on": "root", "guard": {"exit_code": 0}},
                  {"node_id": "n_build", "tool_name": "run_command",
                   "args": {"argv": ["python", "tools/build_artifact.py", "--out", "dist/package.zip"]},
                   "depends_on": "n_int", "guard": {"exit_code": 0}},
                  {"node_id": "n_verify", "tool_name": "run_command",
                   "args": {"argv": ["python", "tools/verify_artifact.py", "--file", "dist/package.zip"]},
                   "depends_on": "n_build",
                   "guard": {"exit_code": 0, "stdout_contains": "BUILD OK"}},
              ]}}, ensure_ascii=False)

COMPLETE = json.dumps({"task_complete": True, "summary": "all verified"}, ensure_ascii=False)
