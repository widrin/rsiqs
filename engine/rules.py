"""A-share trading rules: boards, price limits, lots, fees.

Rules that changed over time take an optional trailing ``d``; ``d=None`` means the current rules.
"""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

# 创业板注册制首批股票上市日起涨跌幅 10% -> 20%（深交所《创业板交易特别规定》）
CHINEXT_20PCT_FROM = date(2020, 8, 24)
# 中国结算下调过户费：成交额的 0.002% -> 0.001%，2022-04-29 起执行
TRANSFER_CUT_FROM = date(2022, 4, 29)
# 财政部、税务总局减半征收证券交易印花税：0.1% -> 0.05%，2023-08-28 起执行
STAMP_CUT_FROM = date(2023, 8, 28)
# 沪深交易所 2026-04-24 发布修订后的交易规则：主板 ST 5% -> 10%，2026-07-06 起执行
MAIN_ST_10PCT_FROM = date(2026, 7, 6)


def _in_force(since: date, d: date | None) -> bool:
    if d is None:
        return True
    if isinstance(d, datetime):  # also covers pandas.Timestamp
        d = d.date()
    return d >= since


class Board(Enum):
    MAIN = "main"
    CHINEXT = "chinext"
    STAR = "star"
    BSE = "bse"


def board(code: str) -> Board:
    if code.startswith("30"):  # 300/301/302
        return Board.CHINEXT
    if code.startswith(("688", "689")):
        return Board.STAR
    if code.startswith(("8", "4", "92")):
        return Board.BSE
    return Board.MAIN


def is_st(name: str) -> bool:
    return "ST" in name.upper()


def limit_pct(code: str, name: str = "", d: date | None = None) -> float:
    b = board(code)
    if b is Board.BSE:
        return 0.30
    if b is Board.STAR:
        return 0.20
    if b is Board.CHINEXT:
        if _in_force(CHINEXT_20PCT_FROM, d):
            return 0.20
        return 0.05 if is_st(name) else 0.10
    if is_st(name) and not _in_force(MAIN_ST_10PCT_FROM, d):
        return 0.05
    return 0.10


def limit_prices(code: str, name: str, prev_close: float, d: date | None = None) -> tuple[float, float]:
    """(limit_up, limit_down) rounded half-up to 0.01, computed in Decimal to avoid float drift."""
    pc = Decimal(str(prev_close))
    pct = Decimal(str(limit_pct(code, name, d)))
    cent = Decimal("0.01")
    up = (pc * (1 + pct)).quantize(cent, rounding=ROUND_HALF_UP)
    down = (pc * (1 - pct)).quantize(cent, rounding=ROUND_HALF_UP)
    return float(up), float(down)


def at_limit_up(price: float, up: float) -> bool:
    """True when ``price`` is at the limit-up price, with one tick (0.01) of tolerance.

    Daily bars are forward-adjusted (前复权): adjusted prices are raw prices rescaled and rounded
    to 0.01, so a stock sealed at limit-up can sit one cent away from the limit computed from the
    adjusted prev_close. The tolerance absorbs that rounding. Cash dividends are subtracted rather
    than rescaled, so an old sealed price can also sit above the computed limit by more than a
    cent; any price above it counts as limit-up too (SimBroker additionally requires
    open == high when the day's prices show the computed band is wrong). Effect on raw prices: a
    price exactly one cent below the true limit also counts as limit-up (a buy there is skipped),
    which only makes the simulation slightly more conservative.
    """
    return round(price * 100) >= round(up * 100) - 1


def at_limit_down(price: float, down: float) -> bool:
    """True when ``price`` is at the limit-down price, with one tick (0.01) of tolerance.

    Same reason as ``at_limit_up``: forward-adjusted rounding can leave a sealed limit-down one cent
    off, and any price below the computed limit counts as limit-down. Effect on raw prices: a high
    exactly one cent above the true limit-down also counts as sealed (the sell is skipped), again
    erring on the conservative side.
    """
    return round(price * 100) <= round(down * 100) + 1


def round_lot(qty: float) -> int:
    """Floor to a multiple of 100 shares. Simplification: STAR's 200-share minimum
    and 1-share increments are not modelled; all boards use 100-share lots."""
    if qty <= 0:
        return 0
    return int(qty // 100) * 100


COMMISSION_RATE = 0.00025
COMMISSION_MIN = 5.0
TRANSFER_RATE = 0.00001  # from TRANSFER_CUT_FROM
TRANSFER_RATE_OLD = 0.00002
STAMP_RATE = 0.0005  # from STAMP_CUT_FROM, sell side only
STAMP_RATE_OLD = 0.001
# 财税〔2015〕101 号：持股 1 个月以内，股息红利所得全额计税，税率 20%。模拟盘持仓都很短，一律按此扣税
DIVIDEND_TAX_RATE = 0.20


def calc_fee(side: str, amount: float, d: date | None = None) -> float:
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    transfer = TRANSFER_RATE if _in_force(TRANSFER_CUT_FROM, d) else TRANSFER_RATE_OLD
    fee = max(amount * COMMISSION_RATE, COMMISSION_MIN) + amount * transfer
    if side == "SELL":
        fee += amount * (STAMP_RATE if _in_force(STAMP_CUT_FROM, d) else STAMP_RATE_OLD)
    return round(fee, 2)
