from datetime import date

import pytest

from engine.calendar import TradingCalendar

# 2026-10-01..07 National Day holiday; 10-10/11 weekend
DAYS = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8), date(2026, 10, 9),
        date(2026, 10, 12), date(2026, 10, 13)]


@pytest.fixture
def cal():
    return TradingCalendar(reversed(DAYS + [DAYS[0]]))  # unsorted + duplicate


def test_is_trading_day(cal):
    assert cal.is_trading_day(date(2026, 10, 8))
    assert not cal.is_trading_day(date(2026, 10, 1))


def test_next_prev(cal):
    assert cal.next_day(date(2026, 9, 30)) == date(2026, 10, 8)
    assert cal.next_day(date(2026, 10, 3)) == date(2026, 10, 8)
    assert cal.prev_day(date(2026, 10, 8)) == date(2026, 9, 30)
    assert cal.prev_day(date(2026, 10, 11)) == date(2026, 10, 9)
    with pytest.raises(ValueError):
        cal.next_day(date(2026, 10, 13))
    with pytest.raises(ValueError):
        cal.prev_day(date(2026, 9, 29))


def test_between_inclusive(cal):
    assert cal.between(date(2026, 9, 30), date(2026, 10, 9)) == DAYS[1:4]
    assert cal.between(date(2026, 10, 1), date(2026, 10, 7)) == []


def test_offset_and_last(cal):
    assert cal.offset(date(2026, 9, 30), 2) == date(2026, 10, 9)
    assert cal.offset(date(2026, 10, 9), 0) == date(2026, 10, 9)
    assert cal.offset(date(2026, 10, 9), -1) == date(2026, 10, 8)
    with pytest.raises(ValueError):
        cal.offset(date(2026, 10, 1), 1)
    assert cal.offset(date(2026, 10, 12), 5) == date(2026, 10, 13)  # clamped to last()
    assert cal.last() == date(2026, 10, 13)
