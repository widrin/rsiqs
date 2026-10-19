"""16:05 盘后复盘：对比两套 LLM 账户的推荐与实际，经验只写入 candidate playbook，并推送日报。"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from agents import playbook
from agents.llm import LLM, BudgetExceeded
from config import Settings
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.store import Store
from notify.format import format_daily_report

log = logging.getLogger(__name__)

LLM_ACCOUNTS = ("h:active", "h:candidate")
MAX_LESSONS = 3
BAR_LOOKBACK_DAYS = 10
PLAYBOOK_FILES = ["playbook/active.json", "playbook/candidate.json"]
REVIEW_TIMEOUT = 300.0  # claude.exe 子进程超时（秒）；复盘没有硬性截止时间
REVIEW_BUDGET_USD = 1.0  # 单次复盘调用的 --max-budget-usd


class Lesson(BaseModel):
    action: Literal["add", "remove", "merge"]
    rule_id: str  # add 时为 ""
    text: str
    evidence: str


class ReviewOutput(BaseModel):
    summary: str
    lessons: list[Lesson]


def _account_section(store: Store, d: dt.date, name: str) -> tuple[list[str], set[str]]:
    rec = store.load_recommendation(d, name)
    acc = store.load_account(name)
    fills = [t for t in acc.trades if t.date == d] if acc else []
    positions = list(acc.positions.values()) if acc else []
    codes = {p.code for p in rec[0]} if rec else set()
    codes |= {t.code for t in fills} | {p.code for p in positions}
    lines = [f"## 账户 {name}"]
    if rec is None:
        lines.append("推荐：（当日无推荐）")
    else:
        picks, meta = rec
        tag = "经 LLM 审核" if meta.get("llm_reviewed") else "未经 LLM 审核，退回量化"
        lines.append(f"推荐（{tag}）：" + ("" if picks else "经审核后不推荐"))
        lines += [f"- {p.code} {p.name} 买≤{p.entry_max:.2f} 止损 {p.stop:.2f} 止盈 {p.target:.2f}"
                  f" 持有 {p.hold_days} 日｜{p.reason}" for p in picks]
    lines.append("### 当日成交")
    lines += [f"- {t.side} {t.code} {t.qty} 股 @ {t.price:.2f}，费用 {t.fee:.2f}，{t.reason or '开盘成交'}"
              for t in fills] or ["（无）"]
    lines.append("### 收盘持仓")
    lines += [f"- {p.code} {p.name} {p.qty} 股，成本 {p.cost:.2f}，止损 {p.stop:.2f}，止盈 {p.target:.2f}，"
              f"到期 {p.exit_date.isoformat()}" for p in positions] or ["（空仓）"]
    return lines, codes


def _bar_lines(cache: BarCache, codes: set[str], d: dt.date) -> list[str]:
    panel, names = cache.load_panel(sorted(codes), d - dt.timedelta(days=BAR_LOOKBACK_DAYS), d)
    ts, out = pd.Timestamp(d), ["## 个股当日行情"]
    for code in sorted(codes):
        df = panel.get(code)
        if df is None or ts not in df.index:
            out.append(f"- {code}：当日无行情（可能停牌）")
            continue
        r = df.loc[ts]
        pc = float(r["prev_close"])
        out.append(f"- {code} {names.get(code, '')}：开 {r['open']:.2f}（{r['open'] / pc - 1:+.2%}）"
                   f" 高 {r['high']:.2f} 低 {r['low']:.2f} 收 {r['close']:.2f}（{r['close'] / pc - 1:+.2%}）"
                   f" 成交额 {r['amount'] / 1e8:.2f} 亿")
    return out


def _effective_changes(old: dict, new: dict) -> tuple[int, int]:
    """(实际生效的改动条数, 只补充了证据的条数)。改动指新增、删除、改写 text，
    不计指向不存在规则的 remove/merge。"""
    a = {r["id"]: r for r in old.get("rules", [])}
    b = {r["id"]: r for r in new.get("rules", [])}
    same = a.keys() & b.keys()
    changed = len(a.keys() ^ b.keys()) + sum(a[k]["text"] != b[k]["text"] for k in same)
    evidence = sum(a[k]["text"] == b[k]["text"] and a[k]["evidence"] != b[k]["evidence"] for k in same)
    return changed, evidence


def _report_rows(store: Store, d: dt.date) -> list[dict]:
    rows = []
    for name in store.list_accounts():
        if name.startswith("q:") and store.get_status(name[2:]) == "retired":
            continue  # 已退役的量化账户不再结算，不列进日报
        nav = store.nav_series(name)
        if nav.empty:
            continue
        nav = nav.loc[: pd.Timestamp(d)]
        if nav.empty:
            continue
        equity = float(nav.iloc[-1])
        today = nav.index[-1] == pd.Timestamp(d)
        day_ret = equity / float(nav.iloc[-2]) - 1.0 if len(nav) >= 2 and today else 0.0
        label = name if today else f"{name}（今日未结算）"  # 选股失败当天跳过结算：不把前一天的涨跌当成今天的
        rows.append({"account": label, "equity": equity, "day_ret": day_ret,
                     "total_ret": equity / float(nav.iloc[0]) - 1.0})
    return rows


def _build_prompt(d: dt.date, pb: dict, sections: list[str], bars: list[str]) -> str:
    return "\n".join([f"# 复盘日 {d.isoformat()}", "",
                      f"## 候选版经验规则（playbook v{pb.get('version', 1)}，你的 lesson 只作用于这一版）",
                      playbook.render(pb), "", *sections, "", *bars, "",
                      f"请写当日复盘 summary，并给出不超过 {MAX_LESSONS} 条带证据的 lesson（可以为空）。"])


BACKFILL_SUMMARY = "回补的日报：这一天的结算延迟到之后才完成。LLM 复盘、playbook 更新与发布只针对最近一个交易日，这一天不做。"


def run_review(d: dt.date, settings: Settings, store: Store, llm: LLM, cache: BarCache, notifier,
               calendar: TradingCalendar, backfill: bool = False) -> None:
    """backfill=True：回补较早的日子，只补发日报（各账户当天的净值），不调用 LLM、不写 lesson、不判断发布。
    回补期间写的规则没有前向模拟过，发布判断和 lesson 都只在最近一个交易日做，顺序与平时相同。"""
    if backfill:
        playbook.reset_window_if_degraded(store, d)  # 这一天盘前按空规则运行时，发布窗口从这一天重新开始
        notifier.send_markdown(format_daily_report(d, _report_rows(store, d), BACKFILL_SUMMARY))
        return
    sections: list[str] = []
    codes: set[str] = set()
    has_rec = False
    for name in LLM_ACCOUNTS:
        lines, c = _account_section(store, d, name)
        sections += lines
        codes |= c
        has_rec = has_rec or store.load_recommendation(d, name) is not None

    # playbook 文件损坏时不发布、不写 lesson：load 会退回空规则，写回去就把规则丢了
    bad = playbook.corrupt_kinds()
    # 先按窗口内实际跑过的候选版决定是否发布，再写当天的 lesson：当天新写的规则没有前向模拟过，不能随这次发布上线
    release = playbook.maybe_release(store, d, calendar)  # 有损坏时不发布，只把窗口起点移到今天
    parts = [release] if release else []
    if not has_rec and not codes:
        summary = "今日两个 LLM 账户均无推荐、无持仓，未调用 LLM 复盘。"
    else:
        cand = playbook.load("candidate")
        try:
            out = llm.parse(purpose="review", system=playbook.load_prompt("review"),
                            user=_build_prompt(d, cand, sections, _bar_lines(cache, codes, d)),
                            schema=ReviewOutput, effort="medium", timeout=REVIEW_TIMEOUT,
                            max_budget_usd=REVIEW_BUDGET_USD, essential=False)
        except BudgetExceeded:
            summary = "本月 LLM 预算已用完，今日未做 LLM 复盘。"
        except Exception as e:  # noqa: BLE001 - LLMError（CLI 报错/拒答/无结构化输出）、超时等：日报照发
            log.warning("review LLM failed: %r", e)
            summary = f"LLM 复盘失败（{type(e).__name__}），今日 playbook 未更新。"
        else:
            summary = out.summary
            lessons = [les for les in out.lessons[:MAX_LESSONS] if les.evidence.strip()]
            new = playbook.apply_lessons(cand, lessons, d)
            if new != cand and not bad:
                playbook.save("candidate", new)
                changed, evidence = _effective_changes(cand, new)
                parts += [f"复盘更新 {changed} 条"] if changed else []
                parts += [f"补充证据 {evidence} 条"] if evidence else []
                parts += [] if changed or evidence else ["复盘整理规则"]

    if parts and playbook.git_commit(PLAYBOOK_FILES, f"playbook: {d.isoformat()} " + "；".join(parts)) is False:
        summary = f"{summary}\n注意：playbook 的 git 提交失败（见日志），请人工提交 playbook/。"
    if release:
        summary = f"{summary}\n{release}"
    if bad:
        files = "、".join(f"playbook/{k}.json" for k in bad)
        summary = f"{summary}\n注意：{files} 损坏，今日未发布、未更新 playbook，请手工修复（可从 git 历史恢复）。"
    notifier.send_markdown(format_daily_report(d, _report_rows(store, d), summary))
