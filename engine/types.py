"""Core value types shared across the engine."""
import datetime
import math
import numbers
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

    def __post_init__(self):
        """Coerce prices to float and hold_days to int; reject values the broker cannot trade.

        entry_max is deliberately not compared with target (a plan may cap the buy price above it).
        """
        for name in ("entry_max", "stop", "target"):
            raw = getattr(self, name)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Pick {self.code}: {name} must be a number, got {raw!r}") from None
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"Pick {self.code}: {name} must be finite and > 0, got {v}")
            object.__setattr__(self, name, v)
        h = self.hold_days
        if (isinstance(h, bool) or not isinstance(h, numbers.Real) or not math.isfinite(h)
                or h != int(h)):
            raise ValueError(f"Pick {self.code}: hold_days must be an integer, got {h!r}")
        object.__setattr__(self, "hold_days", int(h))
        if self.hold_days < 1:
            raise ValueError(f"Pick {self.code}: hold_days must be >= 1, got {self.hold_days}")
        if not self.stop < self.target:
            raise ValueError(f"Pick {self.code}: stop {self.stop} must be below target {self.target}")

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

    def __post_init__(self):
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Fill side must be 'BUY' or 'SELL', got {self.side!r}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fill":
        return cls(date=datetime.date.fromisoformat(d["date"]), code=d["code"], side=d["side"],
                   qty=int(d["qty"]), price=float(d["price"]), fee=float(d["fee"]), reason=d["reason"])
