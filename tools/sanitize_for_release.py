#!/usr/bin/env python3
"""开源发布前的机械脱敏：替换本机绝对路径，保留实验语义内容不变。

替换规则（纯机械、可审计）：
  /home/ubuntu/workspace/context-infrastructure  ->  <workspace>
  /home/ubuntu  ->  ~

不改动任何数字、哈希、事件语义；路径替换后重新生成回放与审计报告，
使回放内嵌哈希与脱敏后的事件文件保持一致。

用法：python tools/sanitize_for_release.py --runs-dir runs [--dry-run]
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLACEMENTS = [
    ("/home/ubuntu/workspace/context-infrastructure", "<workspace>"),
    ("/home/ubuntu", "~"),
]
TEXT_SUFFIXES = (".json", ".jsonl", ".html", ".md", ".txt")


def sanitize_file(path, dry_run=False):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    out = text
    for old, new in REPLACEMENTS:
        out = out.replace(old, new)
    if out != text:
        if not dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(out)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default=os.path.join(HERE, "runs"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    runs_dir = os.path.abspath(args.runs_dir)

    changed = []
    for root, dirs, files in os.walk(runs_dir):
        for f in files:
            if not f.endswith(TEXT_SUFFIXES):
                continue
            p = os.path.join(root, f)
            if sanitize_file(p, dry_run=args.dry_run):
                changed.append(os.path.relpath(p, runs_dir))
    print("sanitized %d files%s" % (len(changed), " (dry-run)" if args.dry_run else ""))

    if args.dry_run:
        for c in changed[:20]:
            print("  would change:", c)
        return 0

    # 路径脱敏改变了 events.jsonl 的字节内容，回放内嵌哈希需重新生成
    venv_py = os.path.join(HERE, ".venv", "bin", "python")
    arm_dirs = []
    for session in sorted(os.listdir(runs_dir)):
        sp = os.path.join(runs_dir, session)
        if not os.path.isdir(sp):
            continue
        for d in sorted(os.listdir(sp)):
            dp = os.path.join(sp, d)
            if os.path.exists(os.path.join(dp, "events.jsonl")):
                arm_dirs.append(dp)

    for dp in arm_dirs:
        subprocess.run([venv_py, os.path.join(HERE, "tools", "replay.py"),
                        "--run-dir", dp], capture_output=True)
    # 重新生成审计报告（覆盖脱敏前的旧报告）
    for dp in arm_dirs:
        name = os.path.basename(dp)
        mode = "common" if name == "common" else name.replace("arm_", "").upper()
        subprocess.run([venv_py, os.path.join(HERE, "tools", "audit.py"),
                        "--run-dir", dp, "--expect-mode", mode], capture_output=True)
        # 审计报告自身记录了绝对路径，再次脱敏
        report = os.path.join(dp, "audit_report.json")
        if os.path.exists(report):
            sanitize_file(report)
        # session_results.json / checkpoint.json 等一并再过一遍（前面已处理，双保险略过）

    print("replays and audit reports regenerated under sanitized content")
    return 0


if __name__ == "__main__":
    sys.exit(main())
