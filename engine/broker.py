"""Simulated open-auction broker applying A-share execution rules."""
import math
from datetime import date

from engine.calendar import TradingCalendar
from engine.ledger import Account, Position
from engine.rules import at_limit_down, at_limit_up, calc_fee, limit_prices, round_lot
from engine.types import Bar, Fill, Pick


def _valid(bar: Bar | None) -> bool:
    """A bar is tradable only with volume > 0 and finite, positive open/high/low/prev_close."""
    if bar is None or not bar.volume > 0:
        return False
    return all(math.isfinite(v) and v > 0 for v in (bar.open, bar.high, bar.low, bar.prev_close))


class SimBroker:
    def __init__(self, slippage_bps: float = 10.0):
        self.slip = slippage_bps / 10_000

    def execute_day(self, account: Account, d: date, bars: dict[str, Bar], picks: list[Pick],
                    calendar: TradingCalendar, capital_per_pick: float) -> list[Fill]:
        """Simulate trading day ``d`` for ``account`` in three steps.

        0. ``account.start_day(d)`` unlocks positions bought before ``d`` (T+1). A bar is valid only
           if volume > 0 and open/high/low/prev_close are finite and positive; otherwise the stock
           is treated as suspended and nothing trades in it. Limits come from
           ``limit_prices(..., d)``, i.e. the rules in force on ``d``.
        1. Open sells. A position whose high is at limit-down (``at_limit_down``) was sealed all
           day and cannot be sold. Otherwise, in priority order: expiry (``d >= exit_date``) sells
           at the open; open <= stop sells at the open (stop-loss); open >= target sells at the
           open (take-profit). Positions that only touch stop or target intraday are deferred to
           step 3. Cash from step 1 is available for today's buys.
        2. Open buys, in pick order, at the open: skip codes already held, invalid bars, an open at
           limit-up (``at_limit_up``) and an open above ``entry_max``. Quantity is whole lots,
           sized so cash (including the 5-yuan minimum commission) never goes negative;
           ``exit_date = calendar.offset(d, hold_days)``.
        3. Intraday sells of the deferred positions: low <= stop sells at the stop price (when both
           stop and target were touched the intraday path is unknown, so the stop wins); otherwise
           at the target price. This cash is not available to step 2.

        Sell prices are multiplied by (1 - slippage), buy prices by (1 + slippage), and every fill
        price is then clipped into [limit_down, limit_up].
        """
        fills: list[Fill] = []
        account.start_day(d)
        deferred: list[tuple[Position, float, float, float]] = []  # (position, low, up, down)
        # 1) open sells: only positions unlocked today (bought before d)
        for pos in list(account.positions.values()):
            bar = bars.get(pos.code)
            if pos.available <= 0 or not _valid(bar):
                continue  # locked by T+1, or suspended / bad data
            up, down = limit_prices(bar.code, bar.name, bar.prev_close, d)
            if at_limit_down(bar.high, down):
                continue  # sealed at limit-down all day: cannot sell
            if d >= pos.exit_date:
                reason = "到期"
            elif bar.open <= pos.stop:
                reason = "止损"
            elif bar.open >= pos.target:
                reason = "止盈"
            else:
                if bar.low <= pos.stop or bar.high >= pos.target:
                    deferred.append((pos, bar.low, up, down))
                continue
            fills.append(self._sell(account, d, pos, bar.open, reason, up, down))
        # 2) open buys
        for pick in picks:
            if pick.code in account.positions:
                continue
            bar = bars.get(pick.code)
            if not _valid(bar):
                continue
            up, down = limit_prices(bar.code, bar.name, bar.prev_close, d)
            if at_limit_up(bar.open, up):
                continue  # 开盘涨停, cannot buy
            if bar.open > pick.entry_max:
                continue  # gapped above the buy zone
            price = min(max(bar.open * (1 + self.slip), down), up)
            qty = round_lot(min(capital_per_pick, account.cash * 0.999) / price)
            # 0.1% buffer may not cover the 5-yuan minimum commission: shrink until affordable
            while qty >= 100 and price * qty + calc_fee("BUY", price * qty, d) > account.cash:
                qty -= 100
            if qty < 100:
                continue
            fills.append(account.buy(d, pick.code, pick.name or bar.name, price, qty, pick.stop,
                                     pick.target, calendar.offset(d, pick.hold_days), pick.reason))
        # 3) intraday sells deferred from step 1 (stop wins when both levels were touched)
        for pos, low, up, down in deferred:
            if low <= pos.stop:
                fills.append(self._sell(account, d, pos, pos.stop, "止损", up, down))
            else:
                fills.append(self._sell(account, d, pos, pos.target, "止盈", up, down))
        return fills

    def _sell(self, account: Account, d: date, pos: Position, price: float, reason: str,
              up: float, down: float) -> Fill:
        price = min(max(price * (1 - self.slip), down), up)
        return account.sell(d, pos.code, price, pos.available, reason)
