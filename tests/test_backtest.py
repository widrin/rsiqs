from datetime import date

import pandas as pd
import pytest

from engine.backtest import BacktestResult, DayBars, bars_for_day, run_backtest
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
    # 第一个点是 start 前一交易日的期初资金，首日的建仓成本和涨跌因此计入指标
    assert list(res.nav.index) == [IDX[1]] + [pd.Timestamp(d) for d in CAL.between(DAYS[2], DAYS[20])]
    assert res.nav.iloc[0] == 100_000 and res.nav.iloc[1] < 100_000  # 首日买入扣了费用和滑点
    buys = [f for f in res.fills if f.side == "BUY"]
    assert buys and all(f.code == "600001" for f in buys)
    assert any(f.side == "SELL" and f.reason == "到期" for f in res.fills)
    assert res.metrics["n_days"] == len(CAL.between(DAYS[2], DAYS[20])) == len(res.nav) - 1
    assert res.metrics["total_return"] > 0  # 买的是上涨股


def test_benchmark_with_date_index_is_normalized():
    a = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=IDX, dtype=float))
    b = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=DAYS, dtype=float))
    assert a.metrics["excess_ann"] != 0.0
    assert a.metrics == b.metrics


def test_picks_beyond_calendar_end_dropped():
    res = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[-2], DAYS[-1], BENCH)
    assert res.fills == []


class BuyOnce:
    """第一次被调用时买 600001，持有 hold 个交易日；止损止盈设得很远，只靠到期卖出。"""

    def __init__(self, hold=10):
        self.name, self.hold, self.done = "buy_once", hold, False

    def select(self, view):
        if self.done:
            return []
        self.done = True
        last = float(view.history("600001", 1)["close"].iloc[-1])
        return [Pick("600001", "上涨股", 1.0, last * 1.05, last * 0.5, last * 2.0, self.hold)]


def test_nav_marks_to_market_and_suspended_day_keeps_last_close():
    panel = _panel()
    panel["600001"] = panel["600001"].drop(IDX[6])  # 停牌一天
    res = run_backtest(BuyOnce(), panel, NAMES, CAL, DAYS[2], DAYS[10], BENCH, capital=100_000, max_picks=1)
    assert [f.side for f in res.fills] == ["BUY"]
    nav = res.nav
    assert nav[IDX[1]] == 100_000 > nav[IDX[2]]
    assert nav[IDX[2]] < nav[IDX[3]] < nav[IDX[4]] < nav[IDX[5]]  # 上涨股按市值计价，不是只算现金
    assert nav[IDX[6]] == pytest.approx(nav[IDX[5]])  # 停牌日沿用上一个收盘价，不按成本估值


def test_first_day_loss_counts_in_metrics():
    closes = [10.0] * 3 + [9.2] * 27
    opens = list(closes)
    opens[3] = 10.0  # DAYS[3] 开盘 10.00 买入，收盘 9.20，之后横盘
    panel = {"600001": _frame(closes, opens)}
    res = run_backtest(BuyOnce(hold=20), panel, NAMES, CAL, DAYS[3], DAYS[15], BENCH, capital=100_000, max_picks=1)
    assert [f.side for f in res.fills] == ["BUY"]
    assert res.metrics["total_return"] < -0.07 and res.metrics["max_dd"] < -0.07


@pytest.mark.parametrize("start,end,hold,bought", [
    (DAYS[-5], DAYS[-1], 4, True),   # 到期日正好是日历最后一天
    (DAYS[-5], DAYS[-1], 5, False),  # 越过日历末尾一天：丢弃
    (DAYS[-5], DAYS[-3], 4, True),   # 持有期可以越过回测末端，只要不越过日历
])
def test_hold_period_horizon_boundary(start, end, hold, bought):
    res = run_backtest(BuyOnce(hold), _panel(), NAMES, CAL, start, end, BENCH)
    assert any(f.side == "BUY" for f in res.fills) is bought


@pytest.mark.parametrize("bps", [10.0, 0.0])
def test_budget_per_pick_and_slippage(bps):
    res = run_backtest(BuyOnce(), _panel(), NAMES, CAL, DAYS[2], DAYS[4], BENCH, capital=100_000, max_picks=5,
                       slippage_bps=bps)
    f = res.fills[0]
    assert f.price == pytest.approx(10.0 * 1.01 ** 2 * (1 + bps / 10_000))
    assert 15_000 < f.price * f.qty <= 20_000  # 每只约占资金的 1/max_picks


def test_nan_close_falls_back_to_last_finite_close():
    panel = _panel()
    panel["600001"].loc[IDX[6], "close"] = float("nan")
    res = run_backtest(BuyOnce(), panel, NAMES, CAL, DAYS[2], DAYS[10], BENCH, capital=100_000, max_picks=1)
    assert res.nav.notna().all()
    assert res.nav[IDX[6]] == pytest.approx(res.nav[IDX[5]])


class PanelProbe:
    """测试专用：记下每天策略经 history 能看到的最后一行，以及 asof。"""
    name = "probe"

    def __init__(self):
        self.seen = []

    def select(self, view):
        self.seen.append((view.asof, max(view.history(c).index.max() for c in view.codes())))
        return []


def test_strategy_never_receives_data_after_asof():
    probe = PanelProbe()
    run_backtest(probe, _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH)
    assert probe.seen and all(last == pd.Timestamp(asof) for asof, last in probe.seen)


def test_day_bars_matches_bars_for_day():
    panel = _panel()
    panel["600002"] = panel["600002"].drop(IDX[4])
    for i, (code, df) in enumerate(panel.items()):  # 开高低收、量、额各不相同，列序错了才测得出来
        df = df.copy()
        df["open"] = df["close"] * (1 + 0.003 * (i + 1))
        df["high"] = df[["open", "close"]].max(axis=1) * 1.02
        df["low"] = df[["open", "close"]].min(axis=1) * 0.98
        df["volume"] = range(1000, 1000 + len(df))
        df["amount"] = df["volume"] * 7.0
        panel[code] = df
    index = DayBars(panel, NAMES)
    for d in DAYS + [date(2030, 1, 1)]:
        assert index.get(d) == bars_for_day(panel, NAMES, d)


def test_benchmark_counts_first_day():
    bench = pd.Series(100.0, index=IDX)
    bench[IDX[2]:] = 110.0  # 基准只在 start 当天上涨 10%，之后横盘
    res = run_backtest(BuyOnce(), _panel(), NAMES, CAL, DAYS[2], DAYS[10], bench, capital=100_000, max_picks=1)
    flat = run_backtest(BuyOnce(), _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH, capital=100_000, max_picks=1)
    assert res.metrics["excess_ann"] == pytest.approx(flat.metrics["excess_ann"] - 0.1 / 9 * 252)
