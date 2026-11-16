"""市场状态（行情 regime）识别：基于沪深300相对长期均线的位置 + 短期动量，把市场分成 牛/熊/震荡。

状态每日在结算时落库到 kv（regime:{date} 与 regime:latest），供盘前扫描、复盘、研究使用。
只提供行情标签，不做交易决策；策略切换由人工或上层规则决定。"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass

import pandas as pd

log = logging.getLogger(__name__)

BULL = "bull"
BEAR = "bear"
SIDEWAYS = "sideways"
UNKNOWN = "unknown"

MA_WINDOW = 120       # 长期均线窗口（交易日），约半年
RET_WINDOW = 20       # 短期动量窗口（交易日），约一个月
BULL_RET = 0.03       # 站上长期均线且短期收益 > 此值 → 牛市
BEAR_RET = -0.03      # 跌破长期均线且短期收益 < 此值 → 熊市
INDEX_SYMBOL = "sh000300"
LATEST_KEY = "regime:latest"

_CN = {BULL: "牛市", BEAR: "熊市", SIDEWAYS: "震荡", UNKNOWN: "未知"}


@dataclass
class Regime:
    state: str
    asof: dt.date
    close: float
    ma_long: float
    ret_short: float
    above_ma: bool

    @property
    def cn(self) -> str:
        return _CN.get(self.state, "未知")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["asof"] = self.asof.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Regime":
        return cls(state=d["state"], asof=dt.date.fromisoformat(d["asof"]),
                   close=float(d["close"]), ma_long=float(d["ma_long"]),
                   ret_short=float(d["ret_short"]), above_ma=bool(d["above_ma"]))


def classify(series: pd.Series) -> Regime:
    """根据指数日线序列（按日期升序的收盘价）计算行情状态。数据不足时返回 unknown。"""
    s = series.dropna()
    asof = s.index[-1].date() if not s.empty else dt.date.today()
    if len(s) < MA_WINDOW + 1:
        return Regime(state=UNKNOWN, asof=asof, close=float("nan"),
                      ma_long=float("nan"), ret_short=float("nan"), above_ma=False)
    close = float(s.iloc[-1])
    ma_long = float(s.iloc[-MA_WINDOW:].mean())
    ret_short = close / float(s.iloc[-RET_WINDOW - 1]) - 1.0
    above_ma = close > ma_long
    if above_ma and ret_short > BULL_RET:
        state = BULL
    elif not above_ma and ret_short < BEAR_RET:
        state = BEAR
    else:
        state = SIDEWAYS
    return Regime(state=state, asof=asof, close=close, ma_long=ma_long,
                  ret_short=ret_short, above_ma=above_ma)


def detect(store, cache, client=None, asof: dt.date | None = None) -> Regime | None:
    """加载指数日线、计算状态并写入 kv。client 为 None 时只用缓存（测试/回补）。"""
    try:
        series = cache.load_index(INDEX_SYMBOL)
    except Exception:  # noqa: BLE001 - 缓存缺失时不阻塞
        series = pd.Series(dtype=float)
    if series.empty and client is not None:
        try:
            series = client.index_daily(INDEX_SYMBOL)
            cache.save_index(INDEX_SYMBOL, series)
        except Exception as e:  # noqa: BLE001
            log.warning("regime index fetch failed: %r", e)
    series = series.dropna()
    if asof is not None:
        ts = pd.Timestamp(asof)
        series = series[series.index <= ts]
    if len(series) < MA_WINDOW + 1:
        log.warning("regime not enough data (have %d)", len(series))
        return None
    reg = classify(series)
    store.kv_set(f"regime:{reg.asof.isoformat()}", reg.to_dict())
    store.kv_set(LATEST_KEY, reg.to_dict())
    return reg


def latest(store) -> Regime | None:
    d = store.kv_get(LATEST_KEY)
    return Regime.from_dict(d) if isinstance(d, dict) else None


def cn(state: str) -> str:
    return _CN.get(state, "未知")


def summary_line(reg: Regime | None) -> str | None:
    if reg is None:
        return None
    return (f"行情状态：{reg.cn}（{reg.close:,.0f}，{MA_WINDOW}日均线 {reg.ma_long:,.0f}，"
            f"{RET_WINDOW}日涨跌 {reg.ret_short:+.2%}）")
