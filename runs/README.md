# 运行记录清单与选择规则

## 选择规则（预先确定）

展示规则：**首个通过全部结构审计的完整正式运行**。开发运行与其他尝试全部保留、
全部可查，不做静默筛选。不在看到结果后修改配置再把结果称为同一次实验。

## 记录清单

| 会话 | 性质 | 配置 | 内容 | 状态 |
|---|---|---|---|---|
| `runs/dev-1` | 开发运行（协议验证） | `sha256:292635643247e721` | common(19 次调用) + R1/R2/R3 | 全部 completed，审计 PASS |
| `runs/formal-1` | **正式运行（文章展示）** | `sha256:292635643247e721` | common(16 次调用) + R1/R2/R3 | 全部 completed，审计 PASS |

两次会话使用完全相同的冻结配置（`harness/config.py`，config_version v1）；
dev-1 通过后配置未做任何改动，formal-1 直接复用。每会话目录含
`session_config.json`（配置快照 + 哈希）可独立核验。

## 每条记录的目录结构

```
arm_rX/ 或 common/
├── run.json            # run_id、arm、故障注入标志
├── session_config.json # 会话级，位于上层
├── events.jsonl        # 结构化事件（含序号、因果引用、内容哈希）
├── ledger.jsonl        # 模型请求账本（供应商实测 usage、provider request_id、重试）
├── raw/                # 供应商原始响应 JSON（脱敏后保存）
├── receipts/           # 工具完整回执（含哈希；R3 含注入标注回执）
├── graphs/             # 模型创作的决策图（原样保存，含创作请求 ID 与工作区哈希）
├── replay.html         # 只读回放（内嵌事件文件 sha256）
└── audit_report.json   # 结构审计报告
```
