"""逐日回放：t 日用 asof=t-1 的 DataView 选股，用 t 日 bar 撮合。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
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


_COLS = ["open", "high", "low", "close", "prev_close", "volume", "amount"]


def bars_for_day(panel: dict[str, pd.DataFrame], names: dict[str, str], d: date) -> dict[str, Bar]:
    """d 日有日线的股票的 Bar。昨收为 NaN 的行跳过（Part 3 的 load_panel 会把首行昨收填成开盘价，
    所以真实数据里一般不会出现；手工构造的面板首行常为 NaN）。"""
    ts = pd.Timestamp(d)
    out: dict[str, Bar] = {}
    for code, df in panel.items():
        if ts not in df.index:
            continue
        row = df.loc[ts]
        prev = float(row["prev_close"])
        if prev != prev:  # NaN：无昨收，跳过
            continue
        out[code] = Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]), prev_close=prev,
                        volume=float(row["volume"]), amount=float(row["amount"]), name=names.get(code, ""))
    return out


def _ns(ts) -> np.ndarray:
    return np.asarray(ts, dtype="datetime64[ns]").view("int64")


class DayBars:
    """回测用的按日索引：一次性把面板转成数组，每天对每只股票只做一次二分查找。结果与 bars_for_day 相同。"""

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str]):
        self._names = names
        self._data = [(code, _ns(df.index.values), df[_COLS].to_numpy(dtype=float)) for code, df in panel.items()]

    def get(self, d: date) -> dict[str, Bar]:
        t = _ns([pd.Timestamp(d).to_datetime64()])[0]
        out: dict[str, Bar] = {}
        for code, keys, cols in self._data:
            i = int(np.searchsorted(keys, t))  # 索引升序
            if i >= len(keys) or keys[i] != t:
                continue
            o, h, lo, c, pc, v, a = (float(x) for x in cols[i])
            if pc != pc:  # NaN：无昨收，跳过
                continue
            out[code] = Bar(code=code, date=d, open=o, high=h, low=lo, close=c, prev_close=pc,
                            volume=v, amount=a, name=self._names.get(code, ""))
        return out


def run_backtest(strategy, panel, names, calendar: TradingCalendar, start: date, end: date,
                 benchmark_nav: pd.Series, capital: float = 500_000, max_picks: int = 5,
                 slippage_bps: float = 10.0) -> BacktestResult:
    """nav 的第一个点是 start 前一交易日的期初资金，首日的建仓成本和涨跌因此计入指标。
    策略拿到的面板截断到 end：本段回测看不到之后（例如留出期）的任何数据。"""
    acct = Account(name=f"bt:{getattr(strategy, 'name', 'strategy')}", cash=float(capital))
    broker = SimBroker(slippage_bps=slippage_bps)
    last_close: dict[str, float] = {}
    days = calendar.between(start, end)
    horizon = len(calendar.between(start, calendar.last()))
    panel = {c: df.loc[:pd.Timestamp(end)] for c, df in panel.items()}
    day_bars = DayBars(panel, names)
    dates = [calendar.prev_day(days[0])] + days if days else []
    navs: list[float] = [float(capital)] if days else []
    for i, t in enumerate(days):
        view = DataView(panel, names, asof=calendar.prev_day(t))
        picks = list(strategy.select(view))[:max_picks]
        # 持有期越过日历末尾的 pick 无法确定到期日，丢弃（t 之后还剩 horizon-1-i 个交易日）
        picks = [p for p in picks if p.hold_days <= horizon - 1 - i]
        bars = day_bars.get(t)
        broker.execute_day(acct, t, bars, picks, calendar, capital / max_picks)
        # 收盘价缺失（NaN）时沿用上一个有效收盘价，净值不会变成 NaN
        last_close.update({c: b.close for c, b in bars.items() if math.isfinite(b.close)})
        navs.append(acct.equity(last_close))
    nav = pd.Series(navs, index=pd.DatetimeIndex([pd.Timestamp(t) for t in dates]), dtype=float)
    bench = benchmark_nav.copy()
    bench.index = pd.to_datetime(bench.index)  # 兼容 date 对象索引
    bench = bench.sort_index().loc[pd.Timestamp(dates[0] if dates else start):pd.Timestamp(end)]
    return BacktestResult(nav=nav, fills=list(acct.trades), metrics=summarize(nav, bench))
