"""A-share trading rules: boards, price limits, lots, fees."""
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Board(Enum):
    MAIN = "main"
    CHINEXT = "chinext"
    STAR = "star"
    BSE = "bse"


def board(code: str) -> Board:
    if code.startswith(("300", "301")):
        return Board.CHINEXT
    if code.startswith(("688", "689")):
        return Board.STAR
    if code.startswith(("8", "4", "92")):
        return Board.BSE
    return Board.MAIN


def is_st(name: str) -> bool:
    return "ST" in name.upper()


def limit_pct(code: str, name: str = "") -> float:
    b = board(code)
    if b is Board.BSE:
        return 0.30
    if b in (Board.CHINEXT, Board.STAR):
        return 0.20
    return 0.05 if is_st(name) else 0.10


def limit_prices(code: str, name: str, prev_close: float) -> tuple[float, float]:
    """(limit_up, limit_down) rounded half-up to 0.01, computed in Decimal to avoid float drift."""
    pc = Decimal(str(prev_close))
    pct = Decimal(str(limit_pct(code, name)))
    cent = Decimal("0.01")
    up = (pc * (1 + pct)).quantize(cent, rounding=ROUND_HALF_UP)
    down = (pc * (1 - pct)).quantize(cent, rounding=ROUND_HALF_UP)
    return float(up), float(down)


def round_lot(qty: float) -> int:
    """Floor to a multiple of 100 shares. Simplification: STAR's 200-share minimum
    and 1-share increments are not modelled; all boards use 100-share lots."""
    if qty <= 0:
        return 0
    return int(qty // 100) * 100


COMMISSION_RATE = 0.00025
COMMISSION_MIN = 5.0
TRANSFER_RATE = 0.00001
STAMP_RATE = 0.0005


def calc_fee(side: str, amount: float) -> float:
    fee = max(amount * COMMISSION_RATE, COMMISSION_MIN) + amount * TRANSFER_RATE
    if side == "SELL":
        fee += amount * STAMP_RATE
    return round(fee, 2)
