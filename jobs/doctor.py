"""每日系统自检（doctor）：体检 + 已知故障自动修复 + 未知异常 LLM 诊断。python -m jobs.doctor

体检项（确定性代码，失败模式可枚举）：
1. 任务失败：jobs 表最近 LOOKBACK_DAYS 天的 error 记录，同一任务失败多次在报告里标注
2. 数据缺口：股票池在锚定日（最近收盘日）缺前复权 K 线的代码 → 自动增量回填，修复不了才告警
3. 指数缺口：基准指数缺锚定日 → 自动补齐
4. 数据源健康：扫描日志尾部 24h 的东财限流/新浪回退告警，只观察不判定（持续全量回退说明该人工降级东财）
5. 研究产出：research 记 ok 但近期没有任何 research LLM 记录 → 空转（exit 0 不代表有产出，真实发生过）
6. 完整性：engine/ 与 .integrity/manifest.json 哈希比对

未修复的异常交给 LLM 出结构化诊断（根因假设 + 修复建议），推 WeCom 供人工处理。
doctor 不自动修改代码：策略层自研有回测 gate 兜底，基础设施改动保持人在环。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from jobs import common
from notify.format import format_alert

LOOKBACK_DAYS = 3       # jobs 表失败回看天数
RESEARCH_STALE_DAYS = 14  # research 每周六跑一次；超过 2 周没成功运行才提示
REPAIR_START_DAYS = 60   # 修复回填的 start_default：只对完全没数据的代码生效，老股票走增量
LOG_TAIL_BYTES = 512 * 1024
MISSING_WARN_FRACTION = 0.02  # 锚定日缺 K 线占比低于此值只提示（退市/CDR 等个别无数据），超过算异常


class Diagnosis(BaseModel):
    """LLM 对系统异常的诊断：只建议，不执行。"""
    summary: str  # 一句话概括最可能的根因
    root_cause: str  # 根因假设与依据
    suggested_fix: str  # 给维护者的具体修复步骤
    severity: Literal["info", "warn", "critical"]


DIAGNOSIS_SYSTEM = """你是 A 股量化模拟交易系统的运维诊断员。系统由若干定时任务组成：盘前扫描/定稿（08:30、09:25）、
盘中监控、收盘结算（settle）、策略自研（research）、系统体检（doctor）。数据源是 akshare（东财为主、新浪回退，
东财有限流熔断），LLM 走火山方舟。下面是自动体检发现的问题清单。

已知系统弱点：东财 push2 限流（反复请求会延长封禁）；新浪缺 CDR（如 689009）数据；engine/ 完整性校验失败会让
research 空转（exit 0 但无产出）；多个计划任务并发写同一个 SQLite（有 30s busy 兜底）。

请结合这些背景输出：最可能的根因假设（写明依据），以及给维护者的具体修复步骤。不要建议系统自动修改代码；
所有改动必须由人执行。"""


def failed_jobs(store, days: int = LOOKBACK_DAYS, today: date | None = None) -> list[str]:
    """最近 days 天的失败任务，按任务聚合："settle × 2（11-02, 11-03）"。"""
    today = today or common.today()
    since = (today - timedelta(days=days)).isoformat()
    rows = store.conn.execute(
        "SELECT job, d FROM jobs WHERE status='error' AND d>=? ORDER BY job, d", (since,)).fetchall()
    agg: dict[str, list[str]] = {}
    for job, d in rows:
        agg.setdefault(job, []).append(d)
    return [f"{job} × {len(ds)}（{', '.join(ds)}）" for job, ds in sorted(agg.items())]


def missing_bars(cache, codes: list[str], anchor: date) -> list[str]:
    """锚定日缺前复权 K 线的代码。停牌股也没有当天 K 线——回填是幂等增量，宁可多查。"""
    have = {r[0] for r in cache.conn.execute(
        "SELECT DISTINCT code FROM bar_daily WHERE date=?", (anchor.isoformat(),))}
    return [c for c in codes if c not in have]


def repair_data(cache, client, missing: list[str], anchor: date) -> tuple[list[str], list[str]]:
    """增量回填补缺口，返回（修复成功, 仍缺）。回填成功但数据源本身没有该股（退市/CDR）会留在"仍缺"。"""
    if not missing:
        return [], []
    cache.update(missing, end=anchor, start_default=anchor - timedelta(days=REPAIR_START_DAYS))
    still = missing_bars(cache, missing, anchor)
    repaired = [c for c in missing if c not in set(still)]
    return repaired, still


def repair_index(cache, client, anchor: date, symbol: str = common.INDEX_SYMBOL) -> bool:
    """基准指数缺锚定日时补齐（save_index 按日期 upsert 全量序列）。返回是否修复过。"""
    if pd.Timestamp(anchor) in cache.load_index(symbol).index:
        return False
    cache.save_index(symbol, client.index_daily(symbol))
    return True


def source_health(log_path: Path, today: date | None = None) -> str | None:
    """日志尾部 24h 的东财限流统计（只观察，不判定）。日志缺失/无相关告警返回 None。"""
    if not log_path.exists():
        return None
    size = log_path.stat().st_size
    with log_path.open("rb") as f:
        f.seek(max(0, size - LOG_TAIL_BYTES))
        text = f.read().decode("utf-8", errors="replace")
    yesterday = ((today or common.today()) - timedelta(days=1)).isoformat()
    limited = fallback = 0
    for line in text.splitlines():
        if len(line) < 10 or line[:10] < yesterday:  # 时间戳前 10 位是 ISO 日期，可直接比较
            continue
        limited += "被临时限流" in line
        fallback += "切换新浪" in line
    if not limited and not fallback:
        return None
    return f"近 24h 东财限流告警 {limited} 次，切换新浪 {fallback} 次"


def research_idle(store, days: int = RESEARCH_STALE_DAYS, today: date | None = None) -> str | None:
    """research 每周六跑一次：距离最近一次 ok 超过 days 天才提示"未运行"；
    运行过但没有任何 research LLM 记录（exit 0 但空转）也提示。"""
    today = today or common.today()
    last_ok = store.conn.execute(
        "SELECT MAX(d) FROM jobs WHERE job='research' AND status='ok'").fetchone()[0]
    if last_ok is None:
        return f"research 从未成功运行过"
    try:
        delta = (today - date.fromisoformat(last_ok)).days
    except (TypeError, ValueError):
        return f"research 最近运行日期无效：{last_ok!r}"
    if delta > days:
        return f"research 距上次成功运行已 {delta} 天（阈值 {days} 天，每周六运行）"
    n = store.conn.execute(
        "SELECT COUNT(*) FROM llm_usage WHERE purpose LIKE 'research%' AND ts>=?",
        (f"{last_ok} 00:00:00",)).fetchone()[0]
    if n == 0:
        return f"research {last_ok} 记 ok 但没有任何 research LLM 记录（空转，检查 engine/ 完整性与日志）"
    return None


def integrity_issues(root: Path) -> list[str]:
    """engine/ 锁定区哈希校验。"""
    from engine import integrity
    if not (Path(root) / integrity.MANIFEST_PATH).exists():
        return [f"完整性清单缺失（{integrity.MANIFEST_PATH}）：先运行 jobs.bootstrap 生成"]
    changed = integrity.verify(root)
    return [f"engine/ 与哈希清单不符：{', '.join(changed)}（确认改动无误后人工重写清单并提交）"] if changed else []


def health_check(settings, store, client, cache, calendar, now: datetime) -> dict:
    """运行全部体检项与修复，返回报告（不推送、不抛业务异常——单项失败记为 issue）。"""
    issues: list[str] = []   # 未修复，需人工处理
    repaired: list[str] = []  # 已自动修复
    notes: list[str] = []    # 观察项
    anchor = common.last_closed_day(now, calendar)
    codes = store.kv_get("universe") or []
    if not codes:
        issues.append("kv 缺少 universe：系统尚未初始化，先运行 jobs.bootstrap")
    else:
        try:
            missing = missing_bars(cache, codes, anchor)
            if missing:
                fixed, still = repair_data(cache, client, missing, anchor)
                if fixed:
                    repaired.append(f"回补 {len(fixed)} 只股票 {anchor} 的 K 线")
                if still:
                    what = (f"{len(still)} 只股票 {anchor} 仍缺 K 线（{', '.join(still[:10])}"
                            f"{' 等' if len(still) > 10 else ''}）")
                    (notes if len(still) <= len(codes) * MISSING_WARN_FRACTION else issues).append(what)
            if repair_index(cache, client, anchor):
                repaired.append(f"补齐基准指数 {common.INDEX_SYMBOL} 至 {anchor}")
        except Exception as e:  # noqa: BLE001 — 数据修复失败不该掩盖其他体检项
            issues.append(f"数据体检/修复失败：{type(e).__name__}: {e}")
    issues.extend(f"任务失败：{m}" for m in failed_jobs(store, today=now.date()))
    idle = research_idle(store, today=now.date())
    if idle:
        issues.append(f"研究产出异常：{idle}")
    issues.extend(integrity_issues(settings.root))
    health = source_health(settings.root / "logs" / "jobs.log", today=now.date())
    if health:
        notes.append(health)
    return {"anchor": anchor, "issues": issues, "repaired": repaired, "notes": notes}


def diagnose(settings, store, issues: list[str]) -> Diagnosis | None:
    """LLM 诊断未修复的问题；失败只降级（告警不带诊断），不让体检任务失败。"""
    try:
        llm = common.make_llm(settings, store)
        user = "体检问题清单：\n" + "\n".join(f"- {i}" for i in issues)
        return llm.parse("doctor", DIAGNOSIS_SYSTEM, user, Diagnosis)
    except Exception as e:  # noqa: BLE001
        common.log.warning("doctor diagnosis failed: %s", e)
        return None


def send_report(settings, store, notifier, report: dict) -> None:
    lines = [f"体检锚定日 {report['anchor']}"]
    lines.extend(f"已自动修复：{m}" for m in report["repaired"])
    lines.extend(report["notes"])
    issues = report["issues"]
    if not issues:
        notifier.send_text("【RSIQS 体检】正常\n" + "\n".join(lines))
        return
    lines.extend(f"待处理：{i}" for i in issues)
    diag = diagnose(settings, store, issues)
    if diag:
        lines.append(f"诊断（{diag.severity}）：{diag.summary}\n根因假设：{diag.root_cause}\n建议：{diag.suggested_fix}")
    notifier.send_text(format_alert("系统体检发现问题", "\n".join(lines)))


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()

    def job() -> None:
        report = health_check(settings, store, client, cache, calendar, now)
        n = notifier.replay_failed()  # 补齐代理故障期间落盘的历史告警
        if n:
            report["repaired"].append(f"补发历史失败告警 {n} 条")
        send_report(settings, store, notifier, report)

    return common.run_job("doctor", now.date(), job, store, notifier)


if __name__ == "__main__":
    main()
