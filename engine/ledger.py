"""Simulated account ledger with A-share T+1 settlement."""
from dataclasses import asdict, dataclass, field
from datetime import date

from engine.rules import calc_fee
from engine.types import Fill


class InsufficientCash(Exception):
    pass


class NotAvailable(Exception):
    pass


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

    def start_day(self, d: date) -> None:
        """T+1 unlock: everything held before today becomes sellable."""
        for p in self.positions.values():
            p.available = p.qty

    def buy(self, d: date, code: str, name: str, price: float, qty: int, stop: float,
            target: float, exit_date: date, reason: str = "") -> Fill:
        if qty <= 0 or qty % 100:
            raise ValueError(f"qty must be a positive multiple of 100, got {qty}")
        amount = price * qty
        fee = calc_fee("BUY", amount)
        if amount + fee > self.cash + 1e-9:
            raise InsufficientCash(f"{self.name}: need {amount + fee:.2f}, have {self.cash:.2f}")
        self.cash -= amount + fee
        pos = self.positions.get(code)
        if pos is None:
            self.positions[code] = Position(code, name, qty, 0, (amount + fee) / qty, d,
                                            stop, target, exit_date)
        else:  # add-on: new shares locked until tomorrow, cost averaged
            pos.cost = (pos.cost * pos.qty + amount + fee) / (pos.qty + qty)
            pos.qty += qty
            pos.stop, pos.target, pos.exit_date = stop, target, exit_date
        fill = Fill(d, code, "BUY", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def sell(self, d: date, code: str, price: float, qty: int, reason: str) -> Fill:
        pos = self.positions.get(code)
        if pos is None or qty <= 0 or qty > pos.available:
            avail = pos.available if pos else 0
            raise NotAvailable(f"{self.name}: sell {code} x{qty}, available {avail}")
        amount = price * qty
        fee = calc_fee("SELL", amount)
        self.cash += amount - fee
        pos.qty -= qty
        pos.available -= qty
        if pos.qty == 0:
            del self.positions[code]
        fill = Fill(d, code, "SELL", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(c, p.cost) for c, p in self.positions.items())

    def to_dict(self) -> dict:
        positions = {}
        for c, p in self.positions.items():
            d = asdict(p)
            d["entry_date"] = p.entry_date.isoformat()
            d["exit_date"] = p.exit_date.isoformat()
            positions[c] = d
        return {"name": self.name, "cash": self.cash, "positions": positions,
                "trades": [t.to_dict() for t in self.trades]}

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        positions = {}
        for c, p in d.get("positions", {}).items():
            positions[c] = Position(
                code=p["code"], name=p["name"], qty=int(p["qty"]), available=int(p["available"]),
                cost=float(p["cost"]), entry_date=date.fromisoformat(p["entry_date"]),
                stop=float(p["stop"]), target=float(p["target"]),
                exit_date=date.fromisoformat(p["exit_date"]))
        return cls(name=d["name"], cash=float(d["cash"]), positions=positions,
                   trades=[Fill.from_dict(t) for t in d.get("trades", [])])
