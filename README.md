# RSIQS

A 股盘前推荐 + 模拟交易 + 递归自我迭代系统。

每个交易日 09:28 前经企业微信推送 ≤5 只推荐（含买入价上限、止损、目标价），盘中按止损/止盈/炸板提醒，
收盘后自动结算模拟单、LLM 复盘并更新 playbook，每周六用回测评估新策略变体。**系统不下实盘单**，
所有信号需要人工执行。衡量标准是扣成本后相对沪深 300 的超额收益。

## 环境要求

- Windows 10/11（计划任务、文件锁按 Windows 实现；其他平台未验证）
- Python 3.12（`pyproject.toml` 限定 `>=3.12,<3.13`）
- [uv](https://docs.astral.sh/uv/)
- 联网：行情来自 akshare（东财为主、新浪回退），LLM 走 OpenAI 兼容接口

## 快速开始

```powershell
uv sync
Copy-Item .env.example .env
notepad .env
```

`.env` 必填项（路径不要加双引号，`\n`、`\b` 会被当转义符）：

| 键 | 说明 |
|---|---|
| `LLM_BASE_URL` | **必填且无默认**，不填启动即报错。火山方舟填 `https://ark.cn-beijing.volces.com/api/v3`，换服务商改成其 `base_url` |
| `ARK_API_KEY` | 对应服务商的 API Key |
| `ARK_MODEL` | 模型 ID 或推理接入点（如 `ep-xxx`） |
| `WECOM_WEBHOOK_URL` | 企业微信群机器人完整 webhook 地址，留空则不推送 |
| `DB_PATH` | 默认 `data/rsiqs.sqlite` |
| `CAPITAL` / `MAX_PICKS` | 默认 `500000` / `5` |

配置读取顺序：**非空的系统环境变量 → 非空的 `.env` → 默认值**。系统里残留的 `ARK_API_KEY`
会覆盖 `.env`，排查时先看 `Get-ChildItem Env:ARK_*`。

### 冒烟（联网，不在 pytest 里）

```powershell
uv run python scripts/smoke_akshare.py
uv run python scripts/smoke_llm.py
uv run python -c "from config import get_settings; from notify.wecom import WeCom; print(WeCom(get_settings().wecom_webhook_url).send_text('RSIQS 连通测试'))"
```

### 初始化

```powershell
uv run python -m jobs.bootstrap
```

首次需下载约 800 只股票自 2015-06-01 起的日线，约 1 小时（数据源限流触发新浪回退时更久）；中断后重跑为增量。
会创建 4 个模拟账户、现任策略、playbook 与 `.integrity/manifest.json`，并在群里推送完成消息。

### 测试

```powershell
uv run pytest
```

测试不联网、不调用真实 LLM、不推送企业微信。

## 日常运行：Windows 计划任务

本机已注册 10 个任务：位于根路径 `\`、名称前缀 `RSIQS_`、SYSTEM 身份，统一经 `scripts\run_job.ps1`
进入仓库目录后调用仓库 `.venv` 的 python（不依赖 `uv` 在 PATH 中）：

| 任务 | 触发 | 入口 | 作用 |
|---|---|---|---|
| RSIQS_premarket_scan | 工作日 08:30 | `jobs.premarket_scan` | 量化扫描 + 两路 LLM 深度分析，为每只候选写出竞价区间、最高买入价、止损、目标 |
| RSIQS_news_scan | 工作日 08:35 | `jobs.news_scan` | 消息面影子通道：新闻 → LLM 选股 → 写入独立账户 `h:news`，只记录净值，不推送 |
| RSIQS_premarket_final | 工作日 09:25 | `jobs.premarket_final` | 等竞价数据落定（09:25:15）后规则过滤 + 轻量确认，09:28 前推送；09:35 之后视为过时跳过 |
| RSIQS_monitor | 工作日 09:30 起，每 2 分钟重复 5 小时 30 分 | `jobs.monitor` | 常驻，检查 `h:active` 持仓，触发止损/止盈/炸板即推送，15:00 退出 |
| RSIQS_midday | 工作日 11:35 | `jobs.midday` | 午盘简报：h:active 持仓盘中状态 + 早盘候选回到买入区间的观察项；不改早盘推荐、不成交 |
| RSIQS_settle | 工作日 15:35 起，每 20 分钟重试至 18:30 | `jobs.settle` | 拉日线结算模拟单、更新净值（当日日线 15:10 后才算可用；失败可补跑） |
| RSIQS_review | 工作日 16:05 起，每 20 分钟重试至 18:40 | `jobs.review` | LLM 复盘当日推荐与实际走势、更新 playbook、推送日报，并回补漏掉的日子 |
| RSIQS_research | 周六 10:00 | `jobs.research` | 提出策略变体、回测评估，合格者进影子盘（错过即跳过） |
| RSIQS_doctor | 每天 07:30 | `jobs.doctor` | 体检 + 已知故障自动修复 + 未修复异常诊断 |
| RSIQS_boot_catchup | 开机时 | `jobs.settle` → `jobs.review` | 开机补跑，依次执行 |

查看状态与下次运行时间：

```powershell
.\scripts\service.ps1 status
```

## 部署 / 启动 / 暂停 / 移除

统一入口 `scripts\service.ps1`（`install/start/stop/remove` 需要**管理员 PowerShell**，`status` 不需要）：

```powershell
.\scripts\service.ps1 install              # 检查 venv/.env 必填项，注册 10 个任务
.\scripts\service.ps1 install -Bootstrap   # 同上，并首次拉取历史日线（约 1 小时，中断可重跑）
.\scripts\service.ps1 start                # 启用全部任务
.\scripts\service.ps1 stop                 # 停掉运行中实例，再禁用全部任务（数据保留）
.\scripts\service.ps1 remove               # 删除全部任务（数据库、日志、engine/ 保留）
.\scripts\service.ps1 status               # 任务状态、上次结果、下次运行时间
```

- `stop` 之后可随时 `start` 恢复；`remove` 后需 `install` 重新注册
- 部署脚本按上表配置写入，重装或迁移机器时以它为准（底层仍是 `install_tasks.ps1` / `uninstall_tasks.ps1`）
- 注意 `RSIQS_boot_catchup` 在开机时补跑结算与复盘，只删其它任务不删它会继续执行，用 `remove` 整组删除

非交易日由代码自行跳过；同一任务同一时刻只有一个进程在跑（`logs/locks/*.lock` 跨进程互斥），
所以开机补跑与定时任务重叠不会互相干扰。

## 手动入口

所有入口都是幂等的，重复执行安全：

```powershell
uv run python -m jobs.bootstrap        # 一次性初始化（可重复）
uv run python -m jobs.settle           # 收盘结算 + 多日补跑
uv run python -m jobs.review           # 复盘 + 日报
uv run python -m jobs.premarket_scan   # 盘前扫描 + 深度分析
uv run python -m jobs.premarket_final  # 竞价定稿 + 推送
uv run python -m jobs.monitor          # 盘中监控（前台常驻，Ctrl+C 结束）
uv run python -m jobs.research         # 研究循环（耗时较长）
uv run python -m jobs.doctor           # 系统体检（每日 07:30 由 RSIQS_doctor 触发，也可手动跑）
```

`jobs.doctor` 会检查近期失败任务、锚定日缺失 K 线（自动增量回填）、指数缺口、数据源限流日志、
研究空转、`engine/` 完整性，未修复的问题交给 LLM 出根因假设与修复建议后推送到企业微信。

## 目录结构

```
agents/      LLM 调用与提示词编排（盘前、复盘、研究、体检）
data/        akshare 客户端与本地 K 线缓存
engine/      锁定区：撮合、账本、日历、指标、回测、评估器、完整性校验
strategies/  量化策略（现任 + 候选）
jobs/        各时间点入口脚本（计划任务调用）
notify/      企业微信推送与消息格式
playbook/    盘前/复盘 system prompt 与 active/candidate 配置
scripts/     冒烟脚本、服务控制（service.ps1）与计划任务安装/卸载
tests/       pytest 用例（全部离线）
docs/plans/  设计文档与实施计划
```

## 数据与产出位置

| 路径 | 内容 |
|---|---|
| `data/rsiqs.sqlite` | 账户、持仓、成交、净值、推荐、任务状态、LLM 记录 |
| `data/em_cooldown.json` | 东财熔断状态（跨任务共享的冷却期，可删；删除后下次失败会重新计时） |
| `data/cache/` | 前复权/不复权日线、指数、分红、股票名称（可删，重跑 `bootstrap` 重建） |
| `logs/jobs.log` | 所有任务、LLM、推送的日志 |
| `logs/locks/` | 任务互斥锁文件 |
| `playbook/active.json`、`playbook/candidate.json` | 现任/候选 playbook |
| `.integrity/manifest.json` | `engine/` 哈希清单 |

## 模拟账户

| 账户 | 含义 |
|---|---|
| `q:<现任策略>`（默认 `q:baseline_momo`） | 纯量化影子账户，用于回答“LLM 层是否增值” |
| `h:active` | 量化 + LLM 的实盘模拟账户，买入成交与盘中止损止盈提醒只推它 |
| `h:candidate` | 候选 playbook 的对照账户 |
| `bench:csi300` | 沪深 300 基准 |

## 排障

```powershell
Get-Content logs\jobs.log -Tail 50              # 先看日志
Get-ScheduledTask | Where-Object TaskName -Like 'RSIQS_*'   # 看任务状态与下次运行时间
uv run python -m jobs.doctor                    # 跑一次体检
uv run python -c "from pathlib import Path; from engine.integrity import verify; print(verify(Path('.')))"
```

常见现象：

- **群里没收到消息**：`WECOM_WEBHOOK_URL` 为空或填错；用上面的一行连通测试确认。
- **任务没执行**：任务以 SYSTEM 身份运行、不依赖登录状态；用上面的查询命令确认任务存在且 `schtasks /run /tn RSIQS_settle` 手动触发一次，再看日志。
- **`engine/` 完整性告警**：`engine/` 是锁定区，改动后 `research` 会空转（exit 0 但无产出）。确认改动无误后人工重写清单并提交。
- **日志出现东财限流、切换新浪**：所有东财请求已按 1 秒间隔串行并带抖动，连续 3 次失败会写入 `data/em_cooldown.json` 冷却 30 分钟（各任务共享）；冷却期内日线走新浪、个股报价走腾讯。不要连续手动重跑盘前任务。
- **启动即报 `LLM_BASE_URL` 为空**：`.env` 未填或系统环境变量把它覆盖成了空值。

## 文档

- [系统设计](docs/plans/2026-10-16-rsiqs-design.md)：目标与边界、每日时间线、撮合与费用规则、自我提升循环
- [实施计划](docs/plans/2026-10-16-rsiqs-implementation.md)：逐 Task 的实现与测试；末尾 Task 6.8 是首次运行清单

注意：设计文档中“以子进程调用 Claude Code（`claude.exe -p --bare`）”一段已过时，
当前实现是直连 OpenAI 兼容接口（见 `agents/llm.py` 与 `.env.example`）。

## 免责声明

本项目用于研究与自我验证，不构成投资建议；模拟撮合无法复现实盘的滑点、冲击成本与成交概率。
