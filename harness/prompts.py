"""提示词：所有 arm 共享同一任务描述与工具协议；模式差异仅限验证协议段。"""

COMMANDS_LIST = """- run_command {"argv": ["python", "-m", "pytest", "tests/unit", "-q"]}
- run_command {"argv": ["python", "-m", "pytest", "tests/integration", "-q"]}
- run_command {"argv": ["python", "-m", "pytest", "tests", "-q"]}
- run_command {"argv": ["python", "tools/build_artifact.py", "--out", "dist/package.zip"]}
- run_command {"argv": ["python", "tools/verify_artifact.py", "--file", "dist/package.zip"]}
- read_file {"path": "<workspace-relative path>"}
- write_file {"path": "config_parser.py", "content": "<full new file content>"}  (only config_parser.py is writable; tests/ and tools/ are read-only)"""

TASK_DESCRIPTION = """# 任务

修复一个小型 Python 配置解析器项目，使 parse_port 同时支持：
1. 十进制端口字符串（既有行为，保持不变）；
2. "0x"/"0X" 前缀的十六进制端口字符串（大小写前缀均可，如 "0x1F90" -> 8080）。

保持既有非法输入校验规则不变：
- 输入必须是 str；去除首尾空白后不得为空；
- 十进制分支只接受纯数字（无符号、无小数点）；
- 十六进制分支 "0x" 后必须至少一位十六进制数字，且其余字符全部是十六进制数字；
- 合法范围 [0, 65535]，越界一律 ValueError；
- 非法输入抛 ValueError，不得崩溃成其他异常类型。

先读取 config_parser.py、tests/ 与 server.conf 明确既有契约，再修复。不要引入任务无关的输入格式扩展。

# 工具（白名单，参数必须逐字节按此写出）
%s

# 回复协议（每次回复必须是单个 JSON 对象，不要输出其他文字）
继续工作时：
{"thought": "<简短分析>", "action": {"tool_name": "...", "args": {...}}}
认为任务完成时：
{"thought": "...", "phase_complete": true, "summary": "<做了什么改动、测试结果如何>"}

工具会返回真实回执（exit_code、stdout、stderr）。"phase_complete" 会被独立评分器复核，
评分不通过你会收到真实失败输出并需要继续。""" % COMMANDS_LIST

ARM_VERIFICATION_PREAMBLE = """# 验证阶段（从共同检查点出发）

代码修复已完成并通过独立评分。现在进入验证阶段，按顺序完成四件事：
N1 单元测试 -> N2 集成测试 -> N3 构建产物 -> N4 校验产物，最后给出总结。
工作区与修复完成时完全一致。"""

R1_MODE_RULES = """# 本模式规则（R1：逐步回调基线）

- 每次回复只允许执行一个独立验证动作（一个 run_command 或一次 read_file）。
- 禁止一次规划或执行多个动作；每次收到工具回执后再决定下一步。
- 全部验证完成后回复 {"thought": "...", "phase_complete": true, "summary": "..."}。
- 这是实验协议：你每一步都必须等待真实回执。"""

GRAPH_MODE_RULES = """# 本模式规则（R2/R3：持续决策图与回调替代）

除了当前动作，你可以在同一次回复中提交一张"决策图"（EDG），把**参数已完全确定**的
后续动作提前保存下来：

{"thought": "...",
 "action": {"tool_name": "...", "args": {...}},
 "graph": {"graph_id": "<唯一id>", "rationale": "<为什么这些后续动作参数已确定>",
           "nodes": [
             {"node_id": "n1", "tool_name": "run_command",
              "args": {"argv": ["python", "-m", "pytest", "tests/integration", "-q"]},
              "depends_on": "root",
              "guard": {"exit_code": 0}},
             {"node_id": "n2", "...": "...", "depends_on": "n1", "guard": {"exit_code": 0}}
           ]}}

图节点规则：
- args 必须是完整字面值；禁止占位符（如 "<等待结果>"、"TBD"）。
- guard 只允许键：exit_code（整数）、stdout_contains（字符串）、stderr_contains（字符串），
  只能对该节点前置动作的真实回执求值。
- depends_on 必须是 "root"（你的当前动作）或更早的 node_id（链式）。
- 运行时会在守卫通过时自动执行已保存节点，**不调用模型**；
  守卫失败或条件不明时会带着真实回执回到你这里，剩余图作废。
- 图耗尽后你会收到 GRAPH_EXHAUSTED 消息，届时回复：
  {"task_complete": true, "summary": "<最终总结>"}

如果不提交 graph，运行时会像普通模式一样在每次工具返回后调用你。"""

FINAL_SUMMARY_RULES = """图的所有节点已真实执行完毕。请回复最终总结（单个 JSON）：
{"task_complete": true, "summary": "<验证链执行情况、每步结果、最终任务状态>"}"""


def common_system_prompt():
    return TASK_DESCRIPTION


def arm_system_prompt(mode):
    if mode == "r1":
        return TASK_DESCRIPTION + "\n\n" + ARM_VERIFICATION_PREAMBLE + "\n\n" + R1_MODE_RULES
    return TASK_DESCRIPTION + "\n\n" + ARM_VERIFICATION_PREAMBLE + "\n\n" + GRAPH_MODE_RULES
