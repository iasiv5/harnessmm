# 开源发布清单（GitHub）

## 已完成的脱敏（2026-11-03）

| 项 | 处理 | 验证 |
|---|---|---|
| 凭据读取逻辑 | 重构为 `harness/auth.py`：只认环境变量（`HARNESSMM_API_KEY` 或 `HARNESSMM_CREDENTIALS_FILE`+`HARNESSMM_CREDENTIALS_REF`），仓库不含任何本机路径、凭据文件路径或密钥引用名硬编码 | 本机经 `.env.local`（gitignored）注入凭据复跑验证通过 |
| 模型端点 | `harness/config.py` 支持环境变量覆盖（`HARNESSMM_BASE_URL`/`HARNESSMM_MODEL`/`HARNESSMM_PROVIDER`）；默认值为公开商业端点，非私有信息 | — |
| 运行记录 | `runs/` 全部文本做机械路径替换（`/home/ubuntu/...` → `<workspace>`/`~`），替换后回放与审计报告重新生成，审计保持 PASS | `grep -rl "/home/ubuntu" runs/` = 0 |
| 密钥泄漏扫描 | 用真实密钥值全项目 grep，无命中；`runs/*/raw/` 供应商响应与 `ledger.jsonl` 的 `request_body_preview` 均不含 Authorization 头 | 0 命中 |
| 邮箱/个人标识 | 全项目 grep 无命中 | 0 命中 |
| 本机缓存 | `.gitignore` 排除 `.venv/`、`__pycache__/`、`.pytest_cache/`、`*.zip`、`.env.local` | `runs/` 内缓存目录已删除 |

## 上传前需要人工确认

1. **LICENSE**：仓库还没有 LICENSE 文件，选定后添加（MIT/Apache-2.0 皆可）。
2. **仓库名与简介**：建议 `harnessmm-mechanism-replay` 或类似；简介注明"机制复现，非完整复现"。
3. **README 顶部定位声明**：已有（"机制复现（mechanism demonstration），不是论文完整复现"）。
4. **runs/ 是否随仓库发布**：建议发布（这是本项目"以真实记录回放为主"的核心证据），
   体积 ~2.2MB 可接受；若想瘦身可只保留 `runs/formal-1/` + `runs/README.md`，
   dev-1 可后续补传。
5. **公众号文章中的 GitHub 链接**：已替换为真实地址 `iasiv5/harnessmm`（正文结尾 + 发布组件）。

## 发布状态（2026-11-03 完成）

- [x] LICENSE：MIT，已入库
- [x] 全量终审：8 条记录结构审计 PASS；基础设施测试 22/22 通过
- [x] 脱敏复核：`/home/ubuntu` 残留 0；真实密钥值全库扫描 0 命中；`.env.local` 确认被忽略
- [x] 推送：`main` → https://github.com/iasiv5/harnessmm（commit a51943a）
- [x] 远端抽查：LICENSE / README / results / 公众号草稿 / auth.py 均 200 可达；
      远端 events 与 ledger 抽样无路径残留、无凭据痕迹

后续维护提醒：本目录是父仓 `adhoc_jobs/` 下的嵌套 git 仓库；改动后同步到远端用
`git add -A && git commit && git push`；重跑实验或审计后，推送前先跑一遍
`tools/sanitize_for_release.py --runs-dir runs`。

## 复跑本实验（本机）

```bash
set -a; source .env.local; set +a   # 本机凭据注入（.env.local 不入库）
./.venv/bin/python run_experiment.py --session runs/<新会话> --arms common,r1,r2,r3
```

注意：重跑审计后 `audit_report.json` 会重新写入本机绝对路径，
上传前需再执行一次 `./.venv/bin/python tools/sanitize_for_release.py --runs-dir runs`。
