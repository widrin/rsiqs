"""Simulated account ledger with A-share T+1 settlement."""
import math
from dataclasses import asdict, dataclass, field
from datetime import date

from engine.rules import DIVIDEND_TAX_RATE, calc_fee
from engine.types import Fill


class InsufficientCash(Exception):
    pass


class NotAvailable(Exception):
    pass


def _check_price(price: float) -> None:
    if not (math.isfinite(price) and price > 0):
        raise ValueError(f"price must be finite and > 0, got {price!r}")


def ex_rights_price(price: float, cash_per_share: float, share_ratio: float) -> float:
    """除权除息参考价：(price - 每股派息) / (1 + 每股送转)。不建模配股；结果不低于 0.01。"""
    return max((price - cash_per_share) / (1.0 + share_ratio), 0.01)


@dataclass
class Position:
    code: str
    name: str
    qty: int
    available: int
    cost: float  # average cost per share, fees included
    entry_date: date
    stop: float
    target: float
    exit_date: date


@dataclass
class Account:
    name: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Fill] = field(default_factory=list)
    last_trade_day: date | None = None  # last day SimBroker.execute_day completed for this account
    corporate_actions: list[dict] = field(default_factory=list)  # applied ex-rights/ex-dividend events

    def start_day(self, d: date) -> None:
        """T+1 unlock: only positions bought before ``d`` become sellable. Calling it again on the
        buy day (or twice on the same day) keeps today's purchases locked."""
        for p in self.positions.values():
            p.available = p.qty if p.entry_date < d else 0

    def buy(self, d: date, code: str, name: str, price: float, qty: int, stop: float,
            target: float, exit_date: date, reason: str = "") -> Fill:
        if qty <= 0 or qty % 100:
            raise ValueError(f"qty must be a positive multiple of 100, got {qty}")
        if code in self.positions:
            raise ValueError(f"{self.name}: already holding {code}; adding to a position is not modelled")
        _check_price(price)
        amount = price * qty
        fee = calc_fee("BUY", amount, d)
        if amount + fee > self.cash + 1e-9:
            raise InsufficientCash(f"{self.name}: need {amount + fee:.2f}, have {self.cash:.2f}")
        self.cash -= amount + fee
        self.positions[code] = Position(code, name, qty, 0, (amount + fee) / qty, d,
                                        stop, target, exit_date)
        fill = Fill(d, code, "BUY", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def sell(self, d: date, code: str, price: float, qty: int, reason: str) -> Fill:
        _check_price(price)
        pos = self.positions.get(code)
        if pos is None or qty <= 0 or qty > pos.available:
            avail = pos.available if pos else 0
            raise NotAvailable(f"{self.name}: sell {code} x{qty}, available {avail}")
        amount = price * qty
        fee = calc_fee("SELL", amount, d)
        self.cash += amount - fee
        pos.qty -= qty
        pos.available -= qty
        if pos.qty == 0:
            del self.positions[code]
        fill = Fill(d, code, "SELL", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def apply_dividend(self, d: date, code: str, cash_per_share: float, share_ratio: float,
                       tax_rate: float = DIVIDEND_TAX_RATE) -> dict | None:
        """Apply an ex-rights / ex-dividend event on ex-date ``d`` to the position in ``code``.

        Shares grow by ``share_ratio`` (bonus + transfer shares per share, rounded down to whole shares);
        the cash dividend (``cash_per_share`` before tax) is credited after ``tax_rate``; cost, stop and
        target move to the ex-rights reference price. Returns the recorded event, or None when there is
        no such position or the event was already applied (re-running a settlement day is safe).
        """
        for v in (cash_per_share, share_ratio):
            if not (math.isfinite(v) and v >= 0):
                raise ValueError(f"dividend values must be finite and >= 0, got {cash_per_share!r}, {share_ratio!r}")
        if cash_per_share == 0 and share_ratio == 0:
            raise ValueError("empty dividend event")
        pos = self.positions.get(code)
        if pos is None or any(a["code"] == code and a["date"] == d.isoformat() for a in self.corporate_actions):
            return None
        qty0 = pos.qty
        qty1 = int(qty0 * (1.0 + share_ratio) + 1e-6)  # 1900 * 1.3 must not floor to 2469
        net = round(qty0 * cash_per_share * (1.0 - tax_rate), 2)
        self.cash += net
        pos.cost = (pos.cost * qty0 - net) / qty1
        pos.qty, pos.available = qty1, int(pos.available * (1.0 + share_ratio) + 1e-6)
        pos.stop = ex_rights_price(pos.stop, cash_per_share, share_ratio)
        pos.target = ex_rights_price(pos.target, cash_per_share, share_ratio)
        act = {"date": d.isoformat(), "code": code, "cash_per_share": cash_per_share, "share_ratio": share_ratio,
               "qty_before": qty0, "qty_after": qty1, "cash": net}
        self.corporate_actions.append(act)
        return act

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(c, p.cost) for c, p in self.positions.items())

    def to_dict(self) -> dict:
        positions = {}
        for c, p in self.positions.items():
            d = asdict(p)
            d["entry_date"] = p.entry_date.isoformat()
            d["exit_date"] = p.exit_date.isoformat()
            positions[c] = d
        last = self.last_trade_day.isoformat() if self.last_trade_day else None
        return {"name": self.name, "cash": self.cash, "positions": positions,
                "trades": [t.to_dict() for t in self.trades], "last_trade_day": last,
                "corporate_actions": [dict(a) for a in self.corporate_actions]}

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        positions = {}
        for c, p in d.get("positions", {}).items():
            positions[c] = Position(
                code=p["code"], name=p["name"], qty=int(p["qty"]), available=int(p["available"]),
                cost=float(p["cost"]), entry_date=date.fromisoformat(p["entry_date"]),
                stop=float(p["stop"]), target=float(p["target"]),
                exit_date=date.fromisoformat(p["exit_date"]))
        last = d.get("last_trade_day")  # absent in accounts saved before this field existed
        return cls(name=d["name"], cash=float(d["cash"]), positions=positions,
                   trades=[Fill.from_dict(t) for t in d.get("trades", [])],
                   last_trade_day=date.fromisoformat(last) if last else None,
                   corporate_actions=[dict(a) for a in d.get("corporate_actions", [])])
