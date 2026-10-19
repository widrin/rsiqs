from datetime import date, time

import pandas as pd

from engine.calendar import TradingCalendar
from engine.ledger import Account, Position
from engine.store import Store
from jobs.monitor import check_once, in_session
from notify.wecom import NullNotifier

D = date(2026, 10, 16)


class SpotClient:
    def __init__(self, rows):
        self.df, self.n = pd.DataFrame(rows), 0

    def spot(self, codes=None):
        self.n += 1
        self.codes = codes
        return self.df.copy()


def row(code, last, high, prev_close=10.0):
    return {"code": code, "name": "测试", "open": prev_close, "prev_close": prev_close,
            "last": last, "pct": (last / prev_close - 1) * 100, "volume": 1e6,
            "amount": 1e7, "high": high}


def make_store(*positions):
    store = Store(":memory:")
    acc = Account(name="h:active", cash=100_000.0)
    for code, stop, target in positions:
        acc.positions[code] = Position(code=code, name="测试", qty=1000, available=1000,
                                       cost=10.0, entry_date=date(2026, 10, 12), stop=stop,
                                       target=target, exit_date=date(2026, 10, 30))
    store.save_account(acc)
    return store


def test_stop_target_zhaban_alerts_deduped_per_day():
    store = make_store(("600001", 9.3, 11.0), ("600002", 9.3, 11.0),
                       ("600003", 9.3, 11.5), ("600004", 9.0, 12.0))
    client = SpotClient([
        row("600001", 9.2, 10.1),                   # 跌破止损
        row("600002", 11.2, 11.2, prev_close=10.5),  # 突破止盈（未到涨停 11.55）
        row("600003", 10.5, 11.0),                  # 曾触及涨停 11.00 后打开 -> 炸板
        row("600004", 11.0, 11.0),                  # 封死涨停 -> 无告警
    ])
    n = NullNotifier()
    first = check_once(D, store, client, n)
    text = "\n".join(first)
    assert len(first) == 3
    assert "触发止损" in text and "600001" in text
    assert "触发止盈" in text and "600002" in text
    assert "炸板" in text and "600003" in text
    assert "600004" not in text
    assert client.codes == ["600001", "600002", "600003", "600004"]  # 只取持仓的报价
    assert check_once(D, store, client, n) == []          # 同日同事件不重复
    assert len(n.sent) == 3
    assert len(check_once(date(2026, 10, 19), store, client, n)) == 3  # 新的一天重新计


def test_new_event_for_same_code_still_alerts():
    store = make_store(("600001", 9.5, 12.0))
    client = SpotClient([row("600001", 10.6, 11.0)])
    n = NullNotifier()
    assert len(check_once(D, store, client, n)) == 1      # 炸板
    client.df = pd.DataFrame([row("600001", 9.4, 11.0)])
    out = check_once(D, store, client, n)                 # 炸板已报过，止损是新事件
    assert len(out) == 1 and "触发止损" in out[0]


class DivCache:
    def __init__(self, events):
        self.events = events

    def dividends(self, code):
        return self.events.get(code, [])


def test_ex_date_uses_adjusted_levels():
    store = make_store(("600001", 9.3, 11.0))  # 除权前的价位
    client = SpotClient([row("600001", 6.4, 6.6, prev_close=6.6)])  # 10 转 5 后正常交易
    cache = DivCache({"600001": [(D, 0.0, 0.5)]})
    assert check_once(D, store, client, NullNotifier(), cache) == []  # 换算后止损 6.2，没有跌破
    out = check_once(D, store, SpotClient([row("600001", 6.1, 6.6, prev_close=6.6)]), NullNotifier(), cache)
    assert len(out) == 1 and "触发止损" in out[0] and "6.20" in out[0]
    assert len(check_once(D, make_store(("600001", 9.3, 11.0)), client, NullNotifier())) == 1  # 不换算会误报
    # 前一天除权、结算已经调整过的持仓（有除权记录），今天不再换算
    yesterday = DivCache({"600001": [(date(2026, 10, 15), 0.0, 0.5)]})
    adjusted = make_store(("600001", 6.2, 7.3))
    acc = adjusted.load_account("h:active")
    acc.corporate_actions.append({"date": "2026-10-15", "code": "600001", "cash_per_share": 0.0,
                                  "share_ratio": 0.5})
    adjusted.save_account(acc)
    out = check_once(D, adjusted, SpotClient([row("600001", 6.1, 6.6, prev_close=6.6)]), NullNotifier(), yesterday)
    assert len(out) == 1 and "6.20" in out[0]


def test_previous_day_ex_not_yet_settled_uses_adjusted_levels():
    store = make_store(("600001", 9.3, 11.0))  # 前一天除权，结算失败还没调整这笔持仓
    cache = DivCache({"600001": [(date(2026, 10, 15), 0.0, 0.5)]})
    assert check_once(D, store, SpotClient([row("600001", 6.6, 6.7, prev_close=6.6)]), NullNotifier(), cache) == []
    out = check_once(D, store, SpotClient([row("600001", 6.1, 6.7, prev_close=6.6)]), NullNotifier(), cache)
    assert len(out) == 1 and "6.20" in out[0]
    # 建仓之前的除权不换算
    old = DivCache({"600001": [(date(2026, 10, 9), 0.0, 0.5)]})
    assert check_once(D, make_store(("600001", 9.3, 11.0)), SpotClient([row("600001", 9.5, 9.6)]),
                      NullNotifier(), old) == []


def test_position_due_today_is_not_monitored():
    store = make_store(("600001", 9.3, 11.0))
    acc = store.load_account("h:active")
    acc.positions["600001"].exit_date = D  # 今天到期：开盘已按到期卖出
    store.save_account(acc)
    assert check_once(D, store, SpotClient([row("600001", 9.0, 9.0)]), NullNotifier()) == []


class RawDivCache(DivCache):
    def __init__(self, events, last_close):
        super().__init__(events)
        self.last_close = last_close

    def last_raw_close(self, code, d):
        return self.last_close


CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)


def test_unexplained_ex_gap_does_not_alert_stop():
    store = make_store(("600001", 9.3, 11.0))
    cache = RawDivCache({}, (date(2026, 10, 15), 10.0))  # 昨收 10.0，今天参考昨收 5.0：疑似 10 转 10，没有记录
    out = check_once(D, store, SpotClient([row("600001", 4.9, 5.0, prev_close=5.0)]), NullNotifier(), cache, CAL)
    assert len(out) == 1 and "疑似除权" in out[0] and "触发止损" not in out[0]
    explained = RawDivCache({"600001": [(D, 0.0, 1.0)]}, (date(2026, 10, 15), 10.0))  # 有记录：按换算后的价位
    out = check_once(D, make_store(("600001", 9.3, 11.0)), SpotClient([row("600001", 4.5, 5.0, prev_close=5.0)]),
                     NullNotifier(), explained, CAL)
    assert len(out) == 1 and "触发止损" in out[0]


def test_stale_raw_close_is_not_taken_for_ex_rights():
    store = make_store(("600001", 9.0, 11.0))
    stale = RawDivCache({}, (date(2026, 10, 14), 10.0))  # 缓存停在前天：昨天正常跌 5% 不能当成除权
    out = check_once(D, store, SpotClient([row("600001", 8.9, 9.6, prev_close=9.5)]), NullNotifier(), stale, CAL)
    assert len(out) == 1 and "触发止损" in out[0]


def test_missing_quote_is_logged_once(caplog):
    store = make_store(("600001", 9.3, 11.0))
    with caplog.at_level("WARNING"):
        for _ in range(2):
            assert check_once(D, store, SpotClient([row("600002", 9.0, 9.0)]), NullNotifier()) == []
    assert caplog.text.count("no quote for 600001") == 1


def test_no_positions_skips_spot_call():
    client = SpotClient([])
    assert check_once(D, Store(":memory:"), client, NullNotifier()) == []
    assert client.n == 0


def test_missing_high_column_and_suspended_stock():
    store = make_store(("600001", 9.3, 11.0), ("600002", 9.3, 11.0))
    client = SpotClient([row("600001", 9.0, 9.0), row("600002", float("nan"), 0.0)])
    client.df = client.df.drop(columns=["high"])
    out = check_once(D, store, client, NullNotifier())
    assert len(out) == 1 and "600001" in out[0] and "触发止损" in out[0]


def test_in_session():
    assert in_session(time(9, 30)) and in_session(time(11, 30)) and in_session(time(14, 59))
    assert not in_session(time(9, 29)) and not in_session(time(12, 0))
    assert not in_session(time(15, 1))
