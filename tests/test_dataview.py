from datetime import date

import pandas as pd
import pytest

from engine.dataview import DataView, LookaheadError


def _frame(n=10, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=n)
    close = [10.0 + i for i in range(n)]
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "volume": [1e6] * n, "amount": [1e7] * n,
                         "prev_close": [float("nan")] + close[:-1]}, index=idx)


@pytest.fixture
def view():
    panel = {"600000": _frame(), "000001": _frame()}
    return DataView(panel, {"600000": "浦发银行"}, asof=date(2026, 1, 9))


def test_history_never_after_asof(view):
    h = view.history("600000")
    assert h.index.max() == pd.Timestamp("2026-01-09")
    assert len(h) == 5
    assert (h.index <= pd.Timestamp(view.asof)).all()


def test_history_last_n_and_copy(view):
    h = view.history("600000", 2)
    assert list(h["close"]) == [13.0, 14.0]
    h.loc[h.index[0], "close"] = -1.0
    assert view.history("600000", 2)["close"].iloc[0] == 13.0


def test_codes_and_name(view):
    assert view.codes() == ["000001", "600000"]
    assert view.name("600000") == "浦发银行"
    assert view.name("000001") == ""


def test_bar_on(view):
    b = view.bar_on("600000", date(2026, 1, 9))
    assert b.close == 14.0 and b.prev_close == 13.0 and b.name == "浦发银行"
    with pytest.raises(LookaheadError):
        view.bar_on("600000", date(2026, 1, 12))


def test_asof_readonly_and_state_sealed(view):
    with pytest.raises(AttributeError):
        view.asof = date(2030, 1, 1)
    assert not hasattr(view, "_panel") and not hasattr(view, "panel")
    with pytest.raises(TypeError):
        vars(view)  # 无 __dict__


def test_history_zero_and_unknown(view):
    assert view.history("600000", 0).empty
    with pytest.raises(KeyError):
        view.history("999999")
