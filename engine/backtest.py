"""逐日回放：t 日用 asof=t-1 的 DataView 选股，用 t 日 bar 撮合。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.broker import SimBroker
from engine.calendar import TradingCalendar
from engine.dataview import DataView
from engine.ledger import Account
from engine.metrics import summarize
from engine.types import Bar, Fill


@dataclass
class BacktestResult:
    nav: pd.Series
    fills: list[Fill]
    metrics: dict


def bars_for_day(panel: dict[str, pd.DataFrame], names: dict[str, str], d: date) -> dict[str, Bar]:
    ts = pd.Timestamp(d)
    out: dict[str, Bar] = {}
    for code, df in panel.items():
        if ts not in df.index:
            continue
        row = df.loc[ts]
        prev = float(row["prev_close"])
        if prev != prev:  # NaN：上市首日无昨收，跳过
            continue
        out[code] = Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]), prev_close=prev,
                        volume=float(row["volume"]), amount=float(row["amount"]), name=names.get(code, ""))
    return out


def run_backtest(strategy, panel, names, calendar: TradingCalendar, start: date, end: date,
                 benchmark_nav: pd.Series, capital: float = 500_000, max_picks: int = 5,
                 slippage_bps: float = 10.0) -> BacktestResult:
    acct = Account(name=f"bt:{getattr(strategy, 'name', 'strategy')}", cash=float(capital))
    broker = SimBroker(slippage_bps=slippage_bps)
    last_close: dict[str, float] = {}
    days = calendar.between(start, end)
    horizon = len(calendar.between(start, calendar.last()))
    navs: list[float] = []
    for i, t in enumerate(days):
        view = DataView(panel, names, asof=calendar.prev_day(t))
        picks = list(strategy.select(view))[:max_picks]
        # 持有期越过日历末尾的 pick 无法确定到期日，丢弃（t 之后还剩 horizon-1-i 个交易日）
        picks = [p for p in picks if p.hold_days <= horizon - 1 - i]
        bars = bars_for_day(panel, names, t)
        broker.execute_day(acct, t, bars, picks, calendar, capital / max_picks)
        last_close.update({c: b.close for c, b in bars.items()})
        navs.append(acct.equity(last_close))
    nav = pd.Series(navs, index=pd.DatetimeIndex([pd.Timestamp(t) for t in days]), dtype=float)
    bench = benchmark_nav.copy()
    bench.index = pd.to_datetime(bench.index)  # 兼容 date 对象索引
    bench = bench.sort_index().loc[pd.Timestamp(start):pd.Timestamp(end)]
    return BacktestResult(nav=nav, fills=list(acct.trades), metrics=summarize(nav, bench))
