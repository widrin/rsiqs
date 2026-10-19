"""绩效指标：收益、夏普、回撤、超额、Deflated Sharpe（无 scipy）。"""
from __future__ import annotations

import math
from statistics import NormalDist

import pandas as pd

EULER_GAMMA = 0.5772156649015329


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.astype(float).pct_change().dropna()


def annualized_return(r: pd.Series) -> float:
    if len(r) == 0:
        return 0.0
    growth = float((1.0 + r).prod())
    return growth ** (252.0 / len(r)) - 1.0 if growth > 0 else -1.0


def sharpe(r: pd.Series, periods: int = 252) -> float:
    if len(r) < 2:
        return 0.0
    std = float(r.std(ddof=1))
    if not std > 1e-12:  # 0、浮点噪声或 NaN
        return 0.0
    return float(r.mean()) / std * math.sqrt(periods)


def max_drawdown(nav: pd.Series) -> float:
    if len(nav) == 0:
        return 0.0
    nav = nav.astype(float)
    return float((nav / nav.cummax() - 1.0).min())


def excess_returns(r: pd.Series, bench_r: pd.Series) -> pd.Series:
    a, b = r.align(bench_r, join="inner")
    return a - b


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe(sr: float, n_trials: int, n_obs: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Bailey & Lopez de Prado (2014)。sr 为每期（日）夏普，非年化。
    SR0 = sqrt(1/n_obs) * ((1-g)*Z^-1(1-1/N) + g*Z^-1(1-1/(N e)))，N=1 时 SR0=0。"""
    if n_obs < 2:
        return 0.0
    n = max(int(n_trials), 1)
    if n == 1:
        sr0 = 0.0
    else:
        z = NormalDist().inv_cdf
        sr0 = math.sqrt(1.0 / n_obs) * ((1 - EULER_GAMMA) * z(1 - 1.0 / n) + EULER_GAMMA * z(1 - 1.0 / (n * math.e)))
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    return _norm_cdf((sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def summarize(nav: pd.Series, bench_nav: pd.Series) -> dict:
    r = daily_returns(nav)
    ex = excess_returns(r, daily_returns(bench_nav))
    return {
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1.0) if len(nav) else 0.0,
        "ann_return": annualized_return(r),
        "sharpe": sharpe(r),
        "max_dd": max_drawdown(nav),
        "excess_ann": float(ex.mean() * 252) if len(ex) else 0.0,
        "n_days": int(len(nav)),
    }
