"""Simulated open-auction broker applying A-share execution rules."""
import math
from datetime import date

from engine.calendar import TradingCalendar
from engine.ledger import Account, Position
from engine.rules import at_limit_down, at_limit_up, calc_fee, limit_prices, round_lot
from engine.types import Bar, Fill, Pick

Band = tuple[float, float] | None  # (limit_up, limit_down), or None when the band is unreliable


def _valid(bar: Bar | None) -> bool:
    """A bar is tradable only with volume > 0 and finite, positive open/high/low/prev_close."""
    if bar is None or not bar.volume > 0:
        return False
    return all(math.isfinite(v) and v > 0 for v in (bar.open, bar.high, bar.low, bar.prev_close))


def _band(bar: Bar, d: date) -> Band:
    """Limit band for ``bar`` on ``d``, or None if the day's prices show the band is wrong.

    The band is trusted only if open, high and low all lie within [limit_down - 0.01,
    limit_up + 0.01] (one tick for forward-adjusted rounding). A price outside it means the band
    itself is off, e.g. an ex-rights day whose prev_close is still the pre-split close, or an ST
    status taken from today's name.
    """
    up, down = limit_prices(bar.code, bar.name, bar.prev_close, d)
    lo, hi = round(down * 100) - 1, round(up * 100) + 1
    if all(lo <= round(v * 100) <= hi for v in (bar.open, bar.high, bar.low)):
        return up, down
    return None


def _clip(price: float, band: Band) -> float:
    return price if band is None else min(max(price, band[1]), band[0])


class SimBroker:
    def __init__(self, slippage_bps: float = 10.0):
        self.slip = slippage_bps / 10_000

    def execute_day(self, account: Account, d: date, bars: dict[str, Bar], picks: list[Pick],
                    calendar: TradingCalendar, capital_per_pick: float) -> list[Fill]:
        """Simulate trading day ``d`` for ``account`` in three steps.

        Each day runs once per account: if ``d`` equals ``account.last_trade_day`` this returns
        ``[]`` without touching the account; a ``d`` before it raises ValueError. A completed run
        sets ``last_trade_day = d``.

        0. ``account.start_day(d)`` unlocks positions bought before ``d`` (T+1). A bar is valid only
           if volume > 0 and open/high/low/prev_close are finite and positive; otherwise the stock
           is treated as suspended and nothing trades in it. Limits come from
           ``limit_prices(..., d)``, i.e. the rules in force on ``d``. The band is unreliable when
           open, high or low lies outside [limit_down - 0.01, limit_up + 0.01] (e.g. an ex-rights
           day with a stale prev_close); then no limit check is made and fill prices are not
           clipped.
        1. Open sells. A position whose high is at limit-down (``at_limit_down``) was sealed all
           day and cannot be sold. Otherwise, in priority order: expiry (``d >= exit_date``) sells
           at the open; open <= stop sells at the open (stop-loss); open >= target sells at the
           open (take-profit). Positions that only touch stop or target intraday are deferred to
           step 3. Cash from step 1 is available for today's buys.
        2. Open buys, in pick order, at the open: skip codes already held, invalid bars, an open at
           limit-up (``at_limit_up``), an open above ``entry_max`` and an open at or below the
           pick's stop. Quantity is whole lots, sized so cash (including the 5-yuan minimum
           commission) never goes negative; ``exit_date = calendar.offset(d, hold_days)``.
        3. Intraday sells of the deferred positions: low <= stop sells at the stop price (when both
           stop and target were touched the intraday path is unknown, so the stop wins); otherwise
           at the target price. This cash is not available to step 2.

        Sell prices are multiplied by (1 - slippage), buy prices by (1 + slippage), and every fill
        price is then clipped into [limit_down, limit_up] when the band is reliable. Slippage can
        put a fill slightly outside the day's [low, high]; that is a deliberate, conservative cost
        assumption.
        """
        last = account.last_trade_day
        if last is not None and d < last:
            raise ValueError(f"{account.name}: {d} is before last trade day {last}")
        if d == last:
            return []  # already executed today
        fills: list[Fill] = []
        account.start_day(d)
        deferred: list[tuple[Position, float, Band]] = []  # (position, low, band)
        # 1) open sells: only positions unlocked today (bought before d)
        for pos in list(account.positions.values()):
            bar = bars.get(pos.code)
            if pos.available <= 0 or not _valid(bar):
                continue  # locked by T+1, or suspended / bad data
            band = _band(bar, d)
            if band and at_limit_down(bar.high, band[1]):
                continue  # sealed at limit-down all day: cannot sell
            if d >= pos.exit_date:
                reason = "到期"
            elif bar.open <= pos.stop:
                reason = "止损"
            elif bar.open >= pos.target:
                reason = "止盈"
            else:
                if bar.low <= pos.stop or bar.high >= pos.target:
                    deferred.append((pos, bar.low, band))
                continue
            fills.append(self._sell(account, d, pos, bar.open, reason, band))
        # 2) open buys
        for pick in picks:
            if pick.code in account.positions:
                continue
            bar = bars.get(pick.code)
            if not _valid(bar):
                continue
            band = _band(bar, d)
            if band and at_limit_up(bar.open, band[0]):
                continue  # 开盘涨停, cannot buy
            if bar.open > pick.entry_max:
                continue  # gapped above the buy zone
            if bar.open <= pick.stop:
                continue  # opened at or below the stop: the plan is already broken
            price = _clip(bar.open * (1 + self.slip), band)
            qty = round_lot(min(capital_per_pick, account.cash * 0.999) / price)
            # 0.1% buffer may not cover the 5-yuan minimum commission: shrink until affordable
            while qty >= 100 and price * qty + calc_fee("BUY", price * qty, d) > account.cash:
                qty -= 100
            if qty < 100:
                continue
            fills.append(account.buy(d, pick.code, pick.name or bar.name, price, qty, pick.stop,
                                     pick.target, calendar.offset(d, pick.hold_days), pick.reason))
        # 3) intraday sells deferred from step 1 (stop wins when both levels were touched)
        for pos, low, band in deferred:
            if low <= pos.stop:
                fills.append(self._sell(account, d, pos, pos.stop, "止损", band))
            else:
                fills.append(self._sell(account, d, pos, pos.target, "止盈", band))
        account.last_trade_day = d
        return fills

    def _sell(self, account: Account, d: date, pos: Position, price: float, reason: str,
              band: Band) -> Fill:
        return account.sell(d, pos.code, _clip(price * (1 - self.slip), band), pos.available, reason)
