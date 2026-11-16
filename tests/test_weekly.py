from datetime import date

import pandas as pd

from agents import weekly
from engine.store import Store
from notify.wecom import NullNotifier


def _dates(n: int = 60) -> list[date]:
    return [ts.date() for ts in pd.bdate_range("2026-06-01", periods=n)]


def _bench(store: Store, dates: list[date], level: float = 100.0) -> None:
    for d in dates:
        store.record_nav("bench:csi300", d, level)


def _record(store: Store, account: str, dates: list[date], rets: list[float]) -> None:
    """按日收益逐日记录净值：dates[0] 记 100，之后每个交易日乘一个收益。"""
    equity = 100.0
    store.record_nav(account, dates[0], equity)
    for d, r in zip(dates[1:], rets):
        equity *= 1.0 + r
        store.record_nav(account, d, equity)


def _regimes(store: Store, dates: list[date], split: int, first: str = "bull", second: str = "bear") -> None:
    for i, d in enumerate(dates):
        store.kv_set(f"regime:{d.isoformat()}", {"state": first if i < split else second})


def _setup(inc_rets: list[float], alt_rets: list[float], split: int = 40) -> tuple[Store, list[date]]:
    store = Store(":memory:")
    dates = _dates(60)
    _bench(store, dates)
    _regimes(store, dates, split)
    _record(store, "q:old_inc", dates, inc_rets)
    _record(store, "q:new_sh", dates, alt_rets)
    store.set_status("old_inc", "incumbent", dates[0])
    store.set_status("new_sh", "shadow", dates[split - 1])
    return store, dates


def _better_when_bear() -> list[float]:
    return [0.0005] * 39 + [(0.004 if i % 2 else 0.001) for i in range(20)]


def _worse_when_bear() -> list[float]:
    return [0.001] * 39 + [(-0.002 if i % 2 else -0.001) for i in range(20)]


def test_recommends_switch_when_alternative_wins_in_current_regime():
    store, dates = _setup(_worse_when_bear(), _better_when_bear())
    data = weekly.collect(store, dates[-1])
    assert data["state"] == "bear" and not data["fallback"]
    assert data["start"] == dates[40] and data["days"] == 20
    assert data["incumbent"] == "old_inc"
    rec = weekly.decide(data)
    assert rec["action"] == "switch" and rec["to"] == "new_sh"
    assert "切换" in rec["text"]


def test_holds_when_incumbent_leads():
    store, dates = _setup(_better_when_bear(), _worse_when_bear())
    rec = weekly.decide(weekly.collect(store, dates[-1]))
    assert rec["action"] == "hold" and rec["to"] == "old_inc"
    assert "维持现任 old_inc" in rec["text"]


def test_falls_back_to_recent_days_when_regime_history_is_short():
    store, dates = _setup(_worse_when_bear(), _better_when_bear(), split=55)  # 当前行情只有 5 个交易日
    data = weekly.collect(store, dates[-1])
    assert data["fallback"] is True and data["start"] == dates[0]
    assert "行情标签不足" in weekly.render(dates[-1], data, weekly.decide(data))


def test_retired_strategy_without_data_in_window_is_excluded():
    store, dates = _setup(_worse_when_bear(), _better_when_bear())
    _record(store, "q:dead_one", dates[:30], [0.001] * 29)  # 当前行情窗口前就已退役
    store.set_status("dead_one", "retired", dates[29])
    data = weekly.collect(store, dates[-1])
    assert [r["strategy"] for r in data["rows"]] == ["new_sh", "old_inc"]


def test_run_weekly_pushes_notice_with_recommendation():
    store, dates = _setup(_worse_when_bear(), _better_when_bear())
    notifier = NullNotifier()
    out = weekly.run_weekly(dates[-1], store, notifier)
    assert out == {"state": "bear", "action": "switch", "to": "new_sh"}
    [msg] = notifier.sent
    assert "每周复盘" in msg and "熊市" in msg and "new_sh" in msg and "建议" in msg


def test_window_stats_needs_two_points():
    store, dates = _setup(_worse_when_bear(), _better_when_bear())
    nav = store.nav_series("q:new_sh").iloc[-1:]
    assert weekly.window_stats(nav, store.nav_series("bench:csi300"), dates[-1], dates[-1]) is None


def test_empty_store_gives_no_recommendation():
    store = Store(":memory:")
    data = weekly.collect(store, date(2026, 11, 14))
    assert data["rows"] == [] and data["start"] is None
    rec = weekly.decide(data)
    assert rec["action"] == "none" and "暂无" in rec["text"]
