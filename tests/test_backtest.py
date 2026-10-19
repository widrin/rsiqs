from datetime import date

import pandas as pd
import pytest

from engine.backtest import BacktestResult, bars_for_day, run_backtest
from engine.calendar import TradingCalendar
from engine.dataview import LookaheadError
from engine.types import Pick

IDX = pd.bdate_range("2026-01-05", periods=30)
DAYS = [ts.date() for ts in IDX]


def _frame(closes, opens=None):
    opens = opens or closes
    return pd.DataFrame({"open": opens, "high": [max(o, c) for o, c in zip(opens, closes)],
                         "low": [min(o, c) for o, c in zip(opens, closes)], "close": closes,
                         "volume": [1e6] * len(closes), "amount": [1e8] * len(closes),
                         "prev_close": [float("nan")] + closes[:-1]}, index=IDX)


def _panel():
    up = [10.0 * 1.01 ** i for i in range(30)]
    flat = [20.0] * 30
    down = [30.0 * 0.995 ** i for i in range(30)]
    return {"600001": _frame(up), "600002": _frame(flat), "000003": _frame(down)}


NAMES = {"600001": "上涨股", "600002": "横盘股", "000003": "下跌股"}
CAL = TradingCalendar(DAYS)
BENCH = pd.Series(100.0, index=IDX)


class Recorder:
    """记录每次 select 收到的 asof，并始终买最近涨幅最大的股票。"""
    name = "recorder"

    def __init__(self):
        self.asofs = []

    def select(self, view):
        self.asofs.append(view.asof)
        scored = []
        for code in view.codes():
            h = view.history(code, 2)
            if len(h) == 2:
                last = float(h["close"].iloc[-1])
                scored.append(Pick(code, view.name(code), float(h["close"].pct_change().iloc[-1]),
                                   last * 1.05, last * 0.9, last * 1.5, 3))
        return sorted(scored, key=lambda p: p.score, reverse=True)


class Peeker:
    name = "peeker"

    def select(self, view):
        view.bar_on("600001", DAYS[-1])  # 偷看未来
        return []


def test_bars_for_day():
    bars = bars_for_day(_panel(), NAMES, DAYS[1])
    assert set(bars) == {"600001", "600002", "000003"}
    b = bars["600001"]
    assert b.date == DAYS[1] and b.name == "上涨股"
    assert b.prev_close == pytest.approx(10.0) and b.close == pytest.approx(10.1)
    assert bars_for_day(_panel(), NAMES, DAYS[0]) == {}  # 首日无昨收
    assert bars_for_day(_panel(), NAMES, date(2030, 1, 1)) == {}


def test_picks_use_prev_day_asof():
    s = Recorder()
    run_backtest(s, _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH)
    assert s.asofs == [CAL.prev_day(t) for t in CAL.between(DAYS[2], DAYS[10])]


def test_peeking_strategy_raises():
    with pytest.raises(LookaheadError):
        run_backtest(Peeker(), _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH)


def test_backtest_result_shape_and_trading():
    res = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], BENCH, capital=100_000, max_picks=1)
    assert isinstance(res, BacktestResult)
    assert list(res.nav.index) == [pd.Timestamp(d) for d in CAL.between(DAYS[2], DAYS[20])]
    assert res.nav.iloc[0] < 100_000  # 首日买入扣了费用和滑点
    buys = [f for f in res.fills if f.side == "BUY"]
    assert buys and all(f.code == "600001" for f in buys)
    assert any(f.side == "SELL" and f.reason == "到期" for f in res.fills)
    assert res.metrics["n_days"] == len(res.nav)
    assert res.metrics["total_return"] > 0  # 买的是上涨股


def test_benchmark_with_date_index_is_normalized():
    a = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=IDX, dtype=float))
    b = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=DAYS, dtype=float))
    assert a.metrics["excess_ann"] != 0.0
    assert a.metrics == b.metrics


def test_picks_beyond_calendar_end_dropped():
    res = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[-2], DAYS[-1], BENCH)
    assert res.fills == []
