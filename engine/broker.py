"""Simulated open-auction broker applying A-share execution rules."""
from datetime import date

from engine.calendar import TradingCalendar
from engine.ledger import Account
from engine.rules import calc_fee, limit_prices, round_lot
from engine.types import Bar, Fill, Pick


class SimBroker:
    def __init__(self, slippage_bps: float = 10.0):
        self.slip = slippage_bps / 10_000

    def execute_day(self, account: Account, d: date, bars: dict[str, Bar], picks: list[Pick],
                    calendar: TradingCalendar, capital_per_pick: float) -> list[Fill]:
        fills: list[Fill] = []
        account.start_day(d)
        # 1) exits: only positions unlocked today (bought before d)
        for code, pos in list(account.positions.items()):
            bar = bars.get(code)
            if pos.available <= 0 or bar is None or bar.volume <= 0:
                continue  # locked by T+1, or suspended
            _, down = limit_prices(bar.code, bar.name, bar.prev_close)
            if bar.high <= down:
                continue  # sealed at limit-down all day: cannot sell
            if d >= pos.exit_date:
                price, reason = bar.open, "到期"
            elif bar.low <= pos.stop:
                price, reason = min(bar.open, pos.stop), "止损"
            elif bar.high >= pos.target:
                price, reason = max(bar.open, pos.target), "止盈"
            else:
                continue
            fills.append(account.sell(d, code, price * (1 - self.slip),
                                      pos.available, reason))
        # 2) entries at the open
        for pick in picks:
            if pick.code in account.positions:
                continue
            bar = bars.get(pick.code)
            if bar is None or bar.volume <= 0:
                continue
            up, _ = limit_prices(bar.code, bar.name, bar.prev_close)
            if bar.open >= up:
                continue  # 开盘涨停, cannot buy
            if bar.open > pick.entry_max:
                continue  # gapped above the buy zone
            price = bar.open * (1 + self.slip)
            qty = round_lot(min(capital_per_pick, account.cash * 0.999) / price)
            # 0.1% buffer may not cover the 5-yuan minimum commission: shrink until affordable
            while qty >= 100 and price * qty + calc_fee("BUY", price * qty) > account.cash:
                qty -= 100
            if qty < 100:
                continue
            fills.append(account.buy(d, pick.code, pick.name or bar.name, price, qty, pick.stop,
                                     pick.target, calendar.offset(d, pick.hold_days), pick.reason))
        return fills
