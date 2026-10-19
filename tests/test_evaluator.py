import json
from datetime import date

import pandas as pd

import engine.evaluator as ev
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick

IDX = pd.bdate_range("2026-01-05", periods=120)
DAYS = [ts.date() for ts in IDX]
SPL = {"train": (DAYS[2], DAYS[40]), "val": (DAYS[41], DAYS[80]), "holdout": (DAYS[81], None)}
CAL = TradingCalendar(DAYS)
BENCH = pd.Series(100.0, index=IDX)


def _panel():
    closes = [10.0 * 1.01 ** i for i in range(120)]
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                       "volume": [1e6] * 120, "amount": [1e8] * 120,
                       "prev_close": [float("nan")] + closes[:-1]}, index=IDX)
    return {"600001": df}


NAMES = {"600001": "上涨股"}


class Winner:
    name = "winner"

    def select(self, view):
        last = float(view.history("600001", 1)["close"].iloc[-1])
        return [Pick("600001", "上涨股", 1.0, last * 1.05, last * 0.9, last * 1.5, 3)]


class Idle:
    name = "idle"

    def select(self, view):
        return []


def test_gate_pass_runs_holdout_and_records_trial():
    store = Store(":memory:")
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, {"p": 1}, splits=SPL)
    assert rep.gate_pass is True
    assert rep.holdout_pass is True
    assert rep.n_trials == 1 == store.trial_count()
    t = store.recent_trials(1)[0]
    assert t["strategy"] == "winner" and t["params"] == {"p": 1} and t["holdout_pass"] is True


def test_gate_fail_skips_holdout(monkeypatch):
    store = Store(":memory:")
    calls = []
    real = ev.run_backtest

    def spy(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        calls.append(start)
        return real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)

    monkeypatch.setattr(ev, "run_backtest", spy)
    rep = ev.evaluate(Idle(), "idle", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    assert rep.gate_pass is False and rep.holdout_pass is None
    assert calls == [DAYS[2], DAYS[41]]  # 留出期未运行
    assert store.recent_trials(1)[0]["holdout_pass"] is None


def test_holdout_end_clamped_to_last_data_day(monkeypatch):
    future = [d.date() for d in pd.bdate_range(IDX[-1] + pd.Timedelta(days=1), periods=10)]
    cal = TradingCalendar(DAYS + future)  # 新浪日历含未来日期
    calls = []
    real = ev.run_backtest

    def spy(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        calls.append((start, end))
        return real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)

    monkeypatch.setattr(ev, "run_backtest", spy)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, cal, BENCH, Store(":memory:"), splits=SPL)
    assert rep.gate_pass is True
    assert calls[-1] == (DAYS[81], DAYS[-1])


def test_n_trials_counts_all_strategies():
    store = Store(":memory:")
    store.add_trial("other", {}, 0.0, 0.0, False, None)
    rep = ev.evaluate(Idle(), "idle", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    assert rep.n_trials == 2


def test_to_agent_dict_never_contains_holdout_numbers(monkeypatch):
    store = Store(":memory:")
    real = ev.run_backtest
    sentinel = 123.456

    def tagged(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        res = real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)
        if start == SPL["holdout"][0]:
            res.metrics.update(sharpe=sentinel, excess_ann=sentinel)
        return res

    monkeypatch.setattr(ev, "run_backtest", tagged)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    d = rep.to_agent_dict()
    assert set(d) == {"strategy", "train", "val", "dsr", "gate_pass", "holdout_pass", "n_trials"}
    assert isinstance(d["holdout_pass"], bool)
    assert str(sentinel) not in json.dumps(d)
    assert str(sentinel) not in repr(rep)
