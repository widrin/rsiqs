import json
from datetime import date

import pandas as pd
import pytest

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


GOOD_TRAIN = {"sharpe": 0.5}
GOOD_VAL = {"sharpe": 0.8, "excess_ann": 0.03, "max_dd": -0.25}


def test_gate_boundaries_inclusive_and_each_condition_counts():
    assert ev._passes_gate(GOOD_TRAIN, GOOD_VAL, 0.90) is True
    assert ev._passes_gate(GOOD_TRAIN, GOOD_VAL, 0.90 - 1e-9) is False
    assert ev._passes_gate({"sharpe": 0.5 - 1e-9}, GOOD_VAL, 0.95) is False
    for key in GOOD_VAL:
        assert ev._passes_gate(GOOD_TRAIN, {**GOOD_VAL, key: GOOD_VAL[key] - 1e-9}, 0.95) is False, key
    assert ev._passes_gate(GOOD_TRAIN, {**GOOD_VAL, "sharpe": float("nan")}, 0.95) is False


def test_splits_are_contiguous_and_holdout_starts_2026_07():
    tr, va, ho = ev.SPLITS["train"], ev.SPLITS["val"], ev.SPLITS["holdout"]
    assert tr == (date(2016, 1, 1), date(2024, 12, 31))
    assert va == (date(2025, 1, 1), date(2026, 6, 30))
    assert ho == (date(2026, 7, 1), None)


def _patched(monkeypatch, holdout_metrics=None, holdout_raises=None):
    real = ev.run_backtest

    def fake(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        if start == SPL["holdout"][0]:
            if holdout_raises is not None:
                raise holdout_raises
            res = real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)
            res.metrics.update(holdout_metrics or {})
            return res
        return real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)

    monkeypatch.setattr(ev, "run_backtest", fake)


@pytest.mark.parametrize("over,passed", [
    ({"n_days": 0}, False),
    ({"excess_ann": -1e-9}, False),
    ({"sharpe": 0.5 - 1e-9}, False),
    ({"excess_ann": 0.0, "sharpe": 0.5}, True),  # 边界含等号
])
def test_holdout_gate_conditions(monkeypatch, over, passed):
    _patched(monkeypatch, holdout_metrics=over)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, Store(":memory:"), splits=SPL)
    assert rep.gate_pass is True and rep.holdout_pass is passed


@pytest.mark.parametrize("exc", [RuntimeError("holdout_return=0.4451"), SystemExit(0), KeyError("x")])
def test_holdout_failure_is_a_counted_fail_without_details(monkeypatch, exc):
    _patched(monkeypatch, holdout_raises=exc)
    store = Store(":memory:")
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    assert rep.gate_pass is True and rep.holdout_pass is False
    assert store.trial_count() == 1 and store.last_trial("winner")["holdout_pass"] is False
    assert "0.4451" not in json.dumps(rep.to_agent_dict()) and "0.4451" not in repr(rep)


class Crasher:
    name = "crasher"

    def select(self, view):
        raise ValueError("boom")


def test_failed_train_val_run_still_counts_as_trial():
    store = Store(":memory:")
    with pytest.raises(ValueError, match="boom"):
        ev.evaluate(Crasher(), "crasher", _panel(), NAMES, CAL, BENCH, store, {"p": 2}, splits=SPL)
    t = store.last_trial("crasher")
    assert store.trial_count() == 1
    assert t["gate_pass"] is False and t["holdout_pass"] is None and t["params"] == {"p": 2}


def test_dsr_inputs_and_trial_sharpes(monkeypatch):
    store = Store(":memory:")
    store.add_trial("other", {}, 0.0, 0.0, False, None)
    store.add_trial("other2", {}, 0.0, 0.0, False, None)
    seen, navs = {}, {}
    real_bt, real_dsr = ev.run_backtest, ev.deflated_sharpe

    def bt(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        res = real_bt(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)
        navs[start] = res.nav
        return res

    def dsr(sr, n_trials, n_obs, skew=0.0, kurt=3.0):
        seen.update(sr=sr, n_trials=n_trials, n_obs=n_obs, skew=skew, kurt=kurt)
        return real_dsr(sr, n_trials, n_obs, skew, kurt)

    monkeypatch.setattr(ev, "run_backtest", bt)
    monkeypatch.setattr(ev, "deflated_sharpe", dsr)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    r = pd.concat([navs[SPL["train"][0]].pct_change().dropna(), navs[SPL["val"][0]].pct_change().dropna()])
    assert seen["n_trials"] == 3  # 所有策略的既往试验 + 本次
    assert seen["n_obs"] == len(r)
    assert seen["sr"] == pytest.approx(ev.sharpe(r, periods=1))  # 日频夏普，不是年化
    assert seen["kurt"] == pytest.approx(float(r.kurt()) + 3.0)  # 非超额峰度
    t = store.last_trial("winner")
    assert (t["train_sharpe"], t["val_sharpe"]) == (pytest.approx(rep.train["sharpe"]), pytest.approx(rep.val["sharpe"]))
