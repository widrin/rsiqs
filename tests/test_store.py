from datetime import date

import pandas as pd
import pytest

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
    store.save_account(acc)
    got = store.load_account("q:baseline_momo")
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

def test_llm_usage_month_cost(store):
    store.add_llm_usage("premarket:active", 1000, 200, 1.5)
    store.add_llm_usage("review", 1000, 200, 2.0)
    today = date.today()
    assert store.month_llm_cost(today.year, today.month) == pytest.approx(3.5)
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


def test_store_shared_across_threads(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda i: store.add_llm_usage(f"p{i}", 1, 1, 1.0), range(50)))
    today = date.today()
    assert store.month_llm_cost(today.year, today.month) == pytest.approx(50.0)


def test_file_db_persists(tmp_path):
    p = tmp_path / "sub" / "x.sqlite"
    Store(p).kv_set("a", 1)
    assert Store(p).kv_get("a") == 1
