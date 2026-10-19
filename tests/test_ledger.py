import json
from datetime import date

import pytest

from engine.ledger import Account, InsufficientCash, NotAvailable

D1, D2, EXIT = date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 22)


def _bought():
    acc = Account("t", 100_000.0)
    acc.buy(D1, "600000", "浦发银行", 10.0, 1000, 9.3, 11.0, EXIT, "test")
    return acc


def test_buy_cash_and_position():
    acc = _bought()
    # amount 10000, fee max(2.5,5)+0.1 = 5.1
    assert acc.cash == pytest.approx(100_000 - 10_005.1)
    p = acc.positions["600000"]
    assert (p.qty, p.available, p.entry_date, p.exit_date) == (1000, 0, D1, EXIT)
    assert p.cost == pytest.approx(10.0051)
    assert acc.trades[-1].side == "BUY" and acc.trades[-1].fee == 5.1


def test_sell_same_day_raises_t_plus_1():
    acc = _bought()
    with pytest.raises(NotAvailable):
        acc.sell(D1, "600000", 10.5, 1000, "same day")


def test_sell_next_day_after_start_day():
    acc = _bought()
    acc.start_day(D2)
    assert acc.positions["600000"].available == 1000
    f = acc.sell(D2, "600000", 11.0, 1000, "止盈")
    # amount 11000: commission 5 + transfer 0.11 + stamp 5.5 = 10.61
    assert f.fee == 10.61
    assert acc.cash == pytest.approx(100_000 - 10_005.1 + 11_000 - 10.61)
    assert "600000" not in acc.positions


def test_partial_sell_keeps_position():
    acc = _bought()
    acc.start_day(D2)
    acc.sell(D2, "600000", 10.0, 400, "部分")
    p = acc.positions["600000"]
    assert (p.qty, p.available) == (600, 600)
    with pytest.raises(NotAvailable):
        acc.sell(D2, "600000", 10.0, 700, "超量")


def test_sell_unknown_code_raises():
    with pytest.raises(NotAvailable):
        Account("t", 1000.0).sell(D1, "000001", 10.0, 100, "x")


def test_insufficient_cash_and_bad_qty():
    acc = Account("t", 10_000.0)
    with pytest.raises(InsufficientCash):
        acc.buy(D1, "600000", "浦发银行", 10.0, 1000, 9, 11, EXIT)  # needs 10005.1
    assert acc.cash == 10_000.0 and not acc.positions
    with pytest.raises(ValueError):
        acc.buy(D1, "600000", "浦发银行", 10.0, 150, 9, 11, EXIT)


def test_equity_uses_price_or_cost():
    acc = _bought()
    assert acc.equity({"600000": 12.0}) == pytest.approx(acc.cash + 12_000)
    assert acc.equity({}) == pytest.approx(acc.cash + 10_005.1)


def test_roundtrip_json():
    acc = _bought()
    restored = Account.from_dict(json.loads(json.dumps(acc.to_dict())))
    assert restored == acc
