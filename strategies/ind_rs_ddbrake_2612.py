"""假设：相对行业强度有真实样本外alpha，每日1只、5日到期的慢速建仓也已改善val风险收益；剩余深回撤主要来自长熊段仍持续买入相对强但高beta的行业。本稿只增加一个慢变量深回撤刹车：以全池个股收盘价距各自250日高点的回撤中位数衡量典型股票损失，在10%至35%区间把新仓weight从1.0线性降到0.3，回撤中位数达到35%时当日不新开仓。已有持仓仍按5日到期及1.5σ/3σ出场，不用紧止损，也不做二元牛熊判断。与ind_rs_singleflow的差异仅是慢速暴露控制，选股、行业分散和出场不变。预期在典型股票深回撤后若相对行业动量迅速修复，刹车会踏空部分V形反弹；若回撤主要由少数个股造成而中位数较浅，保护也会不足。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDdBrake:
    name = "ind_rs_ddbrake_2612"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
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
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
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
        if not rows or len(drawdowns) < 30:
            return []
        dd_med = float(pd.Series(drawdowns).median())
        if not math.isfinite(dd_med) or dd_med >= 0.35:
            return []
        exposure = 1.0 - 0.7 * min(max((dd_med - 0.10) / 0.25, 0.0), 1.0)
        df = pd.DataFrame(rows)
        market20 = float(df["r20"].median())
        market60 = float(df["r60"].median())
        df["ir20"] = df["r20"] - df.groupby("industry")["r20"].transform("mean")
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
        reason = (f"{r['industry']}相对强度入选，个股20日{r['r20']:.1%}、5日{r['r5']:.1%}；"
                  f"全池250日回撤中位数{dd_med:.1%}，新仓权重{weight:.2f}，"
                  f"1.5σ止损{stop_px:.2f}/3σ止盈{target_px:.2f}，持有5日")
        return [Pick(code=r["code"], name=r["name"], score=float(r["score"]),
                     entry_max=entry_max, stop=stop_px, target=target_px,
                     hold_days=5, weight=weight, reason=reason)]


STRATEGY = IndRsDdBrake()
