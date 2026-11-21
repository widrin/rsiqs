"""假设：相对行业强度和每日1只慢建仓有效，但全池距250日高点的回撤是滞后指标，单纯按回撤深度降权可能在下跌初期仍暴露过高。本稿在相同选股与1.5σ/3σ、5日出场之上，用两个横截面慢标量的较差状态控制新仓：市场20日收益中位数从0到-12%对应风险从0到1，全池250日回撤中位数从10%到30%对应风险从0到1，取两者较大值；风险未满时weight从1.0线性降至0.3，只有短期收益中位数已显著为负且深回撤也确认时才停止新开仓。它不是二元牛熊闸，中等弱势仍保留小额资金流，旧仓自然到期。与另一草稿的差异是加入短期横截面确认并提前降权，而非只按深回撤线性刹车。若市场快速V形反转，双确认会晚于价格修复并踏空；若行业相对强度在普跌中只是高beta假象，仍可能亏损。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsSlowBrake:
    name = "ind_rs_slowbrake_2612"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        market_r20 = []
        drawdowns = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.slow_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            last = h.iloc[-1]
            close = float(last["close"])
            prev = float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue
            c = h["close"]
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            if math.isfinite(r20):
                market_r20.append(r20)
            peak = float(c.max())
            if math.isfinite(peak) and peak > 0:
                dd = 1.0 - close / peak
                if math.isfinite(dd):
                    drawdowns.append(dd)
            if not h["amount"].iloc[-20:].mean() >= self.min_amount:
                continue
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue
            r60 = float(c.iloc[-1] / c.iloc[-60] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(r20) and math.isfinite(r60) and math.isfinite(r5)):
                continue
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            industry = view.industry(code)
            if industry is None:
                industry = "未分类"
            rows.append({"code": code, "name": name, "industry": industry,
                         "close": close, "r20": r20, "r60": r60,
                         "r5": r5, "sigma": sigma})
        if not rows or len(drawdowns) < 30 or len(market_r20) < 30:
            return []
        dd_med = float(pd.Series(drawdowns).median())
        r20_med = float(pd.Series(market_r20).median())
        if not (math.isfinite(dd_med) and math.isfinite(r20_med)):
            return []
        dd_risk = min(max((dd_med - 0.10) / 0.20, 0.0), 1.0)
        ret_risk = min(max(-r20_med / 0.12, 0.0), 1.0)
        risk = max(dd_risk, ret_risk)
        if risk >= 1.0:
            return []
        exposure = 1.0 - 0.7 * risk
        df = pd.DataFrame(rows)
        market20 = float(df["r20"].median())
        market60 = float(df["r60"].median())
        industry_stats = df.groupby("industry").agg(ir20=("r20", "mean"), ir60=("r60", "mean"))
        industry_stats["rel20"] = industry_stats["ir20"] - market20
        industry_stats["rel60"] = industry_stats["ir60"] - market60
        industry_stats["ind_score"] = industry_stats["rel20"].rank() + industry_stats["rel60"].rank()
        strong = industry_stats.sort_values(["ind_score"], ascending=[False]).head(5).index
        df = df[df["industry"].isin(strong)].copy()
        if df.empty:
            return []
        df["rel_ind20"] = df["r20"] - df["industry"].map(industry_stats["ir20"])
        df["score"] = df["rel_ind20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        df["ind_rank"] = df.groupby("industry").cumcount()
        df = df[df["ind_rank"] < 2]
        if df.empty:
            return []
        r = df.iloc[0]
        close = float(r["close"])
        sigma = float(r["sigma"])
        stop_px = round(close * (1 - 1.5 * sigma), 2)
        target_px = round(close * (1 + 3.0 * sigma), 2)
        entry_max = round(close * 1.03, 2)
        if not stop_px < entry_max < target_px:
            return []
        weight = round(exposure, 4)
        reason = (f"{r['industry']}20日/60日相对强度入选，个股20日{r['r20']:.1%}、5日{r['r5']:.1%}；"
                  f"市场r20中位数{r20_med:.1%}、250日回撤中位数{dd_med:.1%}，新仓权重{weight:.2f}；"
                  f"止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日")
        return [Pick(code=r["code"], name=r["name"], score=float(r["score"]),
                     entry_max=entry_max, stop=stop_px, target=target_px,
                     hold_days=5, weight=weight, reason=reason)]


STRATEGY = IndRsSlowBrake()
