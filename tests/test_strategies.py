# tests/test_strategies.py
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.dataview import DataView
from engine.types import Pick
from strategies.base import list_strategies, load_strategy
from strategies.baseline_momo import STRATEGY, BaselineMomo

DATES = pd.bdate_range("2026-01-05", periods=80)
ASOF = DATES[-1].date()


def series(closes, amount=2e8, dates=DATES):
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes,
                       "volume": 1e5, "amount": amount}, index=dates[-len(closes):])
    df["prev_close"] = df["close"].shift(1).fillna(df["open"])
    return df


def path(r20, r5, n=80):
    """收盘价路径：前段平，最后 20 天总涨幅 r20，其中最后 5 天涨幅 r5。"""
    c = np.full(n, 10.0)
    mid = 10.0 * (1 + r20) / (1 + r5)
    c[-21:-5] = np.linspace(10.0, mid, 16)
    c[-6:] = np.linspace(mid, mid * (1 + r5), 6)
    c[-21] = 10.0
    return c


def limit_up_path(n=80):
    c = np.full(n, 10.0)
    c[-1] = 11.0  # 昨收 10.00，主板涨停价 11.00
    return c


@pytest.fixture
def view():
    panel = {
        "600001": series(path(0.20, -0.02)),  # 20d 最强、5d 最弱 -> 分数最高
        "600002": series(path(0.15, 0.00)),
        "600003": series(path(0.05, 0.05)),
        "600004": series(path(0.10, 0.10)),
        "600005": series(path(0.30, -0.05)),  # ST
        "600006": series(path(0.30, -0.05), amount=5e7),  # 成交额不足
        "600007": series(path(0.30, -0.05, n=40)),  # 历史不足 60 行
        "600008": series(limit_up_path()),  # asof 收于涨停
        "600009": series(path(0.30, -0.05, n=79), dates=DATES[:-1]),  # asof 当日停牌
    }
    names = {c: "股票" + c[-1] for c in panel}
    names["600005"] = "ST某某"
    return DataView(panel, names, asof=ASOF)


def test_path_helper():
    c = path(0.20, -0.02)
    assert c[-1] / c[-21] - 1 == pytest.approx(0.20)
    assert c[-1] / c[-6] - 1 == pytest.approx(-0.02)


def test_filters_and_ordering(view):
    picks = STRATEGY.select(view)
    assert [p.code for p in picks] == ["600001", "600002", "600003", "600004"]
    assert [p.score for p in picks] == [3.0, 1.0, -2.0, -2.0]  # 同分按代码升序


def test_pick_price_fields(view):
    p = STRATEGY.select(view)[0]
    assert isinstance(p, Pick)
    assert p.name == "股票1"
    assert p.entry_max == pytest.approx(12.36)
    assert p.stop == pytest.approx(11.16)
    assert p.target == pytest.approx(13.20)
    assert p.hold_days == 5
    assert "20日涨幅20.0%" in p.reason and "5日涨幅-2.0%" in p.reason


def test_returns_up_to_15_ordered_candidates():
    # 盘前流程最多取前 15 个复核，回测和结算只取前 5 个：策略返回完整的前 15 名
    panel = {f"6001{i:02d}": series(path(0.01 * (i + 1), 0.0)) for i in range(20)}
    v = DataView(panel, {c: "甲" for c in panel}, asof=ASOF)
    picks = BaselineMomo().select(v)
    assert len(picks) == 15
    assert picks[0].code == "600119" and [p.score for p in picks] == sorted((p.score for p in picks), reverse=True)


def test_empty_view():
    assert STRATEGY.select(DataView({}, {}, asof=ASOF)) == []


def test_limit_up_uses_rules_in_force_on_asof():
    # 创业板 2020-08-24 前涨跌幅 10%：收于 +10% 就是涨停，排除；当天起涨跌幅 20%，+10% 不再是涨停
    for asof, expected in ((date(2020, 8, 21), []), (date(2020, 8, 24), ["300001"])):
        dates = pd.bdate_range(end=asof, periods=80)
        v = DataView({"300001": series(limit_up_path(), dates=dates)}, {"300001": "特锐德"}, asof=asof)
        assert [p.code for p in STRATEGY.select(v)] == expected
    # 收盘比涨停价 11.00 低一分钱也按涨停排除（容忍复权舍入）
    c = limit_up_path()
    c[-1] = 10.99
    assert STRATEGY.select(DataView({"600010": series(c)}, {"600010": "股票0"}, asof=ASOF)) == []


def test_load_and_list_strategies():
    assert load_strategy("baseline_momo") is STRATEGY
    assert STRATEGY.name == "baseline_momo"
    names = list_strategies()
    assert "baseline_momo" in names
    assert not {"base", "sandbox", "__init__"} & set(names)
    for bad in ["../evil", "Base", "ab", "base", "sandbox", "x" * 50]:
        with pytest.raises(ValueError):
            load_strategy(bad)


def test_non_finite_values_are_skipped():
    good = series(path(0.20, -0.02))
    nan_close = good.copy()
    nan_close.iloc[-1, nan_close.columns.get_loc("close")] = float("nan")  # asof 当天收盘价缺失
    nan_amount = series(path(0.15, 0.0), amount=float("nan"))           # 成交额缺失：不能当作足够
    nan_prev = series(path(0.12, 0.01))
    nan_prev.iloc[-1, nan_prev.columns.get_loc("prev_close")] = float("nan")  # 前一日收盘价缺失
    nan_hist = series(path(0.10, 0.10))
    nan_hist.iloc[-21, nan_hist.columns.get_loc("close")] = float("nan")  # 20 日前收盘价缺失
    panel = {"600001": good, "600002": nan_close, "600003": nan_amount, "600004": nan_hist, "600005": nan_prev}
    picks = STRATEGY.select(DataView(panel, {c: "股" for c in panel}, asof=ASOF))
    assert [p.code for p in picks] == ["600001"]
