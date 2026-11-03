# HarnessMM 机制复现实验（教学任务版）

本项目用一个小型、真实、可理解的代码任务，复现论文
《HarnessMM: Recursive Self-Improvement for Harness Token Efficiency》
（[arXiv:2610.32813v1](https://arxiv.org/html/2610.32813v1)）中
"保存已确定的决策 → 跨工具反馈持续复用 → 条件满足时替代模型回调 → 异常时回退"的运行机制。

**项目定位**：机制复现（mechanism demonstration），不是论文完整复现，也不做普遍性能声明。
机制映射、简化点与不可证明的结论见 [`docs/paper_mapping.md`](docs/paper_mapping.md)。

**状态（2026-11-03）**：真实模型实验已完成。正式运行 `runs/formal-1`
（模型 glm-5.3-flash）四条记录全部通过结构审计；技术文章见
[`article/article.md`](article/article.md)，公众号版（精简，面向大众读者）见
[`article/wechat/draft.md`](article/wechat/draft.md)，结果汇总见
[`runs/formal-1/results.md`](runs/formal-1/results.md)。
开源发布前请阅读 [`docs/release_checklist.md`](docs/release_checklist.md)。

## 目录结构

```
harnessmm_mechanism/
├── README.md                  ← 本文件
├── docs/paper_mapping.md      ← 论文机制映射、钢人评估、设计取舍
├── workspace/                 ← 教学任务（被测模型可见的工作区模板）
│   ├── config_parser.py       ← 初始有 bug：只支持十进制端口
│   ├── server.conf            ← 使用 0x1F90 端口的配置
│   ├── tests/unit/            ← N1 单元测试（含全部合法/非法向量）
│   ├── tests/integration/     ← N2 集成测试 + 环境探针（R3 故障开关）
│   └── tools/                 ← N3 build_artifact.py / N4 verify_artifact.py
├── harness/                   ← 实验装置（被测模型不可见）
│   ├── config.py              ← 冻结配置（模型、解码、白名单、预算）
│   ├── llm.py                 ← 统一模型调用入口 + 成本账本
│   ├── tools.py               ← 白名单工具层 + 真实回执 + 故障注入
│   ├── graph.py               ← EDG schema 校验 + 守卫求值 + 确定性选择
│   ├── runner.py              ← 共同修复阶段 + R1/R2/R3 三条臂编排
│   ├── checkpoint.py          ← 共同检查点
│   ├── events.py              ← JSONL 结构化事件
│   └── prompts.py             ← 提示词（模式差异仅限验证协议段）
├── tools/
│   ├── scorer.py              ← 独立评分器（防篡改 + 独立向量，不暴露给模型）
│   ├── audit.py               ← 自动结构审计（10+ 项机器检查）
│   └── replay.py              ← 只读回放生成器（静态 HTML）
├── reference/                 ← 参考答案（不暴露给被测模型）
├── tests_infra/               ← 基础设施自测（假模型客户端夹具，明确标注）
├── runs/                      ← 运行记录（events/ledger/receipts/graphs/回放/审计）
└── article/                   ← 中文说明文章
```

## 环境准备

依赖只有一个：Python 3.11+ 与 pytest（项目自带 .venv）。

```bash
cd adhoc_jobs/harnessmm_mechanism
python3.11 -m venv .venv
./.venv/bin/pip install pytest
```

真实模型调用需要凭据，全部通过环境变量驱动（仓库不含任何本机路径或密钥）：

```bash
# 方式一：直接给 key
export HARNESSMM_API_KEY=<你的 key>

# 方式二：从任意 YAML 凭据库读取（键名 + 引用名）
export HARNESSMM_CREDENTIALS_FILE=<凭据文件路径>
export HARNESSMM_CREDENTIALS_REF=<该文件 refs 下的键名>
```

端点与模型名同样可用环境变量覆盖：`HARNESSMM_BASE_URL` / `HARNESSMM_MODEL`。
**没有凭据时无法复跑真实实验**，但基础设施测试（假模型夹具）与审计/回放工具仍可运行。
`runs/` 下的历史记录已做机械脱敏（绝对路径替换），替换规则见
[`tools/sanitize_for_release.py`](tools/sanitize_for_release.py)。

## 基础设施测试（不调用任何真实模型）

```bash
./.venv/bin/python -m pytest tests_infra -q
```

夹具使用"假模型客户端"（脚本化回复，账本记录带 `synthetic: true` 标记），
工具层执行的是**真实命令**（真实回执）。正式审计在无 `--allow-synthetic` 时
会拒绝任何含合成记录的运行。

## 真实实验

```bash
# 一条命令：共同修复（真实模型修 bug）→ 检查点 C0 → R1/R2/R3 三条臂
./.venv/bin/python run_experiment.py --session runs/formal-1 --arms common,r1,r2,r3
```

- `common`：模型修复 workspace 里的解析器 bug，独立评分器复核通过后保存检查点 C0。
- `R1`：逐步回调基线——每个工具返回后调用模型，每次只执行一个验证动作（受控协议）。
- `R2`：持续决策图——模型一次提交当前动作 + 后续动作图（完整参数 + 守卫），
  运行时在守卫通过时**自动执行已保存节点，不调用模型**；图耗尽后模型做最终总结。
- `R3`：与 R2 同一实现，但工具层在集成测试命令上注入一次**真实失败**
  （环境变量触发 `tests/integration/test_environment_probe.py` 真实退出非零，
  一次性触发、全程标注）。下游构建/校验被阻断，系统带真实回执回退到模型。
- 预算上限（冻结配置）：公共阶段 ≤ 24 次调用，每臂 ≤ 16 次，单次超时 120s。
  超限即停止并如实记录。

## 审计与回放

```bash
# 结构审计（正式审计：无 --allow-synthetic，有合成记录即 FAIL）
./.venv/bin/python tools/audit.py --run-dir runs/formal-1/arm_r2 --expect-mode R2

# 生成只读回放（三栏：模型已提交的决策 / 执行时间线 / 真实请求账本）
./.venv/bin/python tools/replay.py --run-dir runs/formal-1/arm_r2

# 结果重算（调用数、token、bypass 数；数字全部来自账本，无估算）
./.venv/bin/python tools/audit.py --run-dir runs/formal-1/arm_r1 --expect-mode R1
```

回放页面顶部标注"真实运行记录回放，非现场执行"；所有输出经转义渲染；
页面内嵌 `events.jsonl` 的 sha256，由审计第 10 项核验"回放与原始事件一致"。

## 运行记录选择规则

先用开发运行（`runs/dev-*`）验证基础设施与模型协议，再冻结配置跑正式记录（`runs/formal-*`）。
展示规则：**首个通过结构审计的完整正式运行**；开发运行与其他尝试全部保留在 `runs/`
下供检查，不做静默筛选。开发运行中如修改过提示词/配置，`config_version` 会 bump
并在 docs 记录；正式运行开始后配置不再改动。

## 已知边界（详见 docs/paper_mapping.md §4）

- 单任务结果不证明泛化收益；token 是否下降取决于已解决动作的可得性。
- "审计通过"只证明结构与参数一致性，不证明任务语义必然正确。
- R1 的"每步一动作"是实验控制，不是普通 Agent 的能力上限。
- 故障注入是人为构造的教学装置，不是论文机制的一部分。
- 本项目未实现 RSI、提示词自动优化、上下文压缩。
