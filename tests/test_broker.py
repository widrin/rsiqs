from datetime import date

import pytest

from engine.broker import SimBroker
from engine.calendar import TradingCalendar
from engine.ledger import Account
from engine.types import Bar, Pick

DAYS = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]
CAL = TradingCalendar(DAYS)
D0, D1, D2 = DAYS[0], DAYS[1], DAYS[2]
SLIP = 0.001  # 10 bps


def bar(code="600000", d=D1, o=10.0, h=10.5, l=9.8, c=10.2, pc=10.0, vol=1e6, name="浦发银行"):
    return Bar(code, d, o, h, l, c, pc, vol, vol * c, name)


def pick(code="600000", entry_max=10.5, stop=9.3, target=11.0, hold=5, name="浦发银行"):
    return Pick(code, name, 1.0, entry_max, stop, target, hold, "测试")


def held(stop=9.3, target=11.0, exit_date=DAYS[5], cash=100_000.0):
    """Account holding 1000 sh of 600000 bought on D0."""
    acc = Account("t", cash)
    acc.buy(D0, "600000", "浦发银行", 10.0, 1000, stop, target, exit_date, "seed")
    return acc


def run(acc, bars, picks=(), d=D1, cap=20_000.0):
    return SimBroker(10.0).execute_day(acc, d, {b.code: b for b in bars}, list(picks), CAL, cap)


def test_buy_at_open_with_slippage_and_lot_rounding():
    acc = Account("t", 100_000.0)
    fills = run(acc, [bar(o=10.0)], [pick()])
    assert len(fills) == 1
    f = fills[0]
    assert f.side == "BUY" and f.price == pytest.approx(10.01)
    assert f.qty == 1900  # 20000/10.01 = 1998.0 -> 1900
    p = acc.positions["600000"]
    assert p.available == 0 and p.exit_date == CAL.offset(D1, 5) == DAYS[6]


def test_limit_up_open_blocks_buy():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=11.0, h=11.0, l=11.0, c=11.0)], [pick(entry_max=12.0)]) == []
    assert not acc.positions


def test_open_above_entry_max_skipped():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.6)], [pick(entry_max=10.5)]) == []


def test_suspended_pick_skipped():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(vol=0)], [pick()]) == []
    assert run(acc, [], [pick()]) == []


def test_qty_below_one_lot_skipped():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.0)], [pick()], cap=900.0) == []


def test_cash_caps_quantity():
    acc = Account("t", 5_000.0)
    f = run(acc, [bar(o=10.0)], [pick()], cap=20_000.0)[0]
    assert f.qty == 400  # 4995/10.01 = 499 -> 400


def test_min_commission_does_not_overdraw_cash():
    acc = Account("t", 2_000.0)
    # 1_005 cash: 1004/10.01 -> 100 shares, but 1001 + 5 (min commission) > 1005 -> skip, not raise
    acc2 = Account("t2", 1_005.0)
    assert run(acc, [bar(o=10.0)], [pick()], cap=20_000.0)[0].qty == 100
    assert run(acc2, [bar(o=10.0)], [pick()], cap=20_000.0) == []
    assert acc2.cash == 1_005.0


def test_already_held_not_rebought():
    acc = held()
    fills = run(acc, [bar(o=10.0, h=10.3, l=9.9)], [pick()])
    assert fills == [] and acc.positions["600000"].qty == 1000


def test_stop_loss_gap_down_fills_at_open():
    acc = held(stop=9.3)
    f = run(acc, [bar(o=9.0, h=9.2, l=8.9, c=9.1, pc=9.8)])[0]  # limit-down 8.82: no clipping
    assert f.side == "SELL" and f.reason == "止损" and f.qty == 1000
    assert f.price == pytest.approx(9.0 * (1 - SLIP))
    assert "600000" not in acc.positions


def test_stop_loss_intraday_fills_at_stop():
    acc = held(stop=9.3)
    f = run(acc, [bar(o=9.8, h=9.9, l=9.2)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(9.3 * (1 - SLIP))


def test_take_profit():
    acc = held(target=11.0)
    f = run(acc, [bar(o=10.5, h=11.2, l=10.4)])[0]
    assert f.reason == "止盈" and f.price == pytest.approx(11.0 * (1 - SLIP))
    acc2 = held(target=11.0)
    f2 = run(acc2, [bar(o=11.05, h=11.1, l=10.8, pc=10.1)])[0]  # gap above target
    assert f2.reason == "止盈" and f2.price == pytest.approx(11.05 * (1 - SLIP))


def test_expiry_exit_on_exit_date():
    acc = held(exit_date=D2)
    assert run(acc, [bar(d=D1)], d=D1) == []  # quiet day before expiry
    f = run(acc, [bar(d=D2, o=10.1)], d=D2)[0]
    assert f.reason == "到期" and f.price == pytest.approx(10.1 * (1 - SLIP))


def test_all_day_limit_down_blocks_sell():
    acc = held(stop=9.3)
    assert run(acc, [bar(o=9.0, h=9.0, l=9.0, c=9.0, pc=10.0)]) == []
    assert acc.positions["600000"].qty == 1000


def test_suspended_position_not_sold():
    acc = held(exit_date=D1)
    assert run(acc, [bar(vol=0)]) == []
    assert run(acc, []) == []


def test_bought_today_not_exited_today():
    acc = Account("t", 100_000.0)
    # opens 10.0 (buy), then low 9.0 pierces stop 9.3 — but T+1 forbids selling today
    fills = run(acc, [bar(o=10.0, l=9.0)], [pick(stop=9.3)])
    assert [f.side for f in fills] == ["BUY"]
    assert acc.positions["600000"].qty == 1900


def test_exit_then_enter_same_day_uses_freed_cash():
    acc = held(exit_date=D1)
    fills = run(acc, [bar(), bar(code="000001", name="平安银行", o=12.0, h=12.3, l=11.9, pc=12.0)],
                [pick(code="000001", name="平安银行", entry_max=12.5, stop=11.0, target=13.5)])
    assert [(f.side, f.code) for f in fills] == [("SELL", "600000"), ("BUY", "000001")]


PA = dict(code="000001", name="平安银行")
PA_BAR = dict(o=12.0, h=12.3, l=11.9, pc=12.0, **PA)
PA_PICK = dict(entry_max=12.5, stop=11.0, target=13.5, **PA)


def test_intraday_take_profit_cash_not_used_for_open_buy():
    acc = held(cash=10_100.0)  # 94.9 cash left after the seed buy
    fills = run(acc, [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)])
    assert [(f.side, f.code, f.reason) for f in fills] == [("SELL", "600000", "止盈")]
    assert "000001" not in acc.positions and acc.cash > 10_000


def test_expiry_cash_used_for_open_buy():
    acc = held(cash=10_100.0, exit_date=D1)
    fills = run(acc, [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)])
    assert [(f.side, f.code, f.reason) for f in fills] == [("SELL", "600000", "到期"),
                                                          ("BUY", "000001", "测试")]


def test_open_above_target_wins_over_later_stop():
    acc = held(stop=9.3, target=11.0)
    f = run(acc, [bar(o=11.1, h=11.2, l=9.2, pc=10.2)])[0]  # gap above target, then falls through stop
    assert f.reason == "止盈" and f.price == pytest.approx(11.1 * (1 - SLIP))


def test_intraday_stop_and_target_both_touched_stop_wins():
    acc = held(stop=9.3, target=11.0)
    f = run(acc, [bar(o=10.0, h=11.2, l=9.2)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(9.3 * (1 - SLIP))


def test_rerun_same_day_is_a_no_op():
    # cash-starved, intraday take-profit: a second run must not spend the step-3 cash on the pick
    acc = held(cash=10_100.0)
    bars, picks = [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)]
    assert [f.reason for f in run(acc, bars, picks)] == ["止盈"]
    before = acc.to_dict()
    assert run(acc, bars, picks) == []
    assert acc.to_dict() == before and acc.last_trade_day == D1


def test_earlier_day_than_last_trade_day_raises():
    acc = Account("t", 100_000.0)
    run(acc, [bar(d=D2)], [pick()], d=D2)
    before = acc.to_dict()
    with pytest.raises(ValueError):
        run(acc, [bar(d=D1)], [pick(code="000001", name="平安银行")], d=D1)
    assert acc.to_dict() == before


def test_last_trade_day_survives_serialization():
    acc = Account("t", 100_000.0)
    run(acc, [bar()], [pick()])
    acc2 = Account.from_dict(acc.to_dict())
    assert acc2.last_trade_day == D1
    assert run(acc2, [bar()], [pick(code="000001", name="平安银行")]) == []


@pytest.mark.parametrize("over", [dict(o=float("nan")), dict(h=float("nan")), dict(l=0.0),
                                  dict(pc=0.0), dict(pc=float("nan")), dict(vol=float("nan"))])
def test_invalid_bar_treated_as_suspended(over):
    acc = held(exit_date=D1)
    assert run(acc, [bar(**over)]) == [] and acc.positions["600000"].qty == 1000
    acc2 = Account("t", 100_000.0)
    assert run(acc2, [bar(**over)], [pick()]) == [] and not acc2.positions


def test_one_tick_tolerance_blocks_buy_and_sell():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.99, h=11.0, l=10.9)], [pick(entry_max=12.0)]) == []  # limit-up 11.00
    acc2 = held(stop=9.3)
    assert run(acc2, [bar(o=9.0, h=9.01, l=9.0, c=9.0)]) == []  # limit-down 9.00, high 9.01


def test_sell_price_clipped_to_limit_down():
    acc = held(stop=9.3)
    f = run(acc, [bar(o=9.0, h=9.5, l=9.0, c=9.2, pc=10.0)])[0]  # open at limit-down 9.00, then trades
    assert f.reason == "止损" and f.price == 9.0  # 9.0 * (1 - slip) = 8.991 -> clipped to 9.00


ST_CAL = TradingCalendar([date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7)])


def test_st_sealed_limit_down_before_rule_change_blocks_sell():
    # 2026-07-03: main-board ST still ±5%, so 9.50 is the sealed limit-down (would not be under ±10%)
    d0, d = date(2026, 7, 2), date(2026, 7, 3)
    acc = Account("t", 100_000.0)
    acc.buy(d0, "600001", "ST测试", 10.0, 1000, 9.6, 11.0, d, "seed")
    st_bar = Bar("600001", d, 9.5, 9.5, 9.5, 9.5, 10.0, 1e6, 9.5e6, "ST测试")
    assert SimBroker(10.0).execute_day(acc, d, {"600001": st_bar}, [], ST_CAL, 20_000.0) == []
    assert acc.positions["600001"].qty == 1000
    # 2026-07-06: ±10% applies, the same prices are no longer limit-down -> expiry sell goes through
    d2 = date(2026, 7, 6)
    st_bar2 = Bar("600001", d2, 9.5, 9.5, 9.5, 9.5, 10.0, 1e6, 9.5e6, "ST测试")
    fills = SimBroker(10.0).execute_day(acc, d2, {"600001": st_bar2}, [], ST_CAL, 20_000.0)
    assert [f.reason for f in fills] == ["到期"]


def test_open_at_or_below_stop_not_bought():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=9.6, h=9.9, l=9.5, c=9.7)], [pick(stop=9.8)]) == []
    assert run(acc, [bar(o=9.8, h=9.9, l=9.5, c=9.7)], [pick(stop=9.8)]) == []
    assert not acc.positions


def test_ex_rights_day_buy_not_clipped_to_stale_limit_band():
    # 10转5 ex-date, prev_close still the pre-split 20.00: band [18.00, 22.00] never traded today
    acc = Account("t", 100_000.0)
    f = run(acc, [bar(o=13.40, h=13.6, l=13.3, c=13.5, pc=20.0)],
            [pick(entry_max=14.0, stop=12.5, target=15.0)])[0]
    assert f.side == "BUY" and f.price == pytest.approx(13.40 * (1 + SLIP))


def test_unreliable_band_skips_limit_checks():
    # o=h=l would look sealed at limit-down / limit-up, but the prices lie outside the band
    acc = held(exit_date=D1)
    f = run(acc, [bar(o=13.4, h=13.4, l=13.4, c=13.4, pc=20.0)])[0]
    assert f.reason == "到期" and f.price == pytest.approx(13.4 * (1 - SLIP))
    acc2 = Account("t", 100_000.0)
    f2 = run(acc2, [bar(o=15.0, h=15.0, l=15.0, c=15.0, pc=10.0)],
             [pick(entry_max=15.5, stop=14.0, target=17.0)])[0]
    assert f2.side == "BUY" and f2.price == pytest.approx(15.0 * (1 + SLIP))


def test_unreliable_band_intraday_stop_not_clipped():
    acc = held(stop=9.3)
    # band [18.00, 22.00] from the stale prev_close; the stop fill stays at stop * (1 - slip)
    f = run(acc, [bar(o=9.8, h=9.9, l=9.2, c=9.5, pc=20.0)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(9.3 * (1 - SLIP))


@pytest.mark.parametrize("h,clipped", [(22.01, True), (22.02, False)])
def test_band_reliability_allows_one_tick(h, clipped):
    # band [18.00, 22.00]: a high one tick above limit-up is still rounding, two ticks is not
    acc = Account("t", 100_000.0)
    f = run(acc, [bar(o=21.98, h=h, l=21.9, c=21.99, pc=20.0)],
            [pick(entry_max=22.5, stop=20.0, target=24.0)])[0]
    raw = 21.98 * (1 + SLIP)
    assert f.price == (22.0 if clipped else pytest.approx(raw))


def test_reliable_band_still_clips_buy_at_limit_up():
    acc = Account("t", 100_000.0)
    f = run(acc, [bar(o=21.98, h=22.0, l=21.9, c=22.0, pc=20.0)],
            [pick(entry_max=22.5, stop=20.0, target=24.0)])[0]
    assert f.price == 22.0  # 21.98 * 1.001 = 22.002 -> clipped to limit-up 22.00
