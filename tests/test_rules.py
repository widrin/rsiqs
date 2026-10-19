import pytest

from engine.rules import Board, board, calc_fee, is_st, limit_pct, limit_prices, round_lot


@pytest.mark.parametrize("code,expected", [
    ("600000", Board.MAIN), ("000001", Board.MAIN), ("002594", Board.MAIN),
    ("300750", Board.CHINEXT), ("301269", Board.CHINEXT),
    ("688981", Board.STAR), ("689009", Board.STAR),
    ("830799", Board.BSE), ("430047", Board.BSE), ("920001", Board.BSE),
])
def test_board(code, expected):
    assert board(code) is expected


def test_is_st():
    assert is_st("*ST康美") and is_st("ST海越") and is_st("st测试")
    assert not is_st("平安银行")


@pytest.mark.parametrize("code,name,pct", [
    ("600000", "浦发银行", 0.10), ("600000", "*ST浦发", 0.05),
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
    assert limit_prices("600000", "ST浦发", 3.33) == (3.50, 3.16)  # 3.4965->3.50, 3.1635->3.16
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
