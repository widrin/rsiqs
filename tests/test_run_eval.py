# tests/test_run_eval.py
import io
import json
import math
import statistics
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import engine.run_eval as run_eval
from data.cache import BarCache
from engine.store import Store

DATES = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])


class FakeClient:
    def daily_bars(self, code, start, end):
        return pd.DataFrame({"date": DATES, "open": [10.0, 10.1, 10.2], "high": [10.5] * 3,
                             "low": [9.9] * 3, "close": [10.1, 10.2, 10.3],
                             "volume": [1e5] * 3, "amount": [2e8] * 3})


class FakeReport:
    def to_agent_dict(self):
        return {"strategy": "baseline_momo", "train": {"sharpe": 1.1}, "val": {"sharpe": 0.9},
                "gate_pass": True, "holdout_pass": False}


@pytest.fixture
def captured(tmp_path, monkeypatch):
    db = tmp_path / "data" / "t.sqlite"
    cache = BarCache(db, FakeClient())
    cache.update(["600000"], date(2026, 1, 7))
    cache.set_names({"600000": "浦发银行"})
    cache.save_index("sh000300", pd.Series([4000.0, 4400.0, 3960.0], index=DATES))
    monkeypatch.setattr(run_eval, "get_settings",
                        lambda: SimpleNamespace(db_path="data/t.sqlite", root=tmp_path))
    sentinel = object()
    monkeypatch.setattr(run_eval, "load_strategy", lambda name: sentinel)
    seen = {}

    def fake_evaluate(strategy, name, panel, names, calendar, benchmark_nav, store, params=None):
        seen.update(strategy=strategy, name=name, panel=panel, names=names, calendar=calendar,
                    bench=benchmark_nav, store=store)
        return FakeReport()

    monkeypatch.setattr(run_eval, "evaluate", fake_evaluate)
    seen["sentinel"] = sentinel
    seen["store_ref"] = Store(db)
    return seen


def test_main_prints_single_json_line(captured):
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == FakeReport().to_agent_dict()

    assert captured["strategy"] is captured["sentinel"]
    assert captured["name"] == "baseline_momo"
    assert list(captured["panel"]) == ["600000"]
    assert captured["names"] == {"600000": "浦发银行"}
    assert captured["calendar"].last() == date(2026, 1, 7)
    assert captured["bench"].tolist() == pytest.approx([1.0, 1.1, 0.99])
    assert isinstance(captured["store"], Store)


def test_main_reports_error_as_json(captured, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("坏了")

    monkeypatch.setattr(run_eval, "evaluate", boom)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert json.loads(out.getvalue()) == {"error": "RuntimeError: 坏了"}


def test_main_reports_system_exit_as_json(captured, monkeypatch):
    def leave(*a, **k):
        print('{"gate_pass": true, "holdout_pass": true}')  # 伪造的报告行
        raise SystemExit(0)

    monkeypatch.setattr(run_eval, "evaluate", leave)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert json.loads(out.getvalue()) == {"error": "SystemExit: 0"}


def _tamperings(monkeypatch):
    import engine.evaluator as ev
    import engine.rules as rules
    return {
        "gate": lambda: monkeypatch.setitem(ev.GATE, "dsr", -1.0),
        "holdout_gate": lambda: monkeypatch.setitem(ev.HOLDOUT_GATE, "sharpe", -1.0),
        "splits": lambda: monkeypatch.setitem(ev.SPLITS, "holdout", (date(2030, 1, 1), None)),
        "run_backtest": lambda: monkeypatch.setattr(ev, "run_backtest", lambda *a, **k: None),
        "series_mean": lambda: monkeypatch.setattr(pd.Series, "mean", lambda self, *a, **k: 0.01),
        "commission": lambda: monkeypatch.setattr(rules, "COMMISSION_RATE", 0.0),
        "limit_prices": lambda: monkeypatch.setattr(rules, "limit_prices", lambda *a, **k: (99.0, 0.01)),
        "calc_fee": lambda: monkeypatch.setattr(rules, "calc_fee", lambda *a, **k: 0.0),
        "math_erf": lambda: monkeypatch.setattr(math, "erf", lambda x: 1.0),
        "np_sqrt": lambda: monkeypatch.setattr(np, "sqrt", lambda x: x),
        "series_astype": lambda: monkeypatch.setattr(pd.Series, "astype", lambda self, *a, **k: self),
        "inv_cdf": lambda: monkeypatch.setattr(statistics.NormalDist, "inv_cdf", lambda self, p: -10.0),
        "limit_pct": lambda: monkeypatch.setattr(rules, "limit_pct", lambda *a, **k: 0.5),
    }


@pytest.mark.parametrize("what", ["gate", "holdout_gate", "splits", "run_backtest", "series_mean", "commission",
                                  "limit_prices", "calc_fee", "math_erf", "np_sqrt", "series_astype", "inv_cdf",
                                  "limit_pct"])
def test_mutated_rules_void_the_trial(captured, monkeypatch, what):
    tamper_now = _tamperings(monkeypatch)[what]

    def tamper(strategy, name, panel, names, calendar, benchmark_nav, store, params=None):
        store.add_trial(name, {}, 1.0, 1.0, True, True)
        tamper_now()  # 模拟策略代码在评估中改了评估规则
        return FakeReport()

    monkeypatch.setattr(run_eval, "evaluate", tamper)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert "作废" in json.loads(out.getvalue())["error"]
    t = captured["store_ref"].last_trial("baseline_momo")
    assert t["gate_pass"] is False and t["holdout_pass"] is False


def test_lazy_submodule_import_is_not_a_change():
    import importlib
    import sys
    before = run_eval._rules_state()
    sys.modules.pop("numpy.polynomial", None)
    importlib.import_module("numpy.polynomial")  # numpy 子模块惰性导入后会写进 vars(np)
    assert run_eval._rules_state() == before


def test_rules_mutated_while_loading_strategy_void_the_trial(captured, monkeypatch):
    import engine.evaluator as ev

    def load(name):  # 策略模块在 import 时（顶层代码）就改了门槛
        monkeypatch.setitem(ev.GATE, "dsr", -1.0)
        return captured["sentinel"]

    def evaluate(strategy, name, panel, names, calendar, benchmark_nav, store, params=None):
        store.add_trial(name, {}, 1.0, 1.0, True, True)
        return FakeReport()

    monkeypatch.setattr(run_eval, "load_strategy", load)
    monkeypatch.setattr(run_eval, "evaluate", evaluate)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert captured["store_ref"].last_trial("baseline_momo")["gate_pass"] is False


def test_main_usage_error():
    out = io.StringIO()
    assert run_eval.main([], out=out) == 2
    assert "usage" in json.loads(out.getvalue())["error"]


def test_missing_benchmark(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "get_settings",
                        lambda: SimpleNamespace(db_path=str(tmp_path / "empty.sqlite"), root=tmp_path))
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert "sh000300" in json.loads(out.getvalue())["error"]
