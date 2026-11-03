"""教学装置：环境探针测试。

正常情况下该测试恒通过，与任务本身无关。
当且仅当运行环境变量 HARNESSMM_FAULT_INJECT=integration 时，
本测试真实失败（pytest 真实退出非零），用于 R3 演示"故障后下游阻断与模型回退"。
该装置在项目 README、冻结配置与审计事件中全程显式标注，属于人为构造的教学装置，
不是论文机制的一部分。
"""

import os


def test_environment_probe_clean():
    injected = os.environ.get("HARNESSMM_FAULT_INJECT")
    assert injected != "integration", (
        "HARNESSMM TEACHING RIG: injected integration fault is ACTIVE "
        "(env HARNESSMM_FAULT_INJECT=integration). This failure is "
        "deliberately injected by the experiment harness and is recorded "
        "as an injected_fault event in the run ledger."
    )
