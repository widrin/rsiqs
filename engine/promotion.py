"""量化策略晋升/降级：影子盘 >= 20 个收益日且显著跑赢现任 -> 晋升；
现任近 40 个收益日显著落后影子、年化 >= 5% -> 降级。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.calendar import TradingCalendar
from engine.store import Store

MIN_SHADOW_DAYS = 20
PROMOTE_EXCESS_ANN = 0.05
PROMOTE_T = 1.5
DEMOTE_WINDOW = 40
DEMOTE_EXCESS_ANN = -0.05
DEMOTE_T = 1.5  # 降级同样要求显著：否则零超额的噪声也会让现任和影子来回换位
BENCH_ACCOUNT = "bench:csi300"


@dataclass
class Event:
    kind: str  # "promote" | "demote" | "retire"
    strategy: str
    detail: str


def compare(base_nav: pd.Series, cand_nav: pd.Series, window: int | None = None) -> tuple[float, float]:
    """cand 相对 base 的年化超额与 t 值。先按日期对齐净值再算收益：一方缺某天的点时，两边都用跨过
    这个缺口的同一段收益，缺口不会被当成超额。window 只取对齐后最近 window 个收益日。"""
    b, c = base_nav.align(cand_nav, join="inner")
    if window is not None:
        b, c = b.iloc[-(window + 1):], c.iloc[-(window + 1):]
    ex = (c.pct_change() - b.pct_change()).dropna()
    if len(ex) < 2:
        return 0.0, 0.0
    ann = float(ex.mean() * 252)
    std = float(ex.std(ddof=1))
    t = float(ex.mean()) / (std / math.sqrt(len(ex))) if std > 0 else 0.0  # 恒定超额 -> 极大 t，合理
    return ann, t


def _shadow_nav(store: Store, s: str, today: date, calendar: TradingCalendar) -> pd.Series:
    """影子期净值：从进入影子后第一个交易日的前一交易日（结算时记下基线的那一天）到 today。
    研究任务在周六运行时，基线是周五的净值；第 n 个点对应第 n-1 个收益日。"""
    since = store.status_since(s)
    nav = store.nav_series(f"q:{s}")
    first = since if calendar.is_trading_day(since) else calendar.next_day(since)
    try:
        start = calendar.prev_day(first)
    except ValueError:  # 日历第一天
        start = first
    return nav[(nav.index >= pd.Timestamp(start)) & (nav.index <= pd.Timestamp(today))]


def decide(store: Store, today: date, calendar: TradingCalendar) -> list[Event]:
    incs = store.strategies_with_status("incumbent")
    inc = incs[0] if incs else None
    base = store.nav_series(f"q:{inc}" if inc else BENCH_ACCOUNT)
    base = base[base.index <= pd.Timestamp(today)]
    shadows = {s: _shadow_nav(store, s, today, calendar) for s in store.strategies_with_status("shadow")}
    shadows = {s: nav for s, nav in shadows.items() if len(nav) - 1 >= MIN_SHADOW_DAYS}

    # 1) 晋升：影子期内年化超额 >= 5% 且 t >= 1.5，取超额最大者
    best: tuple[float, float, str] | None = None
    for s, nav in shadows.items():
        ann, t = compare(base, nav)
        if ann >= PROMOTE_EXCESS_ANN and t >= PROMOTE_T and (best is None or ann > best[0]):
            best = (ann, t, s)
    if best:
        ann, t, s = best
        events = [Event("promote", s, f"影子 {len(shadows[s]) - 1} 日，年化超额 {ann:.2%}，t={t:.2f}")]
        for old in incs:
            store.set_status(old, "retired", today)
            events.append(Event("retire", old, f"被 {s} 取代"))
        store.set_status(s, "incumbent", today)
        return events

    # 2) 降级：影子已满 DEMOTE_WINDOW 个收益日，且现任在最近这一窗口内相对该影子年化超额 <= -5%、t <= -1.5
    if inc:
        worst: tuple[float, str] | None = None
        for s, nav in shadows.items():
            if len(nav) - 1 < DEMOTE_WINDOW:
                continue
            ann, t = compare(nav, base, window=DEMOTE_WINDOW)
            if ann <= DEMOTE_EXCESS_ANN and t <= -DEMOTE_T and (worst is None or ann < worst[0]):
                worst = (ann, s)
        if worst:
            ann, s = worst
            store.set_status(inc, "shadow", today)
            store.set_status(s, "incumbent", today)
            return [Event("demote", inc, f"近 {DEMOTE_WINDOW} 日落后 {s} 年化 {-ann:.2%}，降为影子"),
                    Event("promote", s, f"接替被降级的 {inc}")]
    return []
