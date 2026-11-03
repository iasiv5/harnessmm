#!/usr/bin/env python3
"""会话结果汇总：从账本与事件重算调用数、token、bypass 数等，不做任何估算。

用法：python tools/summarize.py --session runs/formal-1 [--out runs/formal-1/results.md]
"""

import argparse
import json
import os
import sys


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def arm_stats(run_dir):
    ledger = [r for r in load_jsonl(os.path.join(run_dir, "ledger.jsonl"))
              if r.get("status") == "ok"]
    events = load_jsonl(os.path.join(run_dir, "events.jsonl"))
    ev_types = {}
    for e in events:
        ev_types.setdefault(e["event_type"], []).append(e)
    run_end = ev_types.get("run_end", [{}])[-1]
    status = (run_end.get("data") or {}).get("status", "unknown")
    audit_path = os.path.join(run_dir, "audit_report.json")
    audit = json.load(open(audit_path)) if os.path.exists(audit_path) else None
    return {
        "model_calls": len(ledger),
        "tokens_in": sum((r.get("usage") or {}).get("prompt_tokens") or 0 for r in ledger),
        "tokens_out": sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in ledger),
        "tokens_cached": sum((r.get("usage") or {}).get("prompt_tokens_details", {}).get("cached_tokens") or 0 for r in ledger),
        "bypasses": len(ev_types.get("bypass_certificate", [])),
        "graphs_authored": len(ev_types.get("graph_authored", [])),
        "graphs_invalidated": len(ev_types.get("graph_invalidated", [])),
        "faults": len(ev_types.get("fault_injected", [])),
        "scorer_runs": [((e.get("data") or {}).get("exit_code"))
                        for e in ev_types.get("scorer_run", [])],
        "final_status": status,
        "audit_verdict": (audit or {}).get("verdict"),
        "provider_reported": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    session = os.path.abspath(args.session)

    common = arm_stats(os.path.join(session, "common"))
    rows = []
    for arm, d in [("R1", "arm_r1"), ("R2", "arm_r2"), ("R3", "arm_r3")]:
        rd = os.path.join(session, d)
        if os.path.isdir(rd):
            st = arm_stats(rd)
            st["arm_only_calls"] = st["model_calls"]
            st["arm_only_tokens"] = st["tokens_in"] + st["tokens_out"]
            st["calls_with_shared_prefix"] = st["model_calls"] + common["model_calls"]
            st["tokens_with_shared_prefix"] = st["arm_only_tokens"] + common["tokens_in"] + common["tokens_out"]
            rows.append((arm, st))

    lines = []
    lines.append("# 会话结果：%s\n" % os.path.basename(session))
    lines.append("数字全部来自供应商实测 usage 与事件账本；无估算值，无假设单价。\n")
    lines.append("## 共同修复阶段（shared prefix）\n")
    lines.append("| 指标 | 值 |\n|---|---|")
    lines.append("| 模型调用 | %d |" % common["model_calls"])
    lines.append("| tokens in/out | %d / %d |" % (common["tokens_in"], common["tokens_out"]))
    lines.append("| 最终状态 | %s |" % common["final_status"])
    lines.append("")
    lines.append("## 验证臂\n")
    lines.append("| 指标 | " + " | ".join(r[0] for r in rows) + " |")
    lines.append("|---|" + "---|" * len(rows))
    def row(label, key, fmt=str):
        lines.append("| %s | %s |" % (label, " | ".join(fmt(r[1].get(key)) for r in rows)))
    row("本臂模型调用", "arm_only_calls")
    lines.append("| 本臂 tokens (in/out) | " + " | ".join(
        "%d/%d" % (r[1]["tokens_in"], r[1]["tokens_out"]) for r in rows) + " |")
    lines.append("| 本臂 tokens 合计 | " + " | ".join(str(r[1]["arm_only_tokens"]) for r in rows) + " |")
    lines.append("| 含共同前缀调用 | " + " | ".join(str(r[1]["calls_with_shared_prefix"]) for r in rows) + " |")
    lines.append("| 含共同前缀 tokens | " + " | ".join(str(r[1]["tokens_with_shared_prefix"]) for r in rows) + " |")
    row("CCS bypass 数", "bypasses")
    lines.append("| 图创作/失效 | " + " | ".join("%d/%d" % (r[1]["graphs_authored"], r[1]["graphs_invalidated"]) for r in rows) + " |")
    row("注入故障", "faults")
    lines.append("| 缓存命中 tokens | " + " | ".join(str(r[1]["tokens_cached"]) for r in rows) + " |")
    lines.append("| 评分器运行(退出码) | " + " | ".join(str(r[1]["scorer_runs"]) for r in rows) + " |")
    row("最终状态", "final_status")
    row("审计结论", "audit_verdict")
    lines.append("")
    lines.append("注：共享前缀 = 共同修复阶段的一次性成本。论文语境下的完整任务记账包含它；"
                 "各臂自身的验证阶段成本单独列出。共享前缀在每个臂的\"含共同前缀\"行只重复列出，"
                 "不做多次分摊计算（每次列出的都是同一份真实成本，非重复计入实验结论）。\n")

    out = args.out or os.path.join(session, "results.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))
    print("\nwritten:", out)


if __name__ == "__main__":
    sys.exit(main())
