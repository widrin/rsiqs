import pandas as pd
import pytest

from engine import promotion as pr
from engine.calendar import TradingCalendar
from engine.store import Store

IDX = pd.bdate_range("2026-01-05", periods=60)
DAYS = [ts.date() for ts in IDX]
CAL = TradingCalendar(DAYS)


def _nav(daily, noise=0.002):
    rets = [daily + (noise if i % 2 else -noise) for i in range(len(DAYS))]
    vals, v = [], 100.0
    for r in rets:
        v *= 1 + r
        vals.append(v)
    return vals


def _record(store, account, vals, days=DAYS):
    for d, v in zip(days, vals):
        store.record_nav(account, d, v)


def _setup(cand_daily, shadow_from=0, inc_daily=0.0):
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status("new", "shadow", DAYS[shadow_from])
    _record(store, "q:old", _nav(inc_daily, noise=0.003))
    _record(store, "q:new", _nav(cand_daily, noise=0.001))
    return store


def test_compare():
    base = pd.Series(_nav(0.0), index=IDX)
    cand = pd.Series(_nav(0.001), index=IDX)
    ann, t = pr.compare(base, cand)
    assert ann > 0.2 and t > 3
    assert pr.compare(base, base) == (0.0, 0.0)


def test_promote_after_20_days_retires_old():
    store = _setup(cand_daily=0.002, shadow_from=30)
    events = pr.decide(store, DAYS[-1], CAL)
    assert [e.kind for e in events] == ["promote", "retire"]
    assert events[0].strategy == "new" and events[1].strategy == "old"
    assert store.get_status("new") == "incumbent"
    assert store.get_status("old") == "retired"
    assert store.status_since("new") == DAYS[-1]


def test_no_promotion_with_fewer_than_20_days():
    store = _setup(cand_daily=0.002, shadow_from=45)  # 15 个交易日
    assert pr.decide(store, DAYS[-1], CAL) == []
    assert store.get_status("new") == "shadow" and store.get_status("old") == "incumbent"


def test_no_promotion_when_excess_weak():
    store = _setup(cand_daily=0.00005, shadow_from=0)
    assert pr.decide(store, DAYS[-1], CAL) == []


def test_at_most_one_promotion():
    store = _setup(cand_daily=0.002)
    store.set_status("new2", "shadow", DAYS[0])
    _record(store, "q:new2", _nav(0.003, noise=0.001))
    events = pr.decide(store, DAYS[-1], CAL)
    assert [e.strategy for e in events if e.kind == "promote"] == ["new2"]
    assert store.get_status("new") == "shadow"


def test_demotion_when_incumbent_lags(monkeypatch):
    # 晋升门槛设为不可达，单独验证降级规则
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = _setup(cand_daily=0.0005, inc_daily=0.0)
    events = pr.decide(store, DAYS[-1], CAL)
    assert [(e.kind, e.strategy) for e in events] == [("demote", "old"), ("promote", "new")]
    assert store.get_status("old") == "shadow" and store.get_status("new") == "incumbent"


def test_no_demotion_before_full_window(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = _setup(cand_daily=0.0005, shadow_from=30)  # 影子 30 日 < DEMOTE_WINDOW
    assert pr.decide(store, DAYS[-1], CAL) == []
    assert store.get_status("old") == "incumbent"


def test_no_incumbent_compares_to_benchmark():
    store = Store(":memory:")
    store.set_status("new", "shadow", DAYS[0])
    _record(store, "bench:csi300", _nav(0.0, noise=0.003))
    _record(store, "q:new", _nav(0.002, noise=0.001))
    events = pr.decide(store, DAYS[-1], CAL)
    assert [(e.kind, e.strategy) for e in events] == [("promote", "new")]
