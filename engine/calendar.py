"""Trading calendar over a fixed set of trading dates."""
from bisect import bisect_left, bisect_right
from datetime import date
from typing import Iterable


class TradingCalendar:
    def __init__(self, dates: Iterable[date]):
        self._days = sorted(set(dates))
        self._set = set(self._days)
        if not self._days:
            raise ValueError("empty trading calendar")

    def is_trading_day(self, d: date) -> bool:
        return d in self._set

    def next_day(self, d: date) -> date:
        i = bisect_right(self._days, d)
        if i >= len(self._days):
            raise ValueError(f"no trading day after {d}")
        return self._days[i]

    def prev_day(self, d: date) -> date:
        i = bisect_left(self._days, d)
        if i == 0:
            raise ValueError(f"no trading day before {d}")
        return self._days[i - 1]

    def between(self, a: date, b: date) -> list[date]:
        return self._days[bisect_left(self._days, a):bisect_right(self._days, b)]

    def offset(self, d: date, n: int) -> date:
        if d not in self._set:
            raise ValueError(f"{d} is not a trading day")
        # Clamped to the calendar ends so a hold period running past the data end
        # (e.g. last days of a backtest) still yields a valid exit date.
        i = min(max(self._days.index(d) + n, 0), len(self._days) - 1)
        return self._days[i]

    def last(self) -> date:
        return self._days[-1]
