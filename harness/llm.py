"""统一模型调用入口：所有真实模型请求都经过这里。

记账内容：run_id、request_id、provider_request_id、模型、起止时间、
输入输出内容（脱敏）、供应商实测 usage、失败与重试、缓存信息、配置摘要。
预算上限在此强制执行。禁止任何绕过本入口的模型调用。
"""

import datetime
import json
import os
import time
import urllib.error
import urllib.request

from .config import FROZEN
from .auth import load_api_key, scrub as scrub_key


class BudgetExceededError(RuntimeError):
    pass


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ModelClient:
    def __init__(self, run_id, arm, ledger_path, raw_dir, event_log=None,
                 budget_calls=16, budget_tokens=400000):
        self.run_id = run_id
        self.arm = arm
        self.ledger_path = ledger_path
        self.raw_dir = raw_dir
        self.event_log = event_log
        self.budget_calls = budget_calls
        self.budget_tokens = budget_tokens
        self.calls = 0
        self.total_tokens = 0
        self.records = []
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        self.api_key = load_api_key()
        self._scrub_target = self.api_key

    def _scrub(self, text):
        return scrub_key(text, self._scrub_target)

    def _budget_check(self):
        if self.calls >= self.budget_calls:
            raise BudgetExceededError(
                "call budget exhausted: %d/%d" % (self.calls, self.budget_calls))
        if self.total_tokens >= self.budget_tokens:
            raise BudgetExceededError(
                "token budget exhausted: %d/%d" % (self.total_tokens, self.budget_tokens))

    def call(self, purpose, messages, max_tokens=None, route_hint=None):
        """发起一次真实模型调用并记账。返回 (content, record)。"""
        self._budget_check()
        request_id = "req-%s-%03d" % (self.run_id, self.calls + 1)
        self._route_hint = route_hint or "slow"
        body = {
            "model": FROZEN["model"],
            "messages": messages,
            "temperature": FROZEN["temperature"],
            "thinking": {"type": FROZEN["thinking"]},
            "max_tokens": max_tokens or FROZEN["max_tokens_action"],
        }
        record = {
            "request_id": request_id,
            "run_id": self.run_id,
            "arm": self.arm,
            "purpose": purpose,
            "route_hint": self._route_hint,
            "provider": FROZEN["provider"],
            "model": FROZEN["model"],
            "base_url": FROZEN["base_url"],
            "request_started_at": now_iso(),
            "provider_request_id": None,
            "usage": None,
            "retries": [],
            "status": "error",
            "error": None,
            "request_body_preview": self._scrub(json.dumps(body, ensure_ascii=False))[:4000],
            "response_preview": None,
        }
        attempt = 0
        t0 = time.time()
        while True:
            attempt += 1
            req = urllib.request.Request(
                FROZEN["base_url"] + "/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer %s" % self.api_key,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=FROZEN["per_call_timeout_s"]) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                break
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")[:2000]
                record["retries"].append({"attempt": attempt, "kind": "http_%d" % exc.code,
                                          "detail": err_body, "at": now_iso()})
                if exc.code >= 500 and attempt <= FROZEN["max_retries_transport"]:
                    time.sleep(2 * attempt)
                    continue
                record["error"] = "http_%d: %s" % (exc.code, err_body)
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                record["retries"].append({"attempt": attempt, "kind": "transport",
                                          "detail": repr(exc)[:500], "at": now_iso()})
                if attempt <= FROZEN["max_retries_transport"]:
                    time.sleep(2 * attempt)
                    continue
                record["error"] = "transport: %r" % exc
            # 不可恢复错误：记账并抛出
            record["latency_ms"] = int((time.time() - t0) * 1000)
            record["ended_at"] = now_iso()
            self._finalize(record, None)
            raise RuntimeError("model call failed permanently: %s" % record["error"])

        latency_ms = int((time.time() - t0) * 1000)
        record["provider_request_id"] = data.get("request_id") or data.get("id")
        record["usage"] = data.get("usage")
        record["status"] = "ok"
        record["latency_ms"] = latency_ms
        record["ended_at"] = now_iso()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        finish = choice.get("finish_reason")

        usage = data.get("usage") or {}
        self.calls += 1
        self.total_tokens += int(usage.get("total_tokens") or 0)

        # 保存供应商原始响应（脱敏后）
        raw_file = os.path.join(self.raw_dir, "%s_response.json" % request_id)
        with open(raw_file, "w", encoding="utf-8") as fh:
            fh.write(self._scrub(raw))
        record["raw_response_file"] = os.path.relpath(raw_file, os.path.dirname(self.ledger_path))

        content_s = self._scrub(content) if isinstance(content, str) else content
        record["response_preview"] = (content_s or "")[:2000]
        record["finish_reason"] = finish
        record["reasoning_content_present"] = bool(reasoning)
        record["content_full"] = content_s
        self._finalize(record, usage)

        content_for_call = content_s or ""
        return content_for_call, record

    def _finalize(self, record, usage):
        self.records.append(record)
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.event_log is not None and record["status"] == "ok":
            self.event_log.emit(
                "model_request",
                model_request_id=record["request_id"],
                provider_request_id=record.get("provider_request_id"),
                purpose=record["purpose"],
                route=record.get("route_hint", "slow"),
                tokens_in=(usage or {}).get("prompt_tokens"),
                tokens_out=(usage or {}).get("completion_tokens"),
                tokens_cached=(usage or {}).get("prompt_tokens_details", {}).get("cached_tokens"),
                tokens_reasoning=(usage or {}).get("completion_tokens_details", {}).get("reasoning_tokens"),
                latency_ms=record["latency_ms"],
                data={"status": record["status"], "finish_reason": record.get("finish_reason"),
                      "retries": record["retries"]},
            )
        elif self.event_log is not None:
            self.event_log.emit(
                "model_request_failed",
                model_request_id=record["request_id"],
                purpose=record["purpose"],
                latency_ms=record.get("latency_ms"),
                fallback_reason=record.get("error"),
            )

    def summary(self):
        ok = [r for r in self.records if r["status"] == "ok"]
        tok_in = sum((r.get("usage") or {}).get("prompt_tokens") or 0 for r in ok)
        tok_out = sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in ok)
        tok_cached = sum((r.get("usage") or {}).get("prompt_tokens_details", {}).get("cached_tokens") or 0 for r in ok)
        return {
            "model_calls": len(ok),
            "failed_calls": len(self.records) - len(ok),
            "tokens_in": tok_in,
            "tokens_out": tok_out,
            "tokens_cached": tok_cached,
            "tokens_total": tok_in + tok_out,
            "provider_reported": True,
        }
