from datetime import date, datetime, timedelta

import pytest

from engine import rules
from engine.rules import (Board, at_limit_down, at_limit_up, board, calc_fee, is_st, limit_pct,
                          limit_prices, round_lot)

DAY = timedelta(days=1)


@pytest.mark.parametrize("code,expected", [
    ("600000", Board.MAIN), ("000001", Board.MAIN), ("002594", Board.MAIN),
    ("300750", Board.CHINEXT), ("301269", Board.CHINEXT), ("302132", Board.CHINEXT),
    ("688981", Board.STAR), ("689009", Board.STAR),
    ("830799", Board.BSE), ("430047", Board.BSE), ("920001", Board.BSE),
])
def test_board(code, expected):
    assert board(code) is expected


def test_is_st():
    assert is_st("*ST康美") and is_st("ST海越") and is_st("st测试")
    assert not is_st("平安银行")


@pytest.mark.parametrize("code,name,pct", [
    ("600000", "浦发银行", 0.10), ("600000", "*ST浦发", 0.10),
    ("300750", "宁德时代", 0.20), ("300750", "ST宁德", 0.20),
    ("688981", "中芯国际", 0.20), ("830799", "艾融软件", 0.30),
])
def test_limit_pct(code, name, pct):
    assert limit_pct(code, name) == pct


def test_limit_prices_half_up():
    # 10.05*1.1 = 11.055 -> 11.06 ; 10.05*0.9 = 9.045 -> 9.05 (float math would give 11.05/9.04)
    assert limit_prices("600000", "浦发银行", 10.05) == (11.06, 9.05)


def test_limit_prices_boards():
    assert limit_prices("300750", "宁德时代", 10.0) == (12.0, 8.0)
    # 3.4965->3.50, 3.1635->3.16
    assert limit_prices("600000", "ST浦发", 3.33, date(2026, 7, 3)) == (3.50, 3.16)
    assert limit_prices("600000", "ST浦发", 3.33) == (3.66, 3.00)  # 主板 ST 自 2026-07-06 起 ±10%
    assert limit_prices("830799", "", 10.0) == (13.0, 7.0)


def test_round_lot():
    assert round_lot(1999.9) == 1900
    assert round_lot(100) == 100
    assert round_lot(99.9) == 0
    assert round_lot(-5) == 0


def test_fee_min_commission_buy():
    # 10000*0.00025 = 2.5 -> min 5 ; transfer 0.1
    assert calc_fee("BUY", 10_000) == 5.10


def test_fee_buy_large_no_stamp():
    # 100000: commission 25 + transfer 1
    assert calc_fee("BUY", 100_000) == 26.0


def test_fee_sell_has_stamp():
    # 100000: 25 + 1 + 50
    assert calc_fee("SELL", 100_000) == 76.0
    assert calc_fee("SELL", 10_000) == 10.10


def test_fee_rejects_unknown_side():
    for side in ("buy", "sell", "HOLD", ""):
        with pytest.raises(ValueError):
            calc_fee(side, 10_000)


def test_chinext_limit_switch_date():
    d = rules.CHINEXT_20PCT_FROM
    assert d == date(2020, 8, 24)
    assert limit_pct("300750", "宁德时代", d - DAY) == 0.10
    assert limit_pct("300750", "ST宁德", d - DAY) == 0.05
    assert limit_pct("300750", "宁德时代", d) == 0.20
    assert limit_pct("300750", "ST宁德", d) == 0.20
    assert limit_prices("300750", "宁德时代", 10.0, d - DAY) == (11.0, 9.0)
    assert limit_pct("300750", "宁德时代", datetime(2020, 8, 21, 15, 0)) == 0.10  # datetime/Timestamp ok


def test_main_board_st_limit_switch_date():
    d = rules.MAIN_ST_10PCT_FROM
    assert d == date(2026, 7, 6)
    assert limit_pct("600000", "*ST浦发", d - DAY) == 0.05
    assert limit_pct("600000", "*ST浦发", d) == 0.10
    assert limit_pct("600000", "浦发银行", d - DAY) == 0.10
    assert limit_prices("000001", "ST平安", 10.0, d - DAY) == (10.5, 9.5)
    assert limit_prices("000001", "ST平安", 10.0, d) == (11.0, 9.0)


def test_star_and_bse_limits_ignore_date_and_st():
    for d in (date(2019, 7, 22), None):
        assert limit_pct("688981", "ST中芯", d) == 0.20
        assert limit_pct("830799", "ST艾融", d) == 0.30


def test_302132_is_chinext():
    assert limit_pct("302132", "中航成飞", date(2025, 2, 17)) == 0.20
    assert limit_prices("302132", "中航成飞", 50.0) == (60.0, 40.0)


def test_transfer_fee_switch_date():
    d = rules.TRANSFER_CUT_FROM
    assert d == date(2022, 4, 29)
    # 100000: commission 25 + transfer 2 (0.002%) / 1 (0.001%)
    assert calc_fee("BUY", 100_000, d - DAY) == 27.0
    assert calc_fee("BUY", 100_000, d) == 26.0


def test_stamp_duty_switch_date():
    d = rules.STAMP_CUT_FROM
    assert d == date(2023, 8, 28)
    # 100000: commission 25 + transfer 1 + stamp 100 (0.1%) / 50 (0.05%)
    assert calc_fee("SELL", 100_000, d - DAY) == 126.0
    assert calc_fee("SELL", 100_000, d) == 76.0
    assert calc_fee("BUY", 100_000, d - DAY) == 26.0  # stamp duty is sell-only
    # before both cuts: 25 + 2 + 100
    assert calc_fee("SELL", 100_000, date(2020, 1, 2)) == 127.0


def test_limit_tolerance_one_tick():
    # forward-adjusted prices can sit one cent off the computed limit
    assert at_limit_up(11.06, 11.06) and at_limit_up(11.05, 11.06) and at_limit_up(11.2, 11.06)
    assert not at_limit_up(11.04, 11.06)
    assert at_limit_down(9.05, 9.05) and at_limit_down(9.06, 9.05) and at_limit_down(9.0, 9.05)
    assert not at_limit_down(9.07, 9.05)
