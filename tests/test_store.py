from datetime import date

import pandas as pd
import pytest

import engine.store as store_mod
from engine.ledger import Account
from engine.store import Store
from engine.types import Pick


@pytest.fixture
def store():
    return Store(":memory:")


def _pick(code="600000"):
    return Pick(code=code, name="浦发银行", score=1.5, entry_max=10.3, stop=9.3, target=11.0, hold_days=5, reason="测试")


def test_account_roundtrip(store):
    acc = Account(name="q:baseline_momo", cash=500_000.0)
    acc.buy(date(2026, 1, 5), "600000", "浦发银行", 10.0, 1000, 9.3, 11.0, date(2026, 1, 12))
    acc.last_trade_day = date(2026, 1, 5)
    store.save_account(acc)
    got = store.load_account("q:baseline_momo")
    assert got == acc and got.last_trade_day == date(2026, 1, 5)
    assert got.cash == pytest.approx(acc.cash)
    assert got.positions["600000"].qty == 1000
    assert got.positions["600000"].exit_date == date(2026, 1, 12)
    assert len(got.trades) == 1
    assert store.load_account("nope") is None
    store.save_account(Account(name="h:active", cash=1.0))
    assert store.list_accounts() == ["h:active", "q:baseline_momo"]


def test_nav_series_sorted_and_last_date(store):
    assert store.last_nav_date("a") is None
    assert store.nav_series("a").empty
    store.record_nav("a", date(2026, 1, 6), 101.0)
    store.record_nav("a", date(2026, 1, 5), 100.0)
    store.record_nav("a", date(2026, 1, 6), 102.0)  # 同日覆盖
    s = store.nav_series("a")
    assert isinstance(s.index, pd.DatetimeIndex)
    assert list(s.index) == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
    assert s.dtype == float and s.iloc[-1] == 102.0
    assert store.last_nav_date("a") == date(2026, 1, 6)


def test_recommendation_and_candidates(store):
    d = date(2026, 1, 5)
    assert store.load_recommendation(d, "h:active") is None
    store.save_recommendation(d, "h:active", [_pick()], {"llm_reviewed": True})
    picks, meta = store.load_recommendation(d, "h:active")
    assert picks == [_pick()] and meta == {"llm_reviewed": True}
    store.save_recommendation(d, "h:candidate", [])
    assert store.load_recommendation(d, "h:candidate") == ([], {})
    assert store.load_candidates(d) is None
    store.save_candidates(d, [_pick("000001")], {"news": [{"title": "利好"}]})
    picks, ctx = store.load_candidates(d)
    assert picks[0].code == "000001" and ctx["news"][0]["title"] == "利好"


def test_jobs(store):
    d = date(2026, 1, 5)
    assert not store.job_done("settle", d)
    store.mark_job("settle", d, "error")
    assert not store.job_done("settle", d)
    store.mark_job("settle", d, "ok")
    assert store.job_done("settle", d)
    assert not store.job_done("settle", date(2026, 1, 6))

def test_llm_usage_month_cost(store, monkeypatch):
    stamps = iter(["2026-01-31T23:59:59", "2026-02-01T00:00:00", "2025-01-15T10:00:00"])
    monkeypatch.setattr(store_mod, "_now", lambda: next(stamps))
    store.add_llm_usage("premarket:active", 1000, 200, 1.0)
    store.add_llm_usage("review", 1000, 200, 2.0)
    store.add_llm_usage("research", 1000, 200, 4.0)
    assert store.month_llm_cost(2026, 1) == pytest.approx(1.0)
    assert store.month_llm_cost(2026, 2) == pytest.approx(2.0)
    assert store.month_llm_cost(2025, 1) == pytest.approx(4.0)
    assert store.month_llm_cost(2000, 1) == 0.0


def test_trials(store):
    assert store.trial_count() == 0
    store.add_trial("a", {"k": 1}, 0.6, 0.9, True, None)
    store.add_trial("b", {}, 0.1, 0.2, False, None)
    store.add_trial("a", {"k": 2}, 0.7, 1.0, True, False)
    assert store.trial_count() == 3
    assert store.trial_count("a") == 2
    recent = store.recent_trials(2)
    assert [t["strategy"] for t in recent] == ["a", "b"]
    assert recent[0]["params"] == {"k": 2} and recent[0]["holdout_pass"] is False
    assert recent[1]["holdout_pass"] is None and recent[1]["gate_pass"] is False


def test_status(store):
    assert store.get_status("x") is None and store.status_since("x") is None
    store.set_status("x", "candidate", date(2026, 1, 5))
    store.set_status("x", "shadow", date(2026, 1, 6))
    store.set_status("y", "shadow", date(2026, 1, 7))
    assert store.get_status("x") == "shadow"
    assert store.status_since("x") == date(2026, 1, 6)
    assert store.strategies_with_status("shadow") == ["x", "y"]
    with pytest.raises(ValueError):
        store.set_status("x", "bogus", date(2026, 1, 6))


def test_kv(store):
    assert store.kv_get("k") is None
    assert store.kv_get("k", 5) == 5
    store.kv_set("k", {"a": [1, 2]})
    assert store.kv_get("k") == {"a": [1, 2]}


def test_store_shared_across_threads(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(store_mod, "_now", lambda: "2026-03-10T09:00:00")
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda i: store.add_llm_usage(f"p{i}", 1, 1, 1.0), range(50)))
    assert store.month_llm_cost(2026, 3) == pytest.approx(50.0)


def test_file_db_persists(tmp_path):
    p = tmp_path / "sub" / "x.sqlite"
    Store(p).kv_set("a", 1)
    assert Store(p).kv_get("a") == 1


def test_nav_series_sorted_regardless_of_insert_order(store):
    for d in (date(2026, 1, 8), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 5)):
        store.record_nav("b", d, float(d.day))
    s = store.nav_series("b")
    assert list(s.index) == [pd.Timestamp(f"2026-01-0{i}") for i in (5, 6, 7, 8)]
    assert list(s) == [5.0, 6.0, 7.0, 8.0]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_record_nav_rejects_non_finite(store, bad):
    with pytest.raises(ValueError):
        store.record_nav("a", date(2026, 1, 5), bad)
    assert store.nav_series("a").empty


def test_trial_holdout_update_and_last_trial(store):
    assert store.last_trial("a") is None
    tid = store.add_trial("a", {}, 0.6, None, True, None)  # val 夏普缺失也能入库
    store.add_trial("b", {}, 0.1, 0.2, False, None)
    assert store.last_trial("a")["holdout_pass"] is None and store.last_trial("a")["val_sharpe"] is None
    store.set_trial_holdout(tid, True)
    t = store.last_trial("a")
    assert t["id"] == tid and t["gate_pass"] is True and t["holdout_pass"] is True
    assert store.recent_trials(2)[1]["holdout_pass"] is True


def test_void_trial(store):
    tid = store.add_trial("a", {}, 1.0, 1.2, True, True)
    store.void_trial(tid)
    t = store.last_trial("a")
    assert t["gate_pass"] is False and t["holdout_pass"] is False and store.trial_count() == 1
