#!/usr/bin/env python3
"""只读回放生成器：从保存的事件重新生成静态 HTML 回放页。

- 不重新调用模型、不重新执行工具、不修改工作区；
- 生成物顶部明确标注"真实运行记录回放，非现场执行"；
- 所有模型/工具输出经 JSON 序列化嵌入，并用 textContent 渲染（无 innerHTML 注入面）；
- 嵌入 events.jsonl 的 sha256，供 tools/audit.py 第 10 项核验"回放与原始事件一致"。

用法：python tools/replay.py --run-dir runs/<session>/arm_r2
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def esc(s):
    """把任意字符串安全嵌入 <script type=application/json>。"""
    return json.dumps(s, ensure_ascii=False).replace("</", "<\\/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    run_dir = os.path.abspath(args.run_dir)

    ev_path = os.path.join(run_dir, "events.jsonl")
    events = []
    with open(ev_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    ev_sha = "sha256:" + hashlib.sha256(open(ev_path, "rb").read()).hexdigest()[:24]

    ledger = []
    led_path = os.path.join(run_dir, "ledger.jsonl")
    if os.path.exists(led_path):
        with open(led_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ledger.append(json.loads(line))

    receipts = {}
    rdir = os.path.join(run_dir, "receipts")
    if os.path.isdir(rdir):
        for f in os.listdir(rdir):
            if f.endswith(".json"):
                with open(os.path.join(rdir, f), encoding="utf-8") as fh:
                    r = json.load(fh)
                receipts[r["tool_receipt_id"]] = r

    graphs = {}
    gdir = os.path.join(run_dir, "graphs")
    if os.path.isdir(gdir):
        for f in sorted(os.listdir(gdir)):
            if f.endswith(".json"):
                with open(os.path.join(gdir, f), encoding="utf-8") as fh:
                    g = json.load(fh)
                graphs[g["graph_id"]] = g

    run_meta = {}
    meta_path = os.path.join(run_dir, "run.json")
    if os.path.exists(meta_path):
        run_meta = json.load(open(meta_path, encoding="utf-8"))

    arm = events[0].get("arm", "?") if events else "?"
    model_events = [e for e in events if e["event_type"] == "model_request"]
    bypasses = [e for e in events if e["event_type"] == "bypass_certificate"]
    invalidations = [e for e in events if e["event_type"] == "graph_invalidated"]
    faults = [e for e in events if e["event_type"] == "fault_injected"]
    run_end = [e for e in events if e["event_type"] == "run_end"]
    scorer = [e for e in events if e["event_type"] == "scorer_run"]

    tok_in = sum(e.get("tokens_in") or 0 for e in model_events)
    tok_out = sum(e.get("tokens_out") or 0 for e in model_events)
    tok_cached = sum(e.get("tokens_cached") or 0 for e in model_events)
    tok_reason = sum(e.get("tokens_reasoning") or 0 for e in model_events)

    payload = {
        "label": "真实运行记录回放，非现场执行",
        "run_meta": run_meta,
        "arm": arm,
        "events_sha256": ev_sha,
        "events": events,
        "ledger": [{"request_id": r.get("request_id"),
                    "provider_request_id": r.get("provider_request_id"),
                    "purpose": r.get("purpose"),
                    "route": r.get("route_hint"),
                    "status": r.get("status"),
                    "model": r.get("model"),
                    "usage": r.get("usage"),
                    "latency_ms": r.get("latency_ms"),
                    "retries": r.get("retries"),
                    "content_full": r.get("content_full")} for r in ledger],
        "receipts": receipts,
        "graphs": graphs,
        "summary": {
            "model_calls": len(model_events),
            "bypasses": len(bypasses),
            "graphs_authored": len([e for e in events if e["event_type"] == "graph_authored"]),
            "graphs_invalidated": len(invalidations),
            "faults_injected": len(faults),
            "tokens_in": tok_in, "tokens_out": tok_out,
            "tokens_cached": tok_cached, "tokens_reasoning": tok_reason,
            "provider_reported_usage": True,
            "final_status": (run_end[-1].get("data") or {}).get("status") if run_end else "unknown",
            "scorer_results": [(e.get("data") or {}).get("exit_code") for e in scorer],
        },
    }

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>HarnessMM 回放 — RUN __ARM__</title>
<style>
  :root { --bg:#0f1117; --panel:#161a23; --line:#232936; --fg:#d7dce5; --dim:#8a93a5;
          --green:#4ade80; --red:#f87171; --amber:#fbbf24; --blue:#60a5fa; --purple:#c084fc; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.55 "SF Mono", ui-monospace, "PingFang SC", "Microsoft YaHei", monospace; }
  header { padding:14px 18px; border-bottom:1px solid var(--line); display:flex; gap:16px; align-items:baseline; flex-wrap:wrap;}
  header h1 { font-size:15px; margin:0; font-weight:600; }
  .badge { color:#0f1117; background:var(--amber); padding:2px 8px; border-radius:4px; font-weight:700; font-size:11px; }
  .meta { color:var(--dim); font-size:11px; }
  .cols { display:grid; grid-template-columns: 5fr 7fr 4fr; gap:1px; background:var(--line); height: calc(100vh - 96px);}
  .col { background:var(--panel); overflow:auto; padding:12px 14px; min-width:0;}
  .col h2 { font-size:12px; color:var(--dim); margin:0 0 10px; text-transform:uppercase; letter-spacing:.08em;}
  .card { border:1px solid var(--line); border-radius:6px; padding:8px 10px; margin-bottom:8px; }
  .card.decision { border-left:3px solid var(--purple); }
  .card.tool { border-left:3px solid var(--blue); }
  .card.bypass { border-left:3px solid var(--green); }
  .card.fault { border-left:3px solid var(--red); }
  .card.fallback { border-left:3px solid var(--amber); }
  .card.model { border-left:3px solid var(--dim); }
  .tag { font-size:10px; padding:1px 6px; border-radius:3px; margin-right:6px; color:#0f1117; font-weight:700;}
  .tag.slow{background:var(--dim);} .tag.edg{background:var(--green);} .tag.fallback{background:var(--amber);}
  .tag.fault{background:var(--red); color:#fff;} .tag.model{background:var(--blue);}
  pre { margin:4px 0 0; white-space:pre-wrap; word-break:break-all; font-size:11.5px; color:var(--fg); }
  .dim { color:var(--dim); }
  .kv { margin-top:4px; }
  .kv b { color:var(--dim); font-weight:400; margin-right:4px;}
  .num { font-size:20px; font-weight:700; }
  .step-q { color:var(--dim); font-size:11px; margin-top:6px; border-top:1px dashed var(--line); padding-top:4px;}
</style>
</head>
<body>
<header>
  <h1>HarnessMM 运行回放 — <span id="runid"></span></h1>
  <span class="badge">真实运行记录回放，非现场执行</span>
  <span class="meta" id="hdrmeta"></span>
</header>
<div class="cols">
  <div class="col">
    <h2>左侧 · 模型已提交的决策（创作时点）</h2>
    <div id="decisions"></div>
    <div class="step-q">本栏只显示模型在真实请求中提交过的动作与图（含创作时的事件序号），运行时未曾提交的内容不会出现。</div>
  </div>
  <div class="col">
    <h2>中间 · 执行时间线（工具返回 → 条件检查 → 实际路径）</h2>
    <div id="timeline"></div>
  </div>
  <div class="col">
    <h2>右侧 · 真实请求账本</h2>
    <div class="card"><div class="num" id="n-calls">–</div><div class="dim">模型调用次数（全部经统一入口）</div></div>
    <div class="card"><div class="num" id="n-bypass">–</div><div class="dim">CCS 回调替代（bypass，无模型请求）</div></div>
    <div class="card"><div class="num" id="n-tokens">–</div><div class="dim">供应商实测 token（输入/输出）</div></div>
    <div class="card"><div id="n-extra" class="kv"></div></div>
    <div class="card"><b>最终状态</b><pre id="n-status"></pre></div>
    <div class="card"><b>请求明细</b><div id="ledger-list"></div></div>
    <div class="step-q">“模型未调用”指该边界没有任何请求记录；本页不把它折算成任何假设成本。</div>
  </div>
</div>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
const P = JSON.parse(document.getElementById('payload').textContent);
document.getElementById('runid').textContent = P.run_meta.run_id || P.arm;
document.getElementById('hdrmeta').textContent =
  'arm=' + P.arm + ' · events=' + P.events.length + ' · events_sha256=' + P.events_sha256 + ' · ' + (P.run_meta.fault_inject ? '故障注入已启用' : '无故障注入');

function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function card(cls, tagText, tagCls, title) {
  const c = el('div', 'card ' + cls);
  const t = el('span', 'tag ' + tagCls, tagText);
  c.appendChild(t); c.appendChild(el('b', '', title));
  return c;
}

// ---------- 左栏：模型已提交的决策 ----------
const dec = document.getElementById('decisions');
const seenGraphs = {};
for (const g of Object.values(P.graphs)) {
  const c = card('decision', 'EDG', 'model', g.graph_id);
  c.appendChild(el('div', 'kv', '')).innerHTML = '';
  const meta = el('div', 'dim', '创作于请求 ' + (g.authored_in_request_id||'?') + ' · 工作区 ' + (g.workspace_hash_at_authoring||'').slice(0,16));
  c.appendChild(meta);
  if (g.rationale) c.appendChild(el('pre', '', 'rationale: ' + g.rationale));
  for (const n of g.nodes) {
    const d = el('div', 'kv');
    d.appendChild(el('pre', '', '· ' + n.node_id + '  ' + n.tool_name + '\\n  args: ' + JSON.stringify(n.args) + '\\n  depends_on: ' + n.depends_on + '\\n  guard: ' + JSON.stringify(n.guard)));
    c.appendChild(d);
  }
  dec.appendChild(c);
}
// 决策型回复（R1 / 回退）：从 ledger 里 purpose=arm_decision 且内容含 action 的请求
for (const r of P.ledger) {
  if (r.purpose === 'arm_decision' && r.content_full) {
    try {
      const j = JSON.parse(r.content_full.trim().split('```').join('').replace(/^[^{]*/, '').replace(/[^}]*$/, ''));
      if (j.action && !j.graph) {
        const c = card('decision', 'ACTION', 'model', '单步决策 @' + r.request_id);
        c.appendChild(el('pre', '', JSON.stringify(j.action)));
        if (j.thought) c.appendChild(el('pre', 'dim', 'thought: ' + j.thought.slice(0,300)));
        dec.appendChild(c);
      }
    } catch(e) { /* 非决策回复，忽略 */ }
  }
}

// ---------- 中栏：执行时间线 ----------
const tl = document.getElementById('timeline');
for (const e of P.events) {
  if (e.event_type === 'model_request') {
    const c = card('model', e.route ? e.route.toUpperCase() : 'SLOW', 'model', e.model_request_id + ' · ' + (e.purpose||''));
    c.appendChild(el('pre', 'dim', 'tokens in/out: ' + (e.tokens_in??'?') + '/' + (e.tokens_out??'?') +
      (e.tokens_cached ? ' · cached ' + e.tokens_cached : '') +
      (e.tokens_reasoning ? ' · reasoning ' + e.tokens_reasoning : '') + ' · ' + (e.latency_ms??'?') + 'ms'));
    c.appendChild(el('div', 'step-q', '刚发生了什么：一次真实模型调用（route=' + (e.route||'slow') + '）。'));
    tl.appendChild(c);
  } else if (e.event_type === 'tool_execute') {
    const d = e.data || {};
    const c = card('tool', 'TOOL', 'slow', (d.tool_name||'') + ' exit=' + (d.exit_code??'?'));
    if (d.argv) c.appendChild(el('pre', '', d.argv.join(' ')));
    if (d.path) c.appendChild(el('pre', '', 'path: ' + d.path));
    if (d.stdout_tail) c.appendChild(el('pre', 'dim', 'stdout: ' + d.stdout_tail.slice(-260)));
    if (d.stderr_tail) c.appendChild(el('pre', 'dim', 'stderr: ' + d.stderr_tail.slice(-200)));
    c.appendChild(el('div', 'step-q', '回执 ' + e.tool_receipt_id + '（哈希 ' + (e.tool_receipt_hash||'').slice(0,16) + '）'));
    tl.appendChild(c);
  } else if (e.event_type === 'bypass_certificate') {
    const d = e.data || {};
    const c = card('bypass', 'EDG', 'edg', '节点 ' + e.node_id + ' 自动执行（无模型调用）');
    c.appendChild(el('pre', '', JSON.stringify(d.args)));
    c.appendChild(el('pre', 'dim', 'guard: ' + (e.guard_reason||'') + ' · 前置回执 ' + (e.prior_receipt_id||'') +
      ' · 参数哈希 ' + (e.authored_args_hash||'').slice(0,10) + '==' + (e.executed_args_hash||'').slice(0,10)));
    if (d.exit_code !== undefined) c.appendChild(el('pre', 'dim', 'exit=' + d.exit_code + ' stdout: ' + ((d.stdout_tail||'').slice(-180))));
    if (d.injected_fault) c.appendChild(el('pre', 'fault-tag dim', '⚠ 注入故障命中：' + JSON.stringify(d.injected_fault)));
    c.appendChild(el('div', 'step-q', '已有决策：该动作在图创作时已保存；运行时只检查了前置完成与守卫条件；实际未调用模型。'));
    tl.appendChild(c);
  } else if (e.event_type === 'fault_injected') {
    const c = card('fault', 'FAULT', 'fault', '注入故障（教学装置）');
    c.appendChild(el('pre', '', JSON.stringify(e.data)));
    c.appendChild(el('div', 'step-q', '运行时注入了一次真实失败：子进程真实退出非零；一次性触发并全程标注。'));
    tl.appendChild(c);
  } else if (e.event_type === 'graph_authored') {
    const c = card('decision', 'AUTHOR', 'model', '图 ' + e.graph_id + ' 已保存（' + (e.data?e.data.node_count:'?') + ' 节点）');
    c.appendChild(el('div', 'step-q', '该图来自真实请求 ' + e.model_request_id + '，先于其后续任何执行（seq ' + e.sequence + '）。'));
    tl.appendChild(c);
  } else if (e.event_type === 'graph_invalidated') {
    const c = card('fallback', 'INVALID', 'fallback', '图 ' + (e.graph_id||'') + ' 失效');
    c.appendChild(el('pre', 'dim', (e.fallback_reason||'') + ' guard=' + (e.guard_result||'-')));
    c.appendChild(el('div', 'step-q', '条件不满足或工作区变化：剩余图作废，控制权交回模型。'));
    tl.appendChild(c);
  } else if (e.event_type === 'fallback') {
    const c = card('fallback', 'FALLBACK', 'fallback', '回退到模型');
    c.appendChild(el('pre', 'dim', e.fallback_reason || ''));
    tl.appendChild(c);
  } else if (e.event_type === 'scorer_run') {
    const c = card('tool', 'SCORER', 'slow', '独立评分器 exit=' + (e.data?e.data.exit_code:'?'));
    if (e.data && e.data.output_tail) c.appendChild(el('pre', 'dim', e.data.output_tail.slice(-300)));
    tl.appendChild(c);
  } else if (e.event_type === 'run_start' || e.event_type === 'run_end') {
    const c = card('model', e.event_type === 'run_start' ? 'START' : 'END', 'slow', e.event_type === 'run_start' ? (e.data&&e.data.mode||'') : String((e.data&&e.data.status)||''));
    if (e.data && e.data.model_calls) c.appendChild(el('pre', 'dim', JSON.stringify(e.data.model_calls)));
    tl.appendChild(c);
  }
}

// ---------- 右栏：账本 ----------
document.getElementById('n-calls').textContent = P.summary.model_calls;
document.getElementById('n-bypass').textContent = P.summary.bypasses;
document.getElementById('n-tokens').textContent = P.summary.tokens_in + ' / ' + P.summary.tokens_out;
const extra = document.getElementById('n-extra');
extra.innerHTML = '';
[['图创作', P.summary.graphs_authored], ['图失效', P.summary.graphs_invalidated],
 ['注入故障', P.summary.faults_injected],
 ['缓存命中 tokens', P.summary.tokens_cached], ['reasoning tokens', P.summary.tokens_reasoning],
 ['评分器结果', JSON.stringify(P.summary.scorer_results)],
 ['provider 实测 usage', '是']].forEach(([k, v]) => {
  const d = el('div', 'kv');
  const b = document.createElement('b'); b.textContent = k + ':';
  d.appendChild(b); d.appendChild(el('span', '', String(v)));
  extra.appendChild(d);
});
document.getElementById('n-status').textContent = P.summary.final_status + ' · arm=' + P.arm;
const ll = document.getElementById('ledger-list');
for (const r of P.ledger) {
  const d = el('div', 'kv');
  d.appendChild(el('pre', 'dim', (r.request_id||'') + ' · ' + (r.purpose||'') + ' · route=' + (r.route||'') +
    ' · ' + (r.usage ? ('in ' + r.usage.prompt_tokens + ' out ' + r.usage.completion_tokens) : 'no usage') +
    (r.provider_request_id ? (' · prov ' + r.provider_request_id) : '') +
    (r.retries && r.retries.length ? (' · retries ' + r.retries.length) : '')));
  ll.appendChild(d);
}
</script>
</body>
</html>"""

    html = html.replace("__PAYLOAD__", esc(payload))
    # 头部 arm 占位
    html = html.replace("__ARM__", str(arm))
    out_path = os.path.join(run_dir, "replay.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("replay written: %s (%d events, arm=%s)" % (out_path, len(events), arm))
    return 0


if __name__ == "__main__":
    sys.exit(main())
