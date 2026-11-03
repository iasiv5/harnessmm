#!/usr/bin/env python3
"""实验入口：一条命令完成共同修复 + 三条验证臂。

用法：
  python run_experiment.py --session runs/<session_id> --arms common,r1,r2,r3
  python run_experiment.py --session runs/<session_id> --arms r3   # 补跑单臂
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.runner import Session
from harness.llm import BudgetExceededError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="session dir, e.g. runs/formal-1")
    ap.add_argument("--arms", default="common,r1,r2,r3")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    session = Session(args.session)
    print("session=%s config=%s" % (session.session_id, json.load(
        open(session.config_snapshot_path))["config_hash"]))

    results = {}
    if "common" in arms:
        try:
            session.run_common()
        except BudgetExceededError as exc:
            print("[common] BUDGET EXCEEDED: %s" % exc)
            return 1
        except RuntimeError as exc:
            print("[common] FAILED: %s" % exc)
            return 1

    for arm, runner_fn in [
        ("r1", lambda: session.run_r1()),
        ("r2", lambda: session.run_graph_arm("arm_r2", "R2", fault_inject=False)),
        ("r3", lambda: session.run_graph_arm("arm_r3", "R3", fault_inject=True)),
    ]:
        if arm not in arms:
            continue
        print("=== arm %s ===" % arm.upper())
        try:
            summary = runner_fn()
            results[arm] = summary
            print("[%s] done: %s" % (arm, json.dumps(summary, ensure_ascii=False)))
        except BudgetExceededError as exc:
            print("[%s] BUDGET EXCEEDED: %s" % (arm, exc))
            results[arm] = {"status": "budget_exceeded"}

    stamp = datetime.now(timezone.utc).isoformat()
    with open(os.path.join(session.dir, "session_results.json"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"recorded_at": stamp, "arms_requested": arms,
                             "results": results}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
