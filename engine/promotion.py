"""量化策略晋升/降级：影子盘 >= 20 日且显著跑赢现任 -> 晋升；现任近 40 日落后影子 >= 5% -> 降级。"""
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
BENCH_ACCOUNT = "bench:csi300"


@dataclass
class Event:
    kind: str  # "promote" | "demote" | "retire"
    strategy: str
    detail: str


def compare(base_nav: pd.Series, cand_nav: pd.Series) -> tuple[float, float]:
    b, c = base_nav.pct_change().dropna().align(cand_nav.pct_change().dropna(), join="inner")
    ex = c - b
    if len(ex) < 2:
        return 0.0, 0.0
    ann = float(ex.mean() * 252)
    std = float(ex.std(ddof=1))
    t = float(ex.mean()) / (std / math.sqrt(len(ex))) if std > 0 else 0.0  # 恒定超额 -> 极大 t，合理
    return ann, t


def _shadow_nav(store: Store, s: str, today: date, calendar: TradingCalendar) -> pd.Series:
    since = store.status_since(s)
    nav = store.nav_series(f"q:{s}")
    days = pd.DatetimeIndex([pd.Timestamp(d) for d in calendar.between(since, today)])
    return nav[nav.index.isin(days)]


def decide(store: Store, today: date, calendar: TradingCalendar) -> list[Event]:
    incs = store.strategies_with_status("incumbent")
    inc = incs[0] if incs else None
    base = store.nav_series(f"q:{inc}" if inc else BENCH_ACCOUNT)
    base = base[base.index <= pd.Timestamp(today)]
    shadows = {s: _shadow_nav(store, s, today, calendar) for s in store.strategies_with_status("shadow")}
    shadows = {s: nav for s, nav in shadows.items() if len(nav) >= MIN_SHADOW_DAYS}

    # 1) 晋升：影子期内年化超额 >= 5% 且 t >= 1.5，取超额最大者
    best: tuple[float, float, str] | None = None
    for s, nav in shadows.items():
        ann, t = compare(base, nav)
        if ann >= PROMOTE_EXCESS_ANN and t >= PROMOTE_T and (best is None or ann > best[0]):
            best = (ann, t, s)
    if best:
        ann, t, s = best
        events = [Event("promote", s, f"影子 {len(shadows[s])} 日，年化超额 {ann:.2%}，t={t:.2f}")]
        for old in incs:
            store.set_status(old, "retired", today)
            events.append(Event("retire", old, f"被 {s} 取代"))
        store.set_status(s, "incumbent", today)
        return events

    # 2) 降级：影子已满 DEMOTE_WINDOW 日，且现任在这一窗口内相对该影子年化超额 <= -5%
    if inc:
        worst: tuple[float, str] | None = None
        for s, nav in shadows.items():
            if len(nav) < DEMOTE_WINDOW:
                continue
            ann, _ = compare(nav.iloc[-DEMOTE_WINDOW:], base.iloc[-DEMOTE_WINDOW:])
            if ann <= DEMOTE_EXCESS_ANN and (worst is None or ann < worst[0]):
                worst = (ann, s)
        if worst:
            ann, s = worst
            store.set_status(inc, "shadow", today)
            store.set_status(s, "incumbent", today)
            return [Event("demote", inc, f"近 {DEMOTE_WINDOW} 日落后 {s} 年化 {-ann:.2%}，降为影子"),
                    Event("promote", s, f"接替被降级的 {inc}")]
    return []
