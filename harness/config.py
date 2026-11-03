"""冻结配置：正式运行前确定，运行期间不得改动。

任何改动都必须 bump `config_version` 并在 docs 中记录原因（不允许看到结果后
静默修改配置再称之为同一次实验）。

模型端点与名称可用环境变量覆盖（换 provider 复跑时不用改代码）：
  HARNESSMM_BASE_URL / HARNESSMM_MODEL
密钥读取见 harness/auth.py（纯环境变量驱动）。
"""

import hashlib
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FROZEN = {
    "config_version": "v1",

    # 模型与解码（对所有 arm 完全一致）
    "provider": os.environ.get("HARNESSMM_PROVIDER", "zai-coding-cn"),
    "model": os.environ.get("HARNESSMM_MODEL", "glm-5.3-flash"),
    "base_url": os.environ.get("HARNESSMM_BASE_URL",
                               "https://open.bigmodel.cn/api/coding/paas/v4"),
    "thinking": "disabled",
    "temperature": 0.1,
    "max_tokens_action": 2000,
    "max_tokens_summary": 1500,

    # 预算与超时
    "per_call_timeout_s": 120,
    "max_retries_transport": 2,
    "budget_common_calls": 24,
    "budget_arm_calls": 16,
    "budget_max_total_tokens_per_run": 400000,
    "tool_timeout_s": 180,

    # 工具白名单（argv 的规范形式，逐字节匹配）
    "command_whitelist": [
        "python -m pytest tests/unit -q",
        "python -m pytest tests/integration -q",
        "python -m pytest tests -q",
        "python tools/build_artifact.py --out dist/package.zip",
        "python tools/verify_artifact.py --file dist/package.zip",
    ],
    "tool_timeout_s_per_command": 180,

    # 工具权限
    "readable_paths": "*",           # workspace 内任意相对路径
    "writable_paths": ["config_parser.py"],  # 只允许修复目标文件；tests/tools 只读
    "protected_files": [             # 评分器防篡改清单来源
        "server.conf",
        "tests/unit/test_parse_port.py",
        "tests/integration/test_server_config.py",
        "tests/integration/test_environment_probe.py",
        "tools/build_artifact.py",
        "tools/verify_artifact.py",
    ],

    # 上下文策略：全历史保留（小任务），所有 arm 一致
    "context_policy": "full_history",

    # 故障注入（仅 R3）：工具层在集成测试命令的环境里注入一次真实失败
    "fault_inject_env": "HARNESSMM_FAULT_INJECT",
    "fault_inject_value": "integration",
    "fault_inject_command": "python -m pytest tests/integration -q",

    # 图约束
    "max_graph_nodes": 8,
    "guard_allowed_keys": ["exit_code", "stdout_contains", "stderr_contains"],

    # 教学任务
    "task_id": "config-port-hex",
    "task_prompt_version": "v1",
}

# workspace 哈希排除项：构建产物与缓存不算工作区变更
WORKSPACE_HASH_EXCLUDES = {"__pycache__", ".pytest_cache", "dist"}


def canonical_config():
    return json.dumps(FROZEN, sort_keys=True, ensure_ascii=False, indent=2)


def config_hash():
    return "sha256:" + hashlib.sha256(canonical_config().encode("utf-8")).hexdigest()[:16]


def python_executable():
    return os.path.join(BASE, ".venv", "bin", "python")
