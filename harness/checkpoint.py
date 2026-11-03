"""共同检查点：修复后的工作区快照 + 对话历史 + 成本前缀 + 环境版本。"""

import json
import os
import shutil
import subprocess

from .tools import content_hash


def create_checkpoint(dest_dir, workspace_dir, conversation, ledger_summary,
                      common_run_id, config_hash, ws_hash):
    os.makedirs(dest_dir, exist_ok=True)
    ws_dest = os.path.join(dest_dir, "workspace")
    if os.path.exists(ws_dest):
        shutil.rmtree(ws_dest)
    shutil.copytree(workspace_dir, ws_dest,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "dist"))

    env_info = {
        "python": subprocess.run(["python3", "--version"], capture_output=True,
                                 text=True).stdout.strip(),
        "venv_python": subprocess.run([os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".venv", "bin", "python"), "--version"],
            capture_output=True, text=True).stdout.strip(),
        "pytest": subprocess.run([os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".venv", "bin", "python"), "-m", "pytest",
            "--version"], capture_output=True, text=True).stdout.strip(),
    }
    meta = {
        "checkpoint_id": os.path.basename(dest_dir),
        "common_run_id": common_run_id,
        "config_hash": config_hash,
        "workspace_hash": ws_hash,
        "conversation": conversation,
        "ledger_prefix": ledger_summary,
        "env": env_info,
    }
    with open(os.path.join(dest_dir, "checkpoint.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return meta


def load_checkpoint(ckpt_dir):
    with open(os.path.join(ckpt_dir, "checkpoint.json"), encoding="utf-8") as fh:
        return json.load(fh)


def checkpoint_id_of(ckpt_dir):
    return content_hash(load_checkpoint(ckpt_dir)["workspace_hash"] + os.path.basename(ckpt_dir))[:16]
