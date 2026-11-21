---
name: rsiqs-research-round
description: 发起并监控 RSIQS 研究会话，轮询评估进度并汇总 gate 结果。当用户要求跑一轮策略研究、验证行业轮动或新因子方向、或查看研究评估进度时使用。不用于修改策略代码或修复回测引擎。
---

# RSIQS 研究会话

## 前置事实

- 研究代理入口：`agents/research.py` 的 `run_research(settings, store, notifier, llm=..., focus=...)`。
- `jobs/research.py` 有当日幂等（job 表当日 ok 就跳过），所以**不要**用 `python -m jobs.research` 手动跑，用本 skill 的脚本。
- 每会话最多评估 `MAX_EVALS_PER_SESSION`（当前 5）个策略；单次评估 10–25 分钟（`min_rows=60` 时）。
- 股票池 800 只，基准 `sh000300`，现任策略 `vol_exit_fixedhold_2611`。
- 行业数据：`view.industry(code)` 返回申万一级行业名，无映射返回「其他」。

## 发起一轮

1. 把本轮 focus 写进 `_focus.txt`（PowerShell 引号容易出错，用文件传递）。
2. 后台执行：

```powershell
uv run python .trae/skills/rsiqs-research-round/scripts/run_round.py _focus.txt
```

3. focus 必须写清四件事：
   - 基线策略名 + 关键参数（阈值、名额、持有期、出场）
   - 已证伪方向（避免 LLM 重复试错）
   - 本轮唯一改动（可加一个仅阈值不同的对照组）
   - 目标指标（copy gate 门槛）

## 监控

```powershell
# 评估进度（每 5–10 分钟）
uv run python -c "import sqlite3; c=sqlite3.connect('data/rsiqs.sqlite'); print(c.execute('SELECT id,ts,strategy,train_sharpe,val_sharpe FROM trials ORDER BY ts DESC LIMIT 5').fetchall())"

# 本轮新草稿
Get-ChildItem strategies/*.py | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-30) } | Select-Object Name,LastWriteTime

# 进程
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id,CPU,@{N='Mem(MB)';E={[math]::Round($_.WorkingSet64/1MB,1)}}
```

- `llm_usage.output_tokens` 只有 ~22 说明模型返回了空回复（偶发），直接重跑脚本即可。
- 会话结束后 `run_round.py` 会打印每个策略的 train/val/DSR/gate，无需再单独跑 `engine.run_eval`（单独跑会写入重复 trial）。

## gate 门槛

`train_sharpe >= 0.5`、`train_max_dd >= -0.45`、`val_sharpe >= 0.5`、`val_excess_ann >= 0.05`、`val_max_dd >= -0.25`、`DSR >= max(0.5, 现任 DSR)`。

## 检查生成草稿（必做）

LLM 写出的策略常出现"参数写了但没生效"的错误，评估前先用 Grep 核对：
- 名额：循环里的 break 条件是否用 `daily_picks`，而不是遗留的 `top_n=15`；
- 权重：是否真的 `weight=` 传进 `Pick`；
- 刹车：`dd_stop`/`dd_slow` 是否与 focus 一致；
- 打分：是否与基线一致（LLM 常擅自把"全池排名"改成"行业内排名"）。

发现偏差时，不要重跑会话（浪费 20 分钟和额度），直接以基线文件为源改那两三行，再用 `uv run python -m engine.run_eval <name>` 单独评估。

## 收尾

1. 研究代理成功后会自动 commit（消息形如 `research: <date> 新增候选策略 ...`）。
2. 清理重复 trial（手动跑过 run_eval 才会产生）：

```powershell
uv run python -c "import sqlite3; c=sqlite3.connect('data/rsiqs.sqlite'); print(c.execute('SELECT strategy,COUNT(*) FROM trials GROUP BY strategy HAVING COUNT(*)>1').fetchall())"
```

发现重复时保留 `MIN(id)`，删除其余；随后 `git push`。
3. 删除 `_focus.txt` 与临时脚本。

## 已验证的坑（不要重试）

- `Pick.weight` 语义（engine/broker.py:128）：`weight` 是**占总资金**的比例（`budget = total_capital * weight`），而 `weight=None` 走 `capital_per_pick = total/5`（即每票 20%）。所以「2 只 × weight 0.5」= 100% 单日投入（约 2.5 倍暴露），train_dd 直接恶化到 -84%~-89%。要复现「1 只/日」的稳态暴露，必须用 2 只 × weight **0.1**（10 只 × 10% = 100%）。
- 每日名额从 1 只提到 2 只且 weight=1.0：暴露翻倍，train 夏普 0.48 → 0.18。
- 只缩 weight 不停开仓：回撤几乎不变（-75.5% → -78.7%）。
- 行业打分成 `0.7·rel20 + 0.3·rel60`：train 夏普 0.456 → 0.235。
- 回撤中位数刹车阈值对 0.02 级变动极敏感（0.30 → 0.32 时 train 夏普 0.456 → -0.09），不要在阈值上密集搜索。
- 紧止损 1.0σ 无效；持有期拉到 20 日会降低 train 夏普。
- 对全池 `pd.concat` 会超时，只能用横截面标量（回撤中位数、广度、r20 中位数）。
- 二元牛熊硬闸能压回撤但会踏空，val 超额转负。
- `target_regime = "bull"` 用在行业相对强度结构上会灾难性失效：牛市日超额全负，train/val 夏普 -1.8 ~ -5.0；且该模式下评估更慢，容易撞 30 分钟 EVAL_TIMEOUT 返回 None。
