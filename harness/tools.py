"""工具层：白名单命令执行、受限读写、真实回执、工作区哈希、故障注入。

- 每次工具执行产出真实回执（receipt），带 id 与内容哈希。
- 命令白名单逐字节匹配冻结配置，不允许任何其他命令。
- 故障注入（仅 R3）：对集成测试命令在子进程环境注入 HARNESSMM_FAULT_INJECT，
  让 tests/integration/test_environment_probe.py 真实失败（pytest 真实退出非零），
  一次性触发，全程显式标注。
"""

import datetime
import hashlib
import json
import os
import subprocess

from .config import FROZEN, WORKSPACE_HASH_EXCLUDES


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(obj):
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:16]


class ToolError(Exception):
    pass


class ToolLayer:
    def __init__(self, workspace_dir, event_log=None, fault_inject=False, receipts_dir=None):
        self.ws = os.path.abspath(workspace_dir)
        self.event_log = event_log
        self.fault_armed = bool(fault_inject)
        self.receipts_dir = receipts_dir
        if self.receipts_dir:
            os.makedirs(self.receipts_dir, exist_ok=True)
        self.injection_counter = 0
        self.receipt_seq = 0
        self.py = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".venv", "bin", "python")

    # ---------- workspace hash ----------
    def workspace_hash(self):
        entries = []
        for root, dirs, files in os.walk(self.ws):
            dirs[:] = sorted(d for d in dirs if d not in WORKSPACE_HASH_EXCLUDES)
            for f in sorted(files):
                if f.startswith("."):
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, self.ws)
                with open(p, "rb") as fh:
                    entries.append((rel, hashlib.sha256(fh.read()).hexdigest()))
        return "sha256:" + hashlib.sha256(
            canonical_json(entries).encode("utf-8")).hexdigest()[:24]

    # ---------- whitelist ----------
    def _match_command(self, argv):
        cmd = " ".join(argv)
        for wl in FROZEN["command_whitelist"]:
            if cmd == wl.replace("python ", "%s " % self.py) or cmd == wl:
                return wl
        return None

    # ---------- receipt ----------
    def _receipt(self, tool_name, args, extra=None):
        self.receipt_seq += 1
        receipt = {
            "tool_receipt_id": "rcpt-%s-%03d" % (self.event_log.run_id if self.event_log else "x",
                                                 self.receipt_seq),
            "tool_name": tool_name,
            "args": args,
            "timestamp": now_iso(),
        }
        if extra:
            receipt.update(extra)
        receipt["tool_receipt_hash"] = content_hash(receipt)
        if self.receipts_dir:
            with open(os.path.join(
                    self.receipts_dir, "%s.json" % receipt["tool_receipt_id"]),
                    "w", encoding="utf-8") as fh:
                json.dump(receipt, fh, ensure_ascii=False, indent=2)
        return receipt

    def execute(self, tool_name, args):
        """执行白名单工具并返回真实回执。参数必须是完整字面值。"""
        receipt = self._execute_inner(tool_name, args)
        if self.event_log is not None:
            self.event_log.set_workspace_hash(self.workspace_hash())
        return receipt

    def _execute_inner(self, tool_name, args):
        if tool_name == "run_command":
            return self._run_command(args)
        if tool_name == "read_file":
            return self._read_file(args)
        if tool_name == "write_file":
            return self._write_file(args)
        raise ToolError("unknown tool: %s" % tool_name)

    # ---------- run_command ----------
    def _run_command(self, args):
        if not isinstance(args, dict) or "argv" not in args:
            raise ToolError("run_command args must be {\"argv\": [...]}")
        argv = args["argv"]
        if (not isinstance(argv, list) or not argv
                or not all(isinstance(a, str) for a in argv)):
            raise ToolError("argv must be a non-empty list of strings")
        wl = self._match_command(argv)
        if wl is None:
            raise ToolError("command not in frozen whitelist: %s" % " ".join(argv))

        env = dict(os.environ)
        env.pop("HARNESSMM_FAULT_INJECT", None)
        injected = None
        canonical = wl.replace("%s " % self.py, "python ")
        if self.fault_armed and canonical == FROZEN["fault_inject_command"]:
            env[FROZEN["fault_inject_env"]] = FROZEN["fault_inject_value"]
            self.injection_counter += 1
            injected = {
                "injection_id": "inj-%s-%d" % (self.event_log.run_id, self.injection_counter),
                "env_var": FROZEN["fault_inject_env"],
                "env_value": FROZEN["fault_inject_value"],
                "note": "teaching-rig injected integration fault; real non-zero exit; one-shot",
            }
            self.fault_armed = False  # 一次性
            if self.event_log:
                self.event_log.emit("fault_injected", data=injected)

        t0 = datetime.datetime.now(datetime.timezone.utc)
        # 冻结配置约定：argv[0]=="python" 指项目 venv 解释器（公开、确定性替换，
        # 回执中同时记录 resolved_argv 供审计核对；创作参数本身不改写）
        exec_argv = list(argv)
        if exec_argv[0] == "python":
            exec_argv[0] = self.py
        try:
            proc = subprocess.run(exec_argv, cwd=self.ws, env=env,
                                  capture_output=True, text=True,
                                  timeout=FROZEN["tool_timeout_s_per_command"])
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = "TOOL TIMEOUT after %ss" % FROZEN["tool_timeout_s_per_command"]
            timed_out = True

        receipt = self._receipt("run_command", {"argv": argv}, extra={
            "exit_code": exit_code,
            "stdout": stdout[-12000:],
            "stderr": stderr[-6000:],
            "duration_s": (datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds(),
            "timed_out": timed_out,
            "whitelist_entry": canonical,
            "resolved_argv": exec_argv,
            "injected_fault": injected,
        })
        receipt["tool_receipt_hash"] = content_hash(
            {k: v for k, v in receipt.items() if k != "tool_receipt_hash"})
        if self.event_log:
            self.event_log.emit("tool_execute",
                                tool_receipt_id=receipt["tool_receipt_id"],
                                tool_receipt_hash=receipt["tool_receipt_hash"],
                                data={"tool_name": "run_command", "argv": argv,
                                      "exit_code": exit_code,
                                      "stdout_tail": stdout[-600:],
                                      "stderr_tail": stderr[-400:],
                                      "injected_fault": injected})
        return receipt

    # ---------- read_file ----------
    def _read_file(self, args):
        path = (args or {}).get("path")
        if not isinstance(path, str) or not path:
            raise ToolError("read_file args must be {\"path\": str}")
        full = os.path.abspath(os.path.join(self.ws, path))
        if not full.startswith(self.ws + os.sep):
            raise ToolError("path escapes workspace: %s" % path)
        if not os.path.isfile(full):
            raise ToolError("file not found: %s" % path)
        with open(full, "r", encoding="utf-8") as fh:
            text = fh.read()
        receipt = self._receipt("read_file", {"path": path}, extra={
            "exists": True,
            "content": text[:12000],
            "truncated": len(text) > 12000,
        })
        if self.event_log:
            self.event_log.emit("tool_execute",
                                tool_receipt_id=receipt["tool_receipt_id"],
                                tool_receipt_hash=receipt["tool_receipt_hash"],
                                data={"tool_name": "read_file", "path": path,
                                      "bytes": len(text)})
        return receipt

    # ---------- write_file ----------
    def _write_file(self, args):
        path = (args or {}).get("path")
        content = (args or {}).get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ToolError("write_file args must be {\"path\": str, \"content\": str}")
        if path not in FROZEN["writable_paths"]:
            raise ToolError(
                "write to %r not allowed; writable paths: %s (tests/tools are read-only "
                "by frozen experiment config)" % (path, FROZEN["writable_paths"]))
        full = os.path.abspath(os.path.join(self.ws, path))
        if not full.startswith(self.ws + os.sep):
            raise ToolError("path escapes workspace: %s" % path)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        receipt = self._receipt("write_file", {"path": path}, extra={
            "bytes_written": len(content.encode("utf-8")),
            "content_sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        })
        if self.event_log:
            self.event_log.emit("tool_execute",
                                tool_receipt_id=receipt["tool_receipt_id"],
                                tool_receipt_hash=receipt["tool_receipt_hash"],
                                data={"tool_name": "write_file", "path": path,
                                      "bytes": receipt["bytes_written"]})
        return receipt
