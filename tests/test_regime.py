import datetime as dt

import pandas as pd
import pytest

from engine import regime
from engine.regime import BULL, BEAR, MA_WINDOW, SIDEWAYS, UNKNOWN


def _series(base, rets):
    """从 base 起按日收益率生成收盘序列。"""
    closes = [base]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    idx = pd.bdate_range(end=pd.Timestamp(dt.date(2026, 11, 13)), periods=len(closes))
    return pd.Series(closes, index=idx)


def test_insufficient_data_returns_unknown():
    s = _series(100.0, [0.0] * 5)
    assert regime.classify(s).state == UNKNOWN


def test_bull_above_ma_with_positive_momentum():
    # MA120 基准 100；近 20 日累计涨 5%
    rets = [0.0] * (MA_WINDOW - 20) + [0.0025] * 20  # 1.0025^20 ≈ 1.051
    s = _series(100.0, rets)
    r = regime.classify(s)
    assert r.state == BULL
    assert r.above_ma is True
    assert r.ret_short == pytest.approx(0.05, abs=0.01)


def test_bear_below_ma_with_negative_momentum():
    rets = [0.0] * (MA_WINDOW - 20) + [-0.003] * 20  # ≈ -5.8%
    s = _series(100.0, rets)
    r = regime.classify(s)
    assert r.state == BEAR
    assert r.above_ma is False


def test_sideways_above_ma_but_momentum_flat():
    rets = [0.0] * (MA_WINDOW - 20) + [0.0005] * 20  # +1%，不到 3%
    s = _series(100.0, rets)
    r = regime.classify(s)
    assert r.state == SIDEWAYS
    assert r.above_ma is True


def test_sideways_below_ma_but_momentum_flat():
    rets = [-0.0005] * MA_WINDOW  # 慢跌
    s = _series(100.0, rets)
    r = regime.classify(s)
    assert r.state == SIDEWAYS


def test_cn_mapping():
    assert regime.cn(BULL) == "牛市" and regime.cn(BEAR) == "熊市"
    assert regime.cn(SIDEWAYS) == "震荡" and regime.cn(UNKNOWN) == "未知"


def test_detect_writes_kv(monkeypatch):
    from engine.store import Store

    s = _series(100.0, [0.0] * (MA_WINDOW - 20) + [0.003] * 20)
    store = Store(":memory:")

    class Cache:
        def load_index(self, symbol):
            return s

        def save_index(self, symbol, series):
            pass

    reg = regime.detect(store, Cache(), client=None)
    assert reg is not None and reg.state == BULL
    latest = regime.latest(store)
    assert latest is not None and latest.state == BULL and latest.asof == reg.asof
    by_day = store.kv_get(f"regime:{reg.asof.isoformat()}")
    assert by_day["state"] == BULL


def test_detect_falls_back_to_client_when_cache_empty(monkeypatch):
    from engine.store import Store

    s = _series(100.0, [0.0] * (MA_WINDOW - 20) + [0.003] * 20)
    store = Store(":memory:")
    saved = []

    class Cache:
        def load_index(self, symbol):
            return pd.Series(dtype=float)

        def save_index(self, symbol, series):
            saved.append((symbol, series))

    class Client:
        def index_daily(self, symbol):
            return s

    reg = regime.detect(store, Cache(), client=Client())
    assert reg is not None and reg.state == BULL
    assert saved  # 客户端数据写回了缓存


def test_detect_returns_none_when_no_data(monkeypatch):
    from engine.store import Store

    store = Store(":memory:")

    class Cache:
        def load_index(self, symbol):
            return pd.Series(dtype=float)

    assert regime.detect(store, Cache(), client=None) is None
    assert regime.latest(store) is None


def test_summary_line_format():
    reg = regime.Regime(state=BEAR, asof=dt.date(2026, 11, 13), close=4000.0,
                        ma_long=4100.0, ret_short=-0.08, above_ma=False)
    line = regime.summary_line(reg)
    assert "熊市" in line and "4,000" in line and "-8.00%" in line
    assert regime.summary_line(None) is None


def test_regime_round_trip_dict():
    reg = regime.Regime(state=SIDEWAYS, asof=dt.date(2026, 11, 13), close=5000.0,
                        ma_long=4900.0, ret_short=0.01, above_ma=True)
    d = reg.to_dict()
    assert d["asof"] == "2026-11-13"
    back = regime.Regime.from_dict(d)
    assert back.state == SIDEWAYS and back.close == 5000.0
