from datetime import datetime, time

import pandas as pd
import pytest

import engine.integrity as integrity
import engine.promotion as promotion
from engine.types import Pick
from jobs import bootstrap, settle
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, cal, install_context,
                            make_dates, make_index, make_panel, make_settings, new_store,
                            next_saturday, prepare_root)

ACCOUNTS = ["q:baseline_momo", "h:active", "h:candidate", "bench:csi300"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(integrity, "write_manifest", lambda r: None)
    dates = make_dates()
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    client.current = dates[100]
    cache, store, notifier = FakeCache(panel, NAMES, index), new_store(), NullNotifier()
    settings = make_settings(root)
    install_context(monkeypatch, settings, store, client, cache, cal(dates), notifier)
    bootstrap.main(now=datetime.combine(dates[100], time(20, 0)))
    notifier.sent.clear()
    decided = []
    monkeypatch.setattr(promotion, "decide", lambda s, d, c: decided.append(d) or [])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0,
                stop=0.01, target=999.0, hold_days=5, reason="测试")
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True})
    return dict(dates=dates, store=store, cache=cache, notifier=notifier,
                settings=settings, index=index, decided=decided, client=client)


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm))


def test_catch_up_multiple_missed_days(env):
    dates, store = env["dates"], env["store"]
    settled = settle.main(now=at(dates[104], 16))
    assert settled == dates[101:105]
    for acct in ACCOUNTS:
        idx = store.nav_series(acct).index
        assert [ts.date() for ts in idx] == dates[100:105], acct
        assert store.job_done("settle", dates[101])
    active = store.load_account("h:active")
    assert any(f.side == "BUY" and f.code == "600001" and f.date == dates[101]
               for f in active.trades)
    assert store.load_account("q:baseline_momo").trades  # 量化账户按策略自行选股成交
    assert store.load_account("h:candidate").trades == []  # 无推荐 -> 不交易
    bench = store.nav_series("bench:csi300")
    expected = env["settings"].capital * env["index"].iloc[104] / env["index"].iloc[100]
    assert bench.iloc[-1] == pytest.approx(expected)
    assert env["cache"].updates[-1] == dates[104]  # 补跑前一次性刷新到最后一天
    assert env["decided"] == dates[101:105]  # 每个结算日跑一次晋升判定


def test_rerun_is_idempotent(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[102], 16))
    n_trades = len(store.load_account("h:active").trades)
    cash = store.load_account("q:baseline_momo").cash
    assert settle.main(now=at(dates[102], 17)) == []
    # 即使绕过 job 标记强制重算某天，账户也不会重复成交
    settle.settle_day(dates[102], env["settings"], store, env["cache"], cal(dates))
    assert len(store.load_account("h:active").trades) == n_trades
    assert store.load_account("q:baseline_momo").cash == cash
    assert len(store.nav_series("h:active")) == 3


def test_today_not_settled_before_1510(env):
    dates, store = env["dates"], env["store"]
    assert settle.main(now=at(dates[102], 15, 5)) == [dates[101]]
    assert store.last_nav_date("h:active") == dates[101]
    assert settle.main(now=at(dates[102], 15, 10)) == [dates[102]]
    assert store.last_nav_date("h:active") == dates[102]


def test_weekend_settles_up_to_friday(env):
    dates, store = env["dates"], env["store"]
    sat = next_saturday(dates[103])
    settle.main(now=datetime.combine(sat, time(10)))
    assert store.last_nav_date("bench:csi300") == max(d for d in dates if d < sat)


def test_partial_data_below_coverage_pauses(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    for c in CODES[:2]:  # 10 只里 2 只还没有当天 K 线：80% 低于覆盖率门槛
        cache.panel[c] = cache.panel[c].drop(pd.Timestamp(dates[101]))
    assert settle.main(now=at(dates[101], 16)) == []
    assert any("覆盖不足" in str(m) for m in env["notifier"].sent)


def test_catch_up_stops_at_first_failed_day(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    real = settle.settle_and_decide

    def flaky(d, *a, **k):
        if d == dates[102]:
            raise RuntimeError("指数缺数据")
        return real(d, *a, **k)

    monkeypatch.setattr(settle, "settle_and_decide", flaky)
    assert settle.main(now=at(dates[104], 16)) == [dates[101]]
    assert store.last_nav_date("h:active") == dates[101]  # 次日依赖前日持仓：后面的日子不结算
    assert not store.job_done("settle", dates[103])


def test_universe_refreshed_and_recommendations_refreshed(env, monkeypatch):
    dates, store, cache, client = env["dates"], env["store"], env["cache"], env["client"]
    monkeypatch.setattr(client, "universe", lambda: CODES[:5])
    out_pick = Pick(code="600010", name=NAMES["600010"], score=1.0, entry_max=999.0, stop=0.01,
                    target=999.0, hold_days=5, reason="股票池外的推荐")
    store.save_recommendation(dates[101], "h:candidate", [out_pick], {"llm_reviewed": True})
    settle.main(now=at(dates[101], 16))
    assert store.kv_get("universe") == CODES[:5]
    assert "600010" in cache.update_codes[-1]  # 待结算日的推荐代码也刷新日线


def test_universe_kept_when_refresh_fails(env, monkeypatch):
    dates, store, client = env["dates"], env["store"], env["client"]
    before = store.kv_get("universe")

    def boom():
        raise ConnectionError("中证官网不可用")

    monkeypatch.setattr(client, "universe", boom)
    assert settle.main(now=at(dates[101], 16)) == [dates[101]]
    assert store.kv_get("universe") == before


def test_strategy_view_excludes_llm_recommended_codes(env, monkeypatch):
    dates, store, client = env["dates"], env["store"], env["client"]
    monkeypatch.setattr(client, "universe", lambda: CODES[:5])
    store.kv_set("universe", CODES[:5])
    out_pick = Pick(code="600010", name=NAMES["600010"], score=1.0, entry_max=999.0, stop=0.01,
                    target=999.0, hold_days=5, reason="股票池外的推荐")
    store.save_recommendation(dates[101], "h:candidate", [out_pick], {"llm_reviewed": True})
    seen = []

    class Probe:
        name = "baseline_momo"

        def select(self, view):
            seen.append(sorted(view.codes()))
            return []

    monkeypatch.setattr(settle, "load_strategy", lambda name: Probe())
    settle.main(now=at(dates[101], 16))
    assert seen and "600010" not in seen[0] and "600001" in seen[0]
    assert any(f.code == "600010" for f in store.load_account("h:candidate").trades)  # 推荐照常撮合


def test_late_dividend_record_is_applied_next_day(env):
    dates, store, client = env["dates"], env["store"], env["client"]
    settle.main(now=at(dates[103], 16))  # 600001 于 dates[101] 买入；dates[103] 除息当天还没有分红记录
    assert store.load_account("h:active").corporate_actions == []
    client.dividend_data = {"600001": [(dates[103], 1.0, 0.0)]}
    settle.main(now=at(dates[104], 16))
    acts = store.load_account("h:active").corporate_actions
    assert [a["date"] for a in acts] == [dates[103].isoformat()]
    assert any("补记" in str(m) for m in env["notifier"].sent)


def test_stale_data_aborts_with_alert(env, monkeypatch):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    real = cache.load_panel
    monkeypatch.setattr(cache, "load_panel", lambda codes, a, b: real(codes, a, dates[100]))
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.last_nav_date("h:active") == dates[100]
    assert not store.job_done("settle", dates[101])
    assert any("覆盖不足" in str(m) for m in env["notifier"].sent)


def test_shadow_account_created_and_broken_strategy_skipped(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    store.set_status("momo_shadow", "shadow", dates[100])
    store.set_status("broken_one", "shadow", dates[100])
    real = settle.load_strategy

    def fake_load(name):
        if name == "broken_one":
            raise ImportError("坏策略")
        return real("baseline_momo")

    monkeypatch.setattr(settle, "load_strategy", fake_load)
    assert settle.main(now=at(dates[101], 16)) == [dates[101]]
    assert store.last_nav_date("q:momo_shadow") == dates[101]
    assert store.load_account("q:momo_shadow").trades
    assert store.last_nav_date("q:broken_one") is None  # 选股失败 -> 当日不记净值
    assert any("broken_one" in str(m) for m in env["notifier"].sent)


def test_crash_before_account_saved_loses_no_trades(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    real = store.save_account

    def killed_once(acc):
        if acc.name == "h:active" and acc.last_trade_day == dates[101]:
            monkeypatch.setattr(store, "save_account", real)
            raise RuntimeError("进程被杀")
        return real(acc)

    monkeypatch.setattr(store, "save_account", killed_once)
    assert settle.main(now=at(dates[101], 16)) == []
    assert settle.main(now=at(dates[101], 17)) == [dates[101]]
    buys = [f for f in store.load_account("h:active").trades if f.side == "BUY" and f.date == dates[101]]
    assert len(buys) == 1 and store.last_nav_date("h:active") == dates[101]


def test_crash_after_account_saved_records_nav_once(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    real = store.record_nav

    def killed_once(account, d, equity):
        if account == "h:active" and d == dates[101]:
            monkeypatch.setattr(store, "record_nav", real)
            raise RuntimeError("进程被杀")
        return real(account, d, equity)

    monkeypatch.setattr(store, "record_nav", killed_once)
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.load_account("h:active").last_trade_day == dates[101]  # 账户已存，净值没记
    assert settle.main(now=at(dates[101], 17)) == [dates[101]]
    buys = [f for f in store.load_account("h:active").trades if f.side == "BUY" and f.date == dates[101]]
    assert len(buys) == 1 and store.last_nav_date("h:active") == dates[101]


def test_nan_close_uses_last_valid_close(env):
    dates, store = env["dates"], env["store"]
    env["cache"].raw["600001"].loc[pd.Timestamp(dates[101]), "close"] = float("nan")
    assert settle.main(now=at(dates[101], 16)) == [dates[101]]
    assert store.nav_series("h:active").notna().all()


def test_strategy_view_stops_at_previous_day(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    seen = []

    class Probe:
        name = "probe"

        def select(self, view):  # 策略经 history 能看到的最后一行：d 日的行情不在里面
            seen.append(max(view.history(c).index.max() for c in view.codes()))
            return []

    monkeypatch.setattr(settle, "load_strategy", lambda name: Probe())
    settle.main(now=at(dates[101], 16))
    assert seen == [pd.Timestamp(dates[100])]


def test_ex_dividend_adjusts_position_before_matching(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))  # h:active 买入 600001
    pos = store.load_account("h:active").positions["600001"]
    qty0, stop0, cash0 = pos.qty, pos.stop, store.load_account("h:active").cash
    env["client"].dividend_data = {"600001": [(dates[102], 0.2, 0.5)]}  # 10 转 5 派 2
    settle.main(now=at(dates[102], 16))
    acc = store.load_account("h:active")
    pos = acc.positions["600001"]
    assert pos.qty == int(qty0 * 1.5) and pos.stop == pytest.approx(max((stop0 - 0.2) / 1.5, 0.01))
    assert acc.corporate_actions[0]["date"] == dates[102].isoformat()
    sells = [f for f in acc.trades if f.side == "SELL" and f.date == dates[102]]
    assert sells == [] and acc.cash == pytest.approx(cash0 + round(qty0 * 0.2 * 0.8, 2))
    assert any("除权除息" in str(m) for m in env["notifier"].sent)
    settle.settle_day(dates[102], env["settings"], store, env["cache"], cal(dates))  # 强制重算不会重复调整
    assert len(store.load_account("h:active").corporate_actions) == 1


def test_recommendation_prices_adjusted_on_ex_date(env):
    dates, store = env["dates"], env["store"]
    env["client"].dividend_data = {"600001": [(dates[101], 0.0, 1.0)]}  # 10 转 10：价格减半
    # 推荐写于除权前：除权后开盘约 3.29 元，低于止损 6.0、高于换算后的 3.0；不换算就买不进
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=6.0,
                target=999.0, hold_days=5, reason="测试")
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True})
    _split_from(env, "600001", dates[101])  # 除权日起的不复权日线（参考昨收是除权参考价）
    settle.main(now=at(dates[101], 16))  # 当天推荐的股票也会刷新分红记录
    buys = [f for f in store.load_account("h:active").trades if f.side == "BUY" and f.code == "600001"]
    pos = store.load_account("h:active").positions["600001"]
    assert buys and pos.stop == pytest.approx(3.0) and pos.target == pytest.approx(999.0 / 2)


def test_dividend_fetch_failure_alerts_but_settles(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    env["client"].dividend_data = {"600001": "FAIL"}
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    assert any("除权数据获取失败" in str(m) and "600001" in str(m) for m in env["notifier"].sent)


def _split_from(env, code, d, k=2.0):
    """d 日起 10 转 10（k=2）：不复权价格从 d 日起除以 k，d 日的参考昨收就是除权参考价。"""
    df = env["cache"].raw[code]
    m = df.index >= pd.Timestamp(d)
    cols = ["open", "high", "low", "close", "prev_close"]
    df.loc[m, cols] = df.loc[m, cols] / k


def _rec_with_tight_stop(env, d):
    """d 日推荐 600001，止损设在开盘价下方 10%：除权日不先调整持仓的话一定会记下假止损。"""
    o = float(env["cache"].raw["600001"].loc[pd.Timestamp(d), "open"])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=round(o * 0.9, 2),
                target=999.0, hold_days=5, reason="测试")
    env["store"].save_recommendation(d, "h:active", [pick], {"llm_reviewed": True})
    return pick


def test_ex_rights_adjusted_before_matching(env):
    dates, store = env["dates"], env["store"]
    pick = _rec_with_tight_stop(env, dates[101])
    settle.main(now=at(dates[101], 16))
    env["client"].dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}
    _split_from(env, "600001", dates[102])
    settle.main(now=at(dates[102], 16))
    acc = store.load_account("h:active")
    assert [f for f in acc.trades if f.side == "SELL"] == []
    assert acc.positions["600001"].stop == pytest.approx(pick.stop / 2)
    nav = store.nav_series("h:active")
    assert nav.iloc[-1] == pytest.approx(nav.iloc[-2], rel=0.05)  # 股数翻倍、价格减半：净值连续


def test_raw_bar_failure_pauses_and_retries(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    cache.raw_fail = {"600001"}  # 推荐的股票取不到不复权日线：按停牌处理会漏掉买入，所以暂停
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.last_nav_date("h:active") == dates[100]
    cache.raw_fail = set()
    assert settle.main(now=at(dates[101], 17)) == [dates[101]]
    assert any(f.code == "600001" for f in store.load_account("h:active").trades)


def test_raw_empty_while_adjusted_has_bar_pauses(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    cache.raw["600001"] = cache.raw["600001"].drop(pd.Timestamp(dates[101]))  # 接口返回空表：update_raw 不报失败
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.last_nav_date("h:active") == dates[100]


def test_raw_update_failure_alone_pauses(env, monkeypatch):
    dates, cache = env["dates"], env["cache"]
    monkeypatch.setattr(cache, "update_raw", lambda codes, end, start: ["600001"])  # 旧数据还在，但这次更新失败
    assert settle.main(now=at(dates[101], 16)) == []


def test_recommendation_outside_universe_is_checked(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    df = cache.panel["600001"].copy()
    cache.panel["600099"], cache.raw["600099"], cache.names["600099"] = df.copy(), df.copy(), "新成分"
    cache.raw["600099"] = cache.raw["600099"].drop(pd.Timestamp(dates[101]))
    pick = Pick(code="600099", name="新成分", score=1.0, entry_max=999.0, stop=0.01, target=999.0, hold_days=5)
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True})
    assert settle.main(now=at(dates[101], 16)) == []  # 不在 kv universe 里，同样暂停而不是按停牌跳过


def test_unexplained_ex_with_old_dividend_records_still_pauses(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    env["cache"].save_dividends("600001", pd.DataFrame({"ex_date": [dates[50]], "cash": [0.1], "ratio": [0.0]}))
    _split_from(env, "600001", dates[102])
    env["client"].dividend_data = {"600001": "FAIL"}  # 今天的记录取不到，历史记录不能拿来解释今天的除权
    assert settle.main(now=at(dates[102], 16)) == []


def test_small_unrecorded_dividend_is_detected(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    df = env["cache"].raw["600001"]
    m = df.index >= pd.Timestamp(dates[102])
    df.loc[m, ["open", "high", "low", "close", "prev_close"]] -= 0.05  # 每股派息 0.05 元，没有分红记录
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    assert any("可能是配股" in str(m) for m in env["notifier"].sent)


def test_catch_up_across_ex_date_matches_on_unadjusted_prices(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    env["client"].dividend_data = {"600001": [(dates[102], 0.5, 0.0)]}
    # 前复权在除权后把此前的价格整体下调 0.5；不复权日线不变
    m = cache.panel["600001"].index < pd.Timestamp(dates[102])
    cache.panel["600001"].loc[m, ["open", "high", "low", "close", "prev_close"]] -= 0.5
    settle.main(now=at(dates[102], 16))  # 一次补跑 101、102
    acc = store.load_account("h:active")
    buy = next(f for f in acc.trades if f.side == "BUY" and f.code == "600001")
    raw_open = float(cache.raw["600001"].loc[pd.Timestamp(dates[101]), "open"])
    assert buy.price == pytest.approx(raw_open * 1.001)  # 按当天真实价格成交，不是下调后的前复权价
    assert [a["date"] for a in acc.corporate_actions] == [dates[102].isoformat()]  # 分红只入账一次


def test_missing_bar_on_ex_date_values_at_ex_rights_price(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    settle.main(now=at(dates[101], 16))
    env["client"].dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}
    _split_from(env, "600001", dates[102])
    ts = pd.Timestamp(dates[102])  # 除权日停牌：前复权和不复权都没有这根 K 线
    cache.raw["600001"], cache.panel["600001"] = cache.raw["600001"].drop(ts), cache.panel["600001"].drop(ts)
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    nav = store.nav_series("h:active")
    assert store.load_account("h:active").positions["600001"].qty % 2 == 0  # 股数已翻倍
    assert nav.iloc[-1] == pytest.approx(nav.iloc[-2], rel=0.05)  # 估值价同样减半，净值没有虚增


def test_dividend_fetch_failure_on_ex_date_pauses_and_retries(env):
    dates, store = env["dates"], env["store"]
    pick = _rec_with_tight_stop(env, dates[101])
    settle.main(now=at(dates[101], 16))
    _split_from(env, "600001", dates[102])
    env["client"].dividend_data = {"600001": "FAIL"}
    assert settle.main(now=at(dates[102], 16)) == []  # 疑似除权却没有分红记录：暂停
    assert store.last_nav_date("h:active") == dates[101]
    env["client"].dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}
    assert settle.main(now=at(dates[102], 17)) == [dates[102]]  # 下次运行重试成功
    acc = store.load_account("h:active")
    assert acc.positions["600001"].stop == pytest.approx(pick.stop / 2)
    assert [f for f in acc.trades if f.side == "SELL"] == []


def test_unexplained_ex_without_record_warns(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    _split_from(env, "600001", dates[102], k=1.1)  # 配股之类：参考昨收变了，但没有分红记录
    assert settle.main(now=at(dates[102], 16)) == []  # 跌幅超过 3%：先等分红记录
    assert settle.main(now=at(dates[103], 16)) == []
    assert settle.main(now=at(dates[104], 16)) == dates[102:105]  # 等满 2 个交易日仍没有记录：按配股告警
    assert any("可能是配股" in str(m) for m in env["notifier"].sent)


def test_warnings_before_a_pause_are_not_lost(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    real_run = settle.settle_day

    def pausing(d, *a, warnings=None, **k):
        warnings.append("前面已结算账户的除权调整")
        raise settle.DataError("后面的账户要等分红记录")

    monkeypatch.setattr(settle, "settle_day", pausing)
    assert settle.main(now=at(dates[102], 16)) == []
    assert any("前面已结算账户的除权调整" in str(m) for m in env["notifier"].sent)
    monkeypatch.setattr(settle, "settle_day", real_run)


def test_retired_account_not_tracked(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    from engine.ledger import Account, Position
    acc = Account(name="q:old_one", cash=0.0)
    acc.positions["688999"] = Position(code="688999", name="退役持仓", qty=100, available=100, cost=10.0,
                                       entry_date=dates[90], stop=1.0, target=99.0, exit_date=dates[120])
    store.save_account(acc)
    store.set_status("old_one", "retired", dates[100])
    assert "688999" not in settle.tracked_codes(store)


def test_late_bonus_share_record_pauses_until_it_arrives(env):
    dates, store, client = env["dates"], env["store"], env["client"]
    settle.main(now=at(dates[101], 16))  # h:active 于 dates[101] 买入 600001
    qty = store.load_account("h:active").positions["600001"].qty
    _split_from(env, "600001", dates[102])  # 10 转 10，分红记录当天还没到
    assert settle.main(now=at(dates[102], 16)) == []
    assert any("疑似送转" in str(m) for m in env["notifier"].sent)
    client.dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}  # 第二天记录到了
    assert settle.main(now=at(dates[103], 16)) == dates[102:104]
    acc = store.load_account("h:active")
    assert acc.positions["600001"].qty == 2 * qty and not any(f.side == "SELL" for f in acc.trades)


def test_future_and_past_events_not_applied_again(env):
    dates, store = env["dates"], env["store"]
    env["client"].dividend_data = {"600001": [(dates[101], 0.0, 1.0), (dates[104], 0.0, 1.0)]}
    _split_from(env, "600001", dates[101])  # 101 日除权；h:active 在除权日当天按除权后的价格买入
    settle.main(now=at(dates[101], 16))
    qty = store.load_account("h:active").positions["600001"].qty
    settle.main(now=at(dates[102], 16))
    acc = store.load_account("h:active")
    assert acc.positions["600001"].qty == qty and acc.corporate_actions == []  # 101 的事件早于买入，104 的还没到


def test_recommendation_already_adjusted_is_not_adjusted_again(env):
    dates, store = env["dates"], env["store"]
    env["client"].dividend_data = {"600001": [(dates[101], 0.0, 1.0)]}
    _split_from(env, "600001", dates[101])
    o = float(env["cache"].raw["600001"].loc[pd.Timestamp(dates[101]), "open"])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=round(o * 0.9, 2),
                target=999.0, hold_days=5, reason="测试")
    ref = float(env["cache"].raw["600001"].loc[pd.Timestamp(dates[101]), "prev_close"])  # 已是除权参考价
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True, "ref_close": {"600001": ref}})
    settle.main(now=at(dates[101], 16))
    assert store.load_account("h:active").positions["600001"].stop == pytest.approx(pick.stop)


def test_recommendation_rescaled_by_ref_close(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    ts = pd.Timestamp(dates[101])
    ref = float(cache.raw["600001"].loc[ts, "prev_close"])  # 盘前按除权前的昨收写价位（快照没取到）
    _split_from(env, "600001", dates[101])  # 当天 10 转 10，不需要分红记录
    o = float(cache.raw["600001"].loc[ts, "open"])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=round(o * 1.8, 2),
                target=9999.0, hold_days=5, reason="测试")  # 止损按除权前价位写：不换算就高于开盘价，买不进
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True, "ref_close": {"600001": ref}})
    settle.main(now=at(dates[101], 16))
    pos = store.load_account("h:active").positions["600001"]
    assert pos.stop == pytest.approx(pick.stop / 2) and pos.target == pytest.approx(9999.0 / 2)


def test_quant_pick_converted_when_adjusted_history_moved(env, monkeypatch):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    env["client"].dividend_data = {"600001": [(dates[102], 0.5, 0.0)]}
    m = cache.panel["600001"].index < pd.Timestamp(dates[102])
    cache.panel["600001"].loc[m, ["open", "high", "low", "close", "prev_close"]] -= 0.5  # 前复权整体下调
    q_open = float(cache.panel["600001"].loc[pd.Timestamp(dates[101]), "open"])

    class Tight:  # 买入上限只比前复权开盘价高 2%：不换算的话，真实开盘价高出 0.5 元（约 8%），买不进
        name = "tight"

        def select(self, view):
            return [Pick(code="600001", name="x", score=1.0, entry_max=q_open * 1.02, stop=q_open * 0.8,
                         target=q_open * 1.5, hold_days=5)]

    monkeypatch.setattr(settle, "load_strategy", lambda name: Tight())
    settle.main(now=at(dates[102], 16))  # 一次补跑 101、102
    trades = store.load_account("q:baseline_momo").trades
    assert any(f.side == "BUY" and f.code == "600001" and f.date == dates[101] for f in trades)


def test_quant_pick_prices_converted_to_unadjusted():
    from engine.types import Bar
    d0 = make_dates()[0]
    # 窄振幅：之后 10 转 4，前复权 = 不复权 / 1.4 再舍入到分；按收盘价之比换算，误差在千分之一以内
    r = Bar("600001", d0, 10.33, 10.36, 10.31, 10.33, 10.30, 1e6, 1e7)
    q = Bar("600001", d0, 7.38, 7.40, 7.36, 7.38, 7.36, 1e6, 1e7)
    pick = Pick(code="600001", name="x", score=1.0, entry_max=7.53, stop=6.79, target=8.49, hold_days=5)
    out = settle._to_raw(pick, q, r)
    assert (out.entry_max, out.stop, out.target) == pytest.approx((7.53 * 1.4, 6.79 * 1.4, 8.49 * 1.4), rel=1e-3)
    assert settle._to_raw(pick, r, r) is pick and settle._to_raw(pick, None, r) is pick


def test_new_positions_get_dividend_records_for_tomorrow(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    settle.main(now=at(dates[101], 16))
    held = {c for n in store.list_accounts() for c in store.load_account(n).positions}
    assert held and held <= set(cache.divs)  # 结算完再刷新一次：供次日盘中监控换算


def test_names_refreshed_daily(env):
    dates, cache = env["dates"], env["cache"]
    env["client"].names = lambda: {**NAMES, "600001": "ST测试"}  # 收盘后戴帽
    settle.main(now=at(dates[101], 16))
    assert cache.names["600001"] == "ST测试"


def test_promotion_events_are_notified(env, monkeypatch):
    dates = env["dates"]
    monkeypatch.setattr(promotion, "decide",
                        lambda s, d, c: [promotion.Event("promote", "momo_v2", "超额 8%")])
    settle.main(now=at(dates[101], 16))
    assert any("momo_v2" in str(m) and "晋升" in str(m) for m in env["notifier"].sent)
