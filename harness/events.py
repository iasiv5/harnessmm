"""结构化事件日志：JSONL，逐条含序号、因果引用与内容摘要。"""

import datetime
import json
import os
import threading

REQUIRED_FIELDS = [
    "run_id", "task_id", "arm", "event_id", "event_type", "sequence",
    "timestamp", "config_hash", "workspace_hash",
]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class EventLog:
    def __init__(self, path, run_id, arm, task_id, config_hash):
        self.path = path
        self.run_id = run_id
        self.arm = arm
        self.task_id = task_id
        self.config_hash = config_hash
        self.sequence = 0
        self._lock = threading.Lock()
        self.events = []
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _workspace_hash_provider(self):
        # 由 runner 注入：返回当前工作区哈希字符串
        return getattr(self, "_ws_hash", "")

    def set_workspace_hash(self, ws_hash):
        self._ws_hash = ws_hash

    def emit(self, event_type, **fields):
        with self._lock:
            self.sequence += 1
            event = {
                "event_id": "evt-%s-%06d" % (self.run_id, self.sequence),
                "sequence": self.sequence,
                "run_id": self.run_id,
                "task_id": self.task_id,
                "arm": self.arm,
                "event_type": event_type,
                "timestamp": now_iso(),
                "config_hash": self.config_hash,
                "workspace_hash": self._ws_hash,
            }
            for k, v in fields.items():
                if v is not None:
                    event[k] = v
            self.events.append(event)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event

    def load(path):
        events = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
