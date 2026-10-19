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
    store = _setup(cand_daily=0.002, shadow_from=45)  # 15 个收益日
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
    # 晋升门槛设为不可达，单独验证降级规则（降级用自己的 DEMOTE_T）
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


@pytest.mark.parametrize("shadow_from,promoted", [(40, True), (41, False)])
def test_min_shadow_days_counts_return_days(shadow_from, promoted):
    # 影子期从 DAYS[shadow_from] 开始：前一交易日的净值是基线，之后每天一个收益日
    store = _setup(cand_daily=0.002, shadow_from=shadow_from)
    events = pr.decide(store, DAYS[-1], CAL)
    assert bool(events) is promoted, (60 - shadow_from, events)


def test_shadow_set_on_weekend_keeps_friday_baseline():
    store = Store(":memory:")
    friday = DAYS[29]
    assert friday.weekday() == 4
    saturday = pd.Timestamp(friday) + pd.Timedelta(days=1)
    store.set_status("new", "shadow", saturday.date())  # 研究任务在周六运行
    _record(store, "q:new", _nav(0.002)[29:], days=DAYS[29:])  # 周一首次结算时在周五记基线
    nav = pr._shadow_nav(store, "new", DAYS[-1], CAL)
    assert nav.index[0] == pd.Timestamp(friday) and len(nav) == 31


def test_shadow_nav_stops_at_today():
    store = _setup(cand_daily=0.002, shadow_from=30)
    assert pr._shadow_nav(store, "new", DAYS[45], CAL).index[-1] == IDX[45]
    assert pr.decide(store, DAYS[45], CAL) == []  # 截至当天只有 16 个收益日


def test_compare_aligns_nav_before_returns():
    base = pd.Series(_nav(0.003, noise=0.01), index=IDX)
    gappy = base.drop(IDX[3::4])  # 同一条净值，每 4 天缺一个点
    assert pr.compare(base, gappy) == pytest.approx((0.0, 0.0))
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status("dup", "shadow", DAYS[0])
    _record(store, "q:old", list(base))
    for ts, v in gappy.items():
        store.record_nav("q:dup", ts.date(), v)
    assert pr.decide(store, DAYS[-1], CAL) == []


def test_small_steady_excess_neither_promotes_nor_demotes():
    # 超额恒定约年化 2.5%：t 值极大，但没到晋升的 5%，现任也没落后到降级的 -5%
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status("new", "shadow", DAYS[0])
    _record(store, "q:old", _nav(0.0, noise=0.002))
    _record(store, "q:new", _nav(0.0001, noise=0.002))
    assert pr.decide(store, DAYS[-1], CAL) == []


def _two_navs(store, inc_rets, cand_rets, name="new"):
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status(name, "shadow", DAYS[0])
    for acct, rets in (("q:old", inc_rets), (f"q:{name}", cand_rets)):
        v, vals = 100.0, []
        for r in rets:
            v *= 1 + r
            vals.append(v)
        _record(store, acct, vals)


def test_demotion_uses_only_recent_window(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    # 影子前 19 天每天领先 1%，之后与现任完全同步：近 40 个收益日没有落后，不降级
    inc = [0.001 if i % 2 else -0.001 for i in range(60)]
    cand = [r + (0.01 if i < 20 else 0.0) for i, r in enumerate(inc)]
    _two_navs(store, inc, cand)
    assert pr.decide(store, DAYS[-1], CAL) == []


def test_demotion_requires_significant_lag(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    lag = -0.0003  # 现任每天落后约 0.03%，年化约 -7.5%
    noisy, quiet = Store(":memory:"), Store(":memory:")
    # 噪声很大时同样的落后幅度只是运气，不降级
    _two_navs(noisy, [lag + (0.01 if i % 2 else -0.01) for i in range(60)], [0.0] * 60)
    assert pr.decide(noisy, DAYS[-1], CAL) == []
    _two_navs(quiet, [lag + (0.0002 if i % 2 else -0.0002) for i in range(60)], [0.0] * 60)
    assert [e.kind for e in pr.decide(quiet, DAYS[-1], CAL)] == ["demote", "promote"]


def test_demotion_picks_the_shadow_incumbent_lags_most(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    inc = [0.0002 if i % 2 else -0.0002 for i in range(60)]
    jitter = [0.0001 if i % 3 else -0.0002 for i in range(60)]  # 超额不能恒定，否则 t 值无穷大
    _two_navs(store, inc, [r + 0.0005 + j for r, j in zip(inc, jitter)], name="mild")
    store.set_status("strong", "shadow", DAYS[0])
    v, vals = 100.0, []
    for r, j in zip(inc, jitter):
        v *= 1 + r + 0.001 + j
        vals.append(v)
    _record(store, "q:strong", vals)
    events = pr.decide(store, DAYS[-1], CAL)
    assert [(e.kind, e.strategy) for e in events] == [("demote", "old"), ("promote", "strong")]


def test_demoted_incumbent_shadow_period_starts_next_day(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    cal = TradingCalendar(DAYS + [d.date() for d in pd.bdate_range(IDX[-1] + pd.Timedelta(days=1), periods=5)])
    store = _setup(cand_daily=0.0005, inc_daily=0.0)
    assert [e.kind for e in pr.decide(store, DAYS[-1], cal)] == ["demote", "promote"]
    assert store.status_since("old") == cal.next_day(DAYS[-1])
    nav = pr._shadow_nav(store, "old", DAYS[-1], cal)
    assert list(nav.index) == [IDX[-1]]  # 只有基线，还没有影子期的收益日


def _steady(store, name, excess_daily, noise, status="shadow"):
    """现任 q:old 为 0 收益加交替噪声；name 在其上叠加每日 excess_daily 和独立的交替噪声 noise。"""
    if store.get_status("old") is None:
        store.set_status("old", "incumbent", DAYS[0])
        _record(store, "q:old", _nav(0.0, noise=0.002))
    store.set_status(name, status, DAYS[0])
    base = _nav(0.0, noise=0.002)
    rets = [base[0] / 100 - 1] + [base[i] / base[i - 1] - 1 for i in range(1, len(base))]
    v, vals = 100.0, []
    for i, r in enumerate(rets):
        v *= 1 + r + excess_daily + (noise if i % 3 == 0 else -noise / 2)
        vals.append(v)
    _record(store, f"q:{name}", vals)


@pytest.mark.parametrize("excess,noise,promoted", [
    (0.0003, 0.0, True),      # 年化约 7.6%，t 很大
    (0.00016, 0.0, False),    # 年化约 4%：不到 5%，t 再大也不晋升
    (0.00024, 0.0, True),     # 年化约 6%
    (0.0003, 0.02, False),    # 同样的超额、噪声很大：t < 1.5，不晋升
])
def test_promotion_needs_both_excess_and_significance(excess, noise, promoted):
    store = Store(":memory:")
    _steady(store, "new", excess, noise)
    events = pr.decide(store, DAYS[-1], CAL)
    assert any(e.kind == "promote" and e.strategy == "new" for e in events) is promoted


@pytest.mark.parametrize("lag,demoted", [(-0.00016, False), (-0.00024, True)])
def test_demotion_excess_threshold(monkeypatch, lag, demoted):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    # 现任落后影子：影子每日比现任多 -lag
    _steady(store, "new", -lag, 0.0001)
    kinds = [e.kind for e in pr.decide(store, DAYS[-1], CAL)]
    assert (kinds[:1] == ["demote"]) is demoted


@pytest.mark.parametrize("points,demoted", [(40, False), (41, True)])
def test_demotion_window_boundary(monkeypatch, points, demoted):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    _record(store, "q:old", _nav(0.0, noise=0.0002))
    store.set_status("new", "shadow", DAYS[len(DAYS) - points + 1])  # 影子期净值点数 = points
    _record(store, "q:new", _nav(0.001, noise=0.0004))  # 噪声不同：超额不恒定，t 值有限
    assert len(pr._shadow_nav(store, "new", DAYS[-1], CAL)) == points
    assert bool(pr.decide(store, DAYS[-1], CAL)) is demoted
