"""假设：前两轮证明相对行业强度在val有正超额，但每日最多5只的推荐会让资金快速同步满仓；软闸只减少新名额，不能直接卖出旧仓，因此无法可靠控制总暴露。本稿保留相对行业轮动，改为每日只返回1只股票，使新增资金约为总资金的五分之一，依靠5日到期自然周转形成分批组合。行业打分采用20日与60日等权收益相对全行业均值的平均，兼顾中期趋势与短期轮动；个股用自身20日收益相对全池中位数的排名减5日反转排名，并要求相对强度为正，避免在强行业内继续追逐绝对高beta但相对落后的股票。出场沿用现任1.5σ止损、3σ止盈和5日持有，不再引入市场闸或新的出场参数。与现任差异是行业配置、双重相对强度和每日单票资金流，而非微调止损或广度阈值。预期失效于每日持续买入最终仍接近满仓、60日行业趋势稀释短期轮动alpha，以及单票组合的个股特异性风险上升。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsSingleFlow:
    name = "ind_rs_singleflow_2612"
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        stocks = []
        ind_sum20 = {}
        ind_sum60 = {}
        ind_n = {}
        all_r20 = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.min_rows)
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
            r60 = float(c.iloc[-1] / c.iloc[0] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            rets = c.pct_change().dropna().iloc[-20:]
            if len(rets) < 10 or not (math.isfinite(r20) and math.isfinite(r60) and math.isfinite(r5)):
                continue
            sigma = float(rets.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            industry = view.industry(code)
            if industry is None:
                continue
            all_r20.append(r20)
            stocks.append({"code": code, "name": name, "industry": industry,
                           "close": close, "r20": r20, "r5": r5, "sigma": sigma})
            ind_sum20[industry] = ind_sum20.get(industry, 0.0) + r20
            ind_sum60[industry] = ind_sum60.get(industry, 0.0) + r60
            ind_n[industry] = ind_n.get(industry, 0) + 1
        if len(all_r20) < 100:
            return []
        med = float(pd.Series(all_r20).median())
        if not math.isfinite(med):
            return []
        ind_rows = []
        for industry, count in ind_n.items():
            if count >= 3:
                ind_rows.append({"industry": industry,
                                 "ret20": ind_sum20[industry] / count,
                                 "ret60": ind_sum60[industry] / count})
        if len(ind_rows) < 5:
            return []
        market20 = sum(r["ret20"] for r in ind_rows) / len(ind_rows)
        market60 = sum(r["ret60"] for r in ind_rows) / len(ind_rows)
        for r in ind_rows:
            r["relative"] = 0.5 * (r["ret20"] - market20) + 0.5 * (r["ret60"] - market60)
        ind_df = pd.DataFrame(ind_rows).sort_values(["relative", "industry"], ascending=[False, True])
        leaders = set(ind_df.head(5)["industry"])
        rel_map = dict(zip(ind_df["industry"], ind_df["relative"]))
        df = pd.DataFrame(stocks)
        df = df[df["industry"].isin(leaders)].copy()
        if df.empty:
            return []
        df["relative_stock"] = df["r20"] - med
        df = df[df["relative_stock"] > 0].copy()
        if df.empty:
            return []
        df["score"] = df["relative_stock"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        for r in df.itertuples(index=False):
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            return [Pick(code=r.code, name=r.name, score=float(r.score),
                         entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                         reason=f"{r.industry}20日与60日相对行业强度均值{rel_map[r.industry]:.1%}；个股20日相对全池{r.relative_stock:.1%}、5日{r.r5:.1%}；每日仅新增1只，分批建仓并持有5日")]
        return []


STRATEGY = IndRsSingleFlow()
