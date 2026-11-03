#!/usr/bin/env python3
"""自动结构审计：对一条 arm 运行记录执行 10 项机器可查检查。

用法：python tools/audit.py --run-dir runs/<session>/arm_r2 [--replay replay.html]

审计基于原始证据（events.jsonl + receipts/*.json + graphs/*.json + ledger.jsonl），
不重新调用模型、不重新执行工具。审计通过只证明结构与声明的执行契约一致，
不证明任务语义正确（守卫通过≠任务正确）。
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_events(run_dir):
    events = []
    path = os.path.join(run_dir, "events.jsonl")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def load_ledger(run_dir):
    records = []
    path = os.path.join(run_dir, "ledger.jsonl")
    if not os.path.exists(path):
        return records
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_receipts(run_dir):
    receipts = {}
    rdir = os.path.join(run_dir, "receipts")
    if os.path.isdir(rdir):
        for f in os.listdir(rdir):
            if f.endswith(".json"):
                with open(os.path.join(rdir, f), encoding="utf-8") as fh:
                    r = json.load(fh)
                receipts[r["tool_receipt_id"]] = r
    return receipts


def load_graphs(run_dir):
    graphs = {}
    gdir = os.path.join(run_dir, "graphs")
    if os.path.isdir(gdir):
        for f in sorted(os.listdir(gdir)):
            if f.endswith(".json"):
                with open(os.path.join(gdir, f), encoding="utf-8") as fh:
                    g = json.load(fh)
                graphs[g["graph_id"]] = g
    return graphs


def args_hash(args):
    return "sha256:" + hashlib.sha256(json.dumps(
        args, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()[:16]


def evaluate_guard(guard, receipt):
    for key, want in guard.items():
        if key == "exit_code":
            if receipt.get("exit_code") != want:
                return "failed"
        elif key in ("stdout_contains", "stderr_contains"):
            field = "stdout" if key.startswith("stdout") else "stderr"
            text = receipt.get(field) or ""
            if want not in text:
                return "failed"
    return "pass"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expect-mode", default=None, choices=["R1", "R2", "R3", "common"])
    ap.add_argument("--allow-synthetic", action="store_true",
                    help="仅用于基础设施测试（假模型客户端的夹具运行）；正式审计禁止使用")
    ap.add_argument("--allow-no-graph-reuse", action="store_true",
                    help="仅用于失效路径夹具：该场景图未跨反馈执行属预期；正式 R2/R3 审计禁止使用")
    args = ap.parse_args()
    run_dir = os.path.abspath(args.run_dir)

    events = load_events(run_dir)
    ledger = load_ledger(run_dir)
    receipts = load_receipts(run_dir)
    graphs = load_graphs(run_dir)
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok) if ok is not None else None, "detail": detail})

    arm = events[0]["arm"] if events else "?"
    if args.expect_mode:
        check("arm_matches_expected", arm == args.expect_mode,
              "arm=%s expected=%s" % (arm, args.expect_mode))

    # S. 禁止合成响应冒充真实响应（正式审计强制；基础设施测试用 --allow-synthetic 豁免）
    synthetic = [r.get("request_id") for r in ledger if r.get("synthetic")]
    if args.allow_synthetic:
        check("S_no_synthetic_responses", True,
              "%d synthetic records allowed for infra fixture" % len(synthetic))
    else:
        check("S_no_synthetic_responses", not synthetic,
              "%d synthetic ledger records found: %s" % (len(synthetic), synthetic[:3]))

    # 0. 基本完整性：序号单调、必填字段
    seqs = [e["sequence"] for e in events]
    check("sequences_monotonic", seqs == sorted(seqs) and len(set(seqs)) == len(seqs),
          "%d events, seq range %s..%s" % (len(events), min(seqs or [0]), max(seqs or [0])))
    missing = [e["event_id"] for e in events
               if not e.get("event_type") or not e.get("timestamp") or not e.get("config_hash")]
    check("required_fields_present", not missing, "%d events missing fields" % len(missing))

    ev_by_type = {}
    for e in events:
        ev_by_type.setdefault(e["event_type"], []).append(e)

    graphs_used = {e["graph_id"] for e in ev_by_type.get("bypass_certificate", [])
                   if e.get("graph_id")}
    graphs_authored = {e["graph_id"] for e in ev_by_type.get("graph_authored", [])}
    graph_files = graphs

    # 1. 图在相应反馈前已保存（创作事件序号 < 执行事件序号；图文件存在且内容一致）
    ok, detail = True, []
    for e in ev_by_type.get("graph_authored", []):
        gid = e.get("graph_id")
        if gid not in graph_files:
            ok = False
            detail.append("graph file missing: %s" % gid)
        else:
            g = graph_files[gid]
            if g.get("authored_in_request_id") != e.get("model_request_id"):
                ok = False
                detail.append("%s: authoring request mismatch" % gid)
    for b in ev_by_type.get("bypass_certificate", []):
        ga = [e for e in ev_by_type.get("graph_authored", [])
              if e.get("graph_id") == b.get("graph_id")]
        if not ga or ga[0]["sequence"] >= b["sequence"]:
            ok = False
            detail.append("%s/%s: executed before/without authoring" %
                          (b.get("graph_id"), b.get("node_id")))
    check("1_graph_authored_before_execution", ok, "; ".join(detail) or
          "%d graphs authored before use" % len(graphs_used))

    # 2. 参数一致：执行参数与保存决策逐字节一致
    ok, detail = True, []
    for b in ev_by_type.get("bypass_certificate", []):
        g = graph_files.get(b.get("graph_id"))
        if not g:
            ok = False
            detail.append("%s: no graph file" % b.get("graph_id"))
            continue
        node = next((n for n in g["nodes"] if n["node_id"] == b.get("node_id")), None)
        if node is None:
            ok = False
            detail.append("%s: node missing" % b.get("node_id"))
            continue
        ah = args_hash(node["args"])
        if ah != b.get("authored_args_hash"):
            ok = False
            detail.append("%s: authored hash mismatch" % b.get("node_id"))
        if b.get("authored_args_hash") != b.get("executed_args_hash"):
            ok = False
            detail.append("%s: executed != authored" % b.get("node_id"))
        ev_args = (b.get("data") or {}).get("args")
        if ev_args != node["args"]:
            ok = False
            detail.append("%s: event args != graph args" % b.get("node_id"))
    check("2_executed_args_equal_saved_args", ok,
          "; ".join(detail) or "%d bypasses args-verified" % len(ev_by_type.get("bypass_certificate", [])))

    # 3. 前置回执存在且满足守卫
    ok, detail = True, []
    for b in ev_by_type.get("bypass_certificate", []):
        pid = b.get("prior_receipt_id")
        g = graph_files.get(b.get("graph_id"))
        node = next((n for n in (g or {}).get("nodes", [])
                     if n["node_id"] == b.get("node_id")), None)
        if pid is None or pid not in receipts:
            ok = False
            detail.append("%s: prior receipt %s not found" % (b.get("node_id"), pid))
            continue
        if node:
            guard = node.get("guard", {})
            result = evaluate_guard(guard, receipts[pid])
            if result != "pass":
                ok = False
                detail.append("%s: guard %s re-eval=%s" % (b.get("node_id"), guard, result))
        if b.get("guard_result") != "pass":
            ok = False
            detail.append("%s: guard_result not pass" % b.get("node_id"))
    check("3_prior_receipt_satisfies_guard", ok,
          "; ".join(detail) or "%d bypasses guard-verified" % len(ev_by_type.get("bypass_certificate", [])))

    # 4. 节点不重复执行
    seen, dupes = set(), []
    for b in ev_by_type.get("bypass_certificate", []):
        key = (b.get("graph_id"), b.get("node_id"))
        if key in seen:
            dupes.append(key)
        seen.add(key)
    check("4_node_executed_at_most_once", not dupes,
          "%d duplicates %s" % (len(dupes), dupes))

    # 5. 同一图跨反馈执行多个节点（R2/R3 核心机制）
    per_graph = {}
    for b in ev_by_type.get("bypass_certificate", []):
        per_graph.setdefault(b.get("graph_id"), set()).add(b.get("node_id"))
    multi = {g: sorted(ns) for g, ns in per_graph.items() if len(ns) >= 2}
    if arm in ("R2", "R3"):
        if multi:
            check("5_graph_spans_multiple_feedbacks", True,
                  "graphs with >=2 nodes: %s" % multi)
        elif args.allow_no_graph_reuse:
            check("5_graph_spans_multiple_feedbacks", True,
                  "no graph reuse occurred (expected in invalidation-path fixture)")
        else:
            check("5_graph_spans_multiple_feedbacks", False,
                  "no graph executed >=2 nodes across feedbacks")
    else:
        check("5_no_bypass_in_step_mode", not ev_by_type.get("bypass_certificate", []),
              "R1/common must have zero bypasses, found %d" % len(ev_by_type.get("bypass_certificate", [])))

    # 6. bypass 边界没有模型请求；账本与模型事件一致
    ok, detail = True, []
    for b in ev_by_type.get("bypass_certificate", []):
        if b.get("model_request_id") is not None:
            ok = False
            detail.append("%s has model_request_id!" % b.get("node_id"))
    model_events = [e for e in ev_by_type.get("model_request", [])]
    ledger_ok = [r for r in ledger if r.get("status") == "ok"]
    ev_ids = [e.get("model_request_id") for e in model_events]
    led_ids = [r.get("request_id") for r in ledger_ok]
    if sorted(ev_ids) != sorted(led_ids):
        ok = False
        detail.append("model events %d vs ledger ok records %d" % (len(ev_ids), len(led_ids)))
    ok_ids = {r["request_id"] for r in ledger if r.get("status") != "ok"}
    if set(ev_ids) & ok_ids:
        ok = False
        detail.append("model_request event for failed ledger record")
    check("6_bypass_has_no_model_request", ok,
          "; ".join(detail) or "%d bypasses w/o request; %d model events match ledger"
          % (len(ev_by_type.get("bypass_certificate", [])), len(ev_ids)))

    # 7. 故障后没有继续自动执行下游节点（R3）
    faults = ev_by_type.get("fault_injected", [])
    if arm == "R3":
        if not faults:
            check("7_fault_blocked_downstream", False, "R3 declares fault injection but no fault_injected event")
        else:
            fault_seq = faults[0]["sequence"]
            fr = (faults[0].get("data") or {})
            detail = []
            ok = True
            # 注入回执真实非零
            inj_receipts = [r for r in receipts.values() if r.get("injected_fault")]
            if not any((r.get("exit_code") not in (0, None)) for r in inj_receipts):
                ok = False
                detail.append("injected receipt exit_code not non-zero")
            # 故障前的活跃图
            active_before = None
            for e in ev_by_type.get("graph_authored", []):
                if e["sequence"] < fault_seq:
                    active_before = e.get("graph_id")
            # 故障后属于旧图的 bypass 一律违规（故障受害者节点自身除外——
            # 它的证书记录的正是注入失败的真实回执）
            for b in ev_by_type.get("bypass_certificate", []):
                if b["sequence"] > fault_seq and b.get("graph_id") == active_before:
                    if (b.get("data") or {}).get("injected_fault"):
                        continue
                    ok = False
                    detail.append("old graph %s auto-continued after fault (%s)"
                                  % (active_before, b.get("node_id")))
            # 故障后必须先有 graph_invalidated 或回退模型请求
            post_invalid = any(e["event_type"] == "graph_invalidated"
                               and e["sequence"] > fault_seq for e in events)
            if not post_invalid:
                ok = False
                detail.append("no graph_invalidated after fault")
            check("7_fault_blocked_downstream", ok, "; ".join(detail) or
                  "fault at seq %d blocked; old graph %s invalidated" % (fault_seq, active_before))
    else:
        check("7_fault_blocked_downstream", True, "not applicable (arm=%s)" % arm)

    # 8. 回退产生真实模型请求
    ok, detail = True, []
    fallbacks = [e for e in events if e["event_type"] in ("graph_invalidated", "fallback")]
    for f in fallbacks:
        later_request = any(e["event_type"] == "model_request"
                            and e["sequence"] > f["sequence"]
                            for e in events)
        if not later_request:
            ok = False
            detail.append("%s@%d(%s) never followed by a model request"
                          % (f["event_type"], f["sequence"], f.get("fallback_reason", "")))
    check("8_fallback_produces_real_model_request", ok,
          "; ".join(detail) or "%d fallbacks all recovered to model" % len(fallbacks))

    # 9. 工作区变更导致旧图失效（执行参数时工作区哈希必须与创作时一致）
    ok, detail = True, []
    authored_ws = {}
    for e in ev_by_type.get("graph_authored", []):
        g = graph_files.get(e.get("graph_id")) or {}
        authored_ws[e["graph_id"]] = (g.get("workspace_hash_at_authoring")
                                      or (e.get("data") or {}).get("workspace_hash_at_authoring"))
    for b in ev_by_type.get("bypass_certificate", []):
        ws_at_exec = b.get("workspace_hash")
        ws_at_auth = authored_ws.get(b.get("graph_id"))
        if ws_at_auth and ws_at_exec and ws_at_exec != ws_at_auth:
            ok = False
            detail.append("%s executed under changed workspace" % b.get("node_id"))
    changed = [e for e in events if e["event_type"] == "graph_invalidated"
               and "workspace_changed" in (e.get("fallback_reason") or "")]
    check("9_workspace_change_invalidates_graph", ok,
          "; ".join(detail) or "%d workspace-change invalidations" % len(changed))

    # 10. 回放与原始事件一致（若回放文件存在）
    replay_path = os.path.join(run_dir, "replay.html")
    if os.path.exists(replay_path):
        ev_path = os.path.join(run_dir, "events.jsonl")
        ev_sha = "sha256:" + hashlib.sha256(open(ev_path, "rb").read()).hexdigest()[:24]
        html = open(replay_path, encoding="utf-8").read()
        marker = '"events_sha256": "%s"' % ev_sha
        check("10_replay_matches_events", marker in html,
              "replay %s embedded events hash" % ("matches" if marker in html else "MISMATCH for " + ev_sha))
    else:
        check("10_replay_matches_events", None, "replay.html not generated yet")

    all_pass = all(c["ok"] for c in checks if c["ok"] is not None)
    report = {
        "run_dir": run_dir,
        "arm": arm,
        "verdict": "PASS" if all_pass else "FAIL",
        "checks": checks,
        "totals": {
            "events": len(events),
            "model_requests": len(model_events),
            "bypasses": len(ev_by_type.get("bypass_certificate", [])),
            "graphs_authored": len(ev_by_type.get("graph_authored", [])),
            "graphs_invalidated": len(ev_by_type.get("graph_invalidated", [])),
            "faults_injected": len(ev_by_type.get("fault_injected", [])),
            "fallbacks": len(fallbacks),
            "ledger_tokens": {
                "in": sum((r.get("usage") or {}).get("prompt_tokens") or 0 for r in ledger_ok),
                "out": sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in ledger_ok),
            },
        },
    }
    out_path = os.path.join(run_dir, "audit_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    for c in checks:
        print("%-42s %s  %s" % (c["check"],
                                "PASS" if c["ok"] else ("n/a" if c["ok"] is None else "FAIL"),
                                str(c["detail"])[:160]))
    print("AUDIT %s -> %s" % (report["verdict"], out_path))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
