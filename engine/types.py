"""Core value types shared across the engine."""
import datetime
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class Bar:
    code: str
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: float
    amount: float
    name: str = ""


@dataclass(frozen=True)
class Pick:
    code: str
    name: str
    score: float
    entry_max: float
    stop: float
    target: float
    hold_days: int
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Pick":
        return cls(**{f.name: d[f.name] for f in fields(cls) if f.name in d})


@dataclass(frozen=True)
class Fill:
    date: datetime.date
    code: str
    side: str  # "BUY" | "SELL"
    qty: int
    price: float
    fee: float
    reason: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fill":
        return cls(date=datetime.date.fromisoformat(d["date"]), code=d["code"], side=d["side"],
                   qty=int(d["qty"]), price=float(d["price"]), fee=float(d["fee"]), reason=d["reason"])
