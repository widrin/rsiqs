import dataclasses
from datetime import date

import numpy as np
import pytest

from engine.types import Bar, Fill, Pick


def test_pick_roundtrip():
    p = Pick("600000", "浦发银行", 1.5, 10.3, 9.3, 11.0, 5, "动量")
    assert Pick.from_dict(p.to_dict()) == p


def test_pick_from_dict_default_reason():
    d = {"code": "000001", "name": "平安银行", "score": 0.2, "entry_max": 12.0,
         "stop": 11.0, "target": 13.0, "hold_days": 3}
    assert Pick.from_dict(d).reason == ""


def test_fill_roundtrip():
    f = Fill(date(2026, 10, 16), "600000", "BUY", 1000, 10.01, 5.1, "买入")
    d = f.to_dict()
    assert d["date"] == "2026-10-16"
    assert Fill.from_dict(d) == f


def test_bar_is_frozen():
    b = Bar("600000", date(2026, 10, 16), 10, 11, 9.5, 10.5, 10, 1e6, 1e7)
    assert b.name == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.open = 1.0


def _pick_kwargs(**over):
    kw = dict(code="600000", name="浦发银行", score=1.0, entry_max=10.3, stop=9.3, target=11.0,
              hold_days=5)
    kw.update(over)
    return kw


def test_pick_coerces_prices_and_hold_days():
    p = Pick(**_pick_kwargs(entry_max="10.3", stop=9, target=np.float64(11.0), hold_days=np.int64(3)))
    assert (p.entry_max, p.stop, p.target, p.hold_days) == (10.3, 9.0, 11.0, 3)
    assert type(p.stop) is float and type(p.target) is float and type(p.hold_days) is int
    assert Pick(**_pick_kwargs(hold_days=4.0)).hold_days == 4


@pytest.mark.parametrize("over", [
    {"hold_days": 2.5}, {"hold_days": 0}, {"hold_days": -1}, {"hold_days": "abc"},
    {"hold_days": True}, {"hold_days": float("nan")},
    {"entry_max": float("nan")}, {"stop": 0}, {"target": -1.0}, {"stop": float("inf")},
    {"entry_max": "abc"}, {"stop": None},
    {"stop": 11.0, "target": 11.0}, {"stop": 11.5, "target": 11.0},
])
def test_pick_rejects_invalid(over):
    with pytest.raises(ValueError):
        Pick(**_pick_kwargs(**over))


def test_pick_entry_max_may_exceed_target():
    assert Pick(**_pick_kwargs(entry_max=12.0, target=11.0)).entry_max == 12.0


def test_fill_side_must_be_buy_or_sell():
    for side in ("buy", "HOLD", ""):
        with pytest.raises(ValueError):
            Fill(date(2026, 10, 16), "600000", side, 100, 10.0, 5.0, "x")
