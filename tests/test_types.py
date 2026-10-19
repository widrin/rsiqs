import dataclasses
from datetime import date

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
