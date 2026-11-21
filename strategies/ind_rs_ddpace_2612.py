"""假设：相对行业强度与每日1只、5日到期的慢速建仓有样本外alpha，但长熊段持续买入相对强行业仍造成系统性回撤。上一版按回撤中位数连续设置Pick.weight后，train/val波动和回撤反而异常扩大；本稿不再使用weight，只把深回撤刹车实现为资金流入节奏：全池个股距250日高点的回撤中位数低于10%时每日最多新开1只，10%至30%时隔日最多新开1只，达到30%时暂停新开仓，已有仓位始终按5日到期及1.5σ/3σ退出。这样在中等下跌中仍保留流入而非二元空仓，深度下跌中让存量自然收缩，也避免权重与资金分配的非直觉交互。与ind_rs_ddbrake的差异仅是暴露控制载体，选股、单行业分散和出场不变。若市场V形反转，隔日或暂停开仓会踏空部分修复；若相对强度只是高beta假象，慢刹车仍不能消除存量亏损。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDdPace:
    name = "ind_rs_ddpace_2612"
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
        if not math.isfinite(dd_med) or dd_med >= 0.30:
            return []
        half_speed = dd_med >= 0.10
        if half_speed and view.asof.toordinal() % 2 != 0:
            return []
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
        pace = "隔日1只" if half_speed else "每日1只"
        reason = (f"{r['industry']}20日/60日相对强度入选，个股20日{r['r20']:.1%}、5日{r['r5']:.1%}；"
                  f"全池250日回撤中位数{dd_med:.1%}，新开仓节奏{pace}；"
                  f"1.5σ止损{stop_px:.2f}/3σ止盈{target_px:.2f}，持有5日")
        return [Pick(code=r["code"], name=r["name"], score=float(r["score"]),
                     entry_max=entry_max, stop=stop_px, target=target_px,
                     hold_days=5, reason=reason)]


STRATEGY = IndRsDdPace()
