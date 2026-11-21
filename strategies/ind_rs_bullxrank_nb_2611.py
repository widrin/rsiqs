"""假设：250日回撤中位数是慢变量，市场进入牛市修复时仍可能处于深度回撤区，刹车可能错过强行业反弹。本稿与 ind_rs_bullxrank_2611 除刹车生效范围外完全一致：先用全池个股 rel20、rel60 百分位排名，再按行业取均值并等权相加，选前5强行业；个股 rel20 排名减5日反转排名，每日1只，1.5σ止损、3σ止盈、5日持有，单行业≤2只。唯一差异是 dd_med 0.30/0.22 刹车只在非牛市代理状态生效，牛市代理状态照常建仓；该代理只用于隔离刹车作用，不作为额外空仓闸。评估仍为 target_regime='bull' 的超额收益口径。若结果仍差，说明问题不是刹车；若明显改善，才支持慢变量刹车误伤修复期。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsBullXRankNb:
    name = "ind_rs_bullxrank_nb_2611"
    target_regime = "bull"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    industry_top = 5
    industry_cap = 2
    min_industry_members = 3

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        drawdowns = []
        market_rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.slow_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            if not h["amount"].iloc[-20:].mean() >= self.min_amount:
                continue
            last = h.iloc[-1]
            close = float(last["close"])
            prev = float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue
            c = h["close"]
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            r60 = float(c.iloc[-1] / c.iloc[-61] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(r60)):
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            high250 = float(c.max())
            if math.isfinite(high250) and high250 > 0:
                drawdowns.append(max(0.0, 1.0 - close / high250))
            if len(c) >= 120:
                ma120 = float(c.iloc[-120:].mean())
                if math.isfinite(ma120) and ma120 > 0:
                    market_rows.append({"above": close > ma120, "r20": r20})
            rows.append({"code": code, "name": name, "industry": view.industry(code),
                         "close": close, "r20": r20, "r5": r5, "r60": r60,
                         "sigma": sigma})
        if not rows or not drawdowns:
            return []
        dd_med = float(pd.Series(drawdowns).median())
        bull_proxy = False
        if market_rows:
            market = pd.DataFrame(market_rows)
            bull_proxy = bool(float(market["above"].mean()) >= 0.5 and float(market["r20"].median()) > 0)
        if not bull_proxy:
            if dd_med >= 0.30:
                return []
            if dd_med >= 0.22 and view.asof.toordinal() % 2 != 0:
                return []
        df = pd.DataFrame(rows)
        df["rel20"] = df["r20"].rank(pct=True)
        df["rel60"] = df["r60"].rank(pct=True)
        industry_scores = []
        for industry, members in df.groupby("industry", sort=True):
            if industry is None or len(members) < self.min_industry_members:
                continue
            ind_score = float(members["rel20"].mean() + members["rel60"].mean())
            industry_scores.append({"industry": industry, "ind_score": ind_score})
        if not industry_scores:
            return []
        ind_df = pd.DataFrame(industry_scores)
        ind_df = ind_df.sort_values(["ind_score", "industry"], ascending=[False, True])
        strong = set(ind_df.head(self.industry_top)["industry"])
        df = df[df["industry"].isin(strong)].copy()
        if df.empty:
            return []
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        industry_counts = {}
        for r in df.itertuples(index=False):
            industry = r.industry
            if industry_counts.get(industry, 0) >= self.industry_cap:
                continue
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"全池相对强度前5强行业{industry}，20日涨幅{r.r20:.1%}，60日涨幅{r.r60:.1%}，5日涨幅{r.r5:.1%}，σ={r.sigma:.1%}，牛市代理日不刹车"))
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if len(picks) >= 1:
                break
        return picks


STRATEGY = IndRsBullXRankNb()
