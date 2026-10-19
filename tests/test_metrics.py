import math

import pandas as pd
import pytest

from engine import metrics as m


def _s(vals, start="2026-01-05"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def test_daily_returns():
    r = m.daily_returns(_s([100, 110, 99]))
    assert list(r.round(10)) == [0.1, -0.1]


def test_annualized_return():
    r = _s([0.01] * 252)
    assert m.annualized_return(r) == pytest.approx(1.01 ** 252 - 1)
    assert m.annualized_return(_s([])) == 0.0
    assert m.annualized_return(_s([-0.5, -0.6, 0.1])) == pytest.approx(0.22 ** (252 / 3) - 1)
    assert m.annualized_return(_s([0.1, -1.0])) == -1.0  # 净值归零


def test_sharpe_hand_computed():
    r = _s([0.01, -0.01, 0.02, 0.0])
    mean, std = 0.005, math.sqrt((0.005**2 + 0.015**2 + 0.015**2 + 0.005**2) / 3)
    assert m.sharpe(r) == pytest.approx(mean / std * math.sqrt(252))
    assert m.sharpe(_s([0.01, 0.01, 0.01])) == 0.0
    assert m.sharpe(_s([0.1, 0.1, 0.1])) == 0.0  # pandas 给出 ~1.7e-17 的浮点噪声 std


def test_max_drawdown():
    assert m.max_drawdown(_s([100, 120, 90, 130, 117])) == pytest.approx(-0.25)
    assert m.max_drawdown(_s([1, 2, 3])) == 0.0


def test_excess_returns_inner_join():
    a = _s([0.02, 0.01, 0.03])
    b = _s([0.01, 0.01], start="2026-01-06")
    ex = m.excess_returns(a, b)
    assert list(ex.index) == list(b.index)
    assert list(ex.round(10)) == [0.0, 0.02]


def test_deflated_sharpe_sanity():
    sr = 0.1  # 日夏普 ≈ 年化 1.59
    one = m.deflated_sharpe(sr, n_trials=1, n_obs=500)
    many = m.deflated_sharpe(sr, n_trials=100, n_obs=500)
    assert 0.0 <= many < one <= 1.0
    assert one > 0.95  # 单次试验、500 个观测，0.1 的日夏普高度显著
    assert m.deflated_sharpe(0.0, 1, 500) == pytest.approx(0.5)
    assert m.deflated_sharpe(0.01, 1000, 500) < 0.5  # 海量试验下弱信号不可信


def test_summarize():
    nav = _s([100, 110, 99, 108.9])
    bench = _s([100, 100, 100, 100])
    s = m.summarize(nav, bench)
    assert set(s) == {"total_return", "ann_return", "sharpe", "max_dd", "excess_ann", "n_days"}
    assert s["total_return"] == pytest.approx(0.089)
    assert s["max_dd"] == pytest.approx(-0.1)
    assert s["n_days"] == 3  # 收益天数：净值第一个点是起点
    assert s["excess_ann"] == pytest.approx((0.1 - 0.1 + 0.1) / 3 * 252)


def test_deflated_sharpe_reference_values():
    # 参考值由独立实现复算（Bailey & Lopez de Prado 2014，kurt 为非超额峰度）
    assert m.deflated_sharpe(0.1, 10, 250, skew=-0.5, kurt=5.0) == pytest.approx(0.5025292790757397, rel=1e-9)
    assert m.deflated_sharpe(0.1, 100, 500) == pytest.approx(0.38456717973214927, rel=1e-9)
    assert m.deflated_sharpe(0.1, 2, 250) < m.deflated_sharpe(0.1, 1, 250)
    # 负偏度让 DSR 更低（A 股策略常见左偏）
    assert m.deflated_sharpe(0.1, 10, 250, skew=-0.5) < m.deflated_sharpe(0.1, 10, 250, skew=0.5)


def test_summarize_with_moving_benchmark():
    nav = _s([100, 103, 101, 106, 104, 109])
    bench = _s([100, 101, 103, 102, 104, 105])
    s = m.summarize(nav, bench)
    r = m.daily_returns(nav)
    assert s["sharpe"] == pytest.approx(m.sharpe(r))
    assert s["ann_return"] == pytest.approx(m.annualized_return(r))
    assert s["excess_ann"] == pytest.approx(float((r - m.daily_returns(bench)).mean() * 252))
    assert s["sharpe"] != pytest.approx(m.sharpe(r - m.daily_returns(bench)))
