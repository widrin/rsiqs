"""假设：行业20日动量具有中期持续性，但此前无市场闸版本回撤过深，硬闸又因完全空仓踏空。本稿用两个横截面标量构造软闸：收盘价位于60日区间上半部的股票占比，以及全池20日收益中位数。广度以0.3为起点线性映射，市场动量从-5%到+5%线性映射，两者平均后决定当天新开0至5只；旧仓仍按5日到期或波动出场自然退出，避免频繁反手。行业取20日等权收益前五，行业内沿用现任20日动量减5日反转，单行业最多2只。与现任差异是增加行业配置和连续的新资金暴露控制，而非修改个股出场。预期在急速V形反转时因仓位恢复较慢而踏空，在行业动量整体失效时也会跑输。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoSoftBreadth:
    name = "ind_momo_softbreadth_2612"
    min_rows = 60
    min_amount = 1e8
    top_n = 15

    def select(self, view: DataView) -> list[Pick]:
        stocks = []
        industries = {}
        breadth_up = 0
        breadth_n = 0
        market_r20 = []
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
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10 or not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            lo = float(c.min())
            hi = float(c.max())
            if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
                breadth_n += 1
                if close >= (lo + hi) * 0.5:
                    breadth_up += 1
            market_r20.append(r20)
            industry = view.industry(code)
            row = {"code": code, "name": name, "close": close,
                   "r20": r20, "r5": r5, "sigma": sigma,
                   "industry": industry}
            stocks.append(row)
            if industry is not None:
                if industry not in industries:
                    industries[industry] = []
                industries[industry].append(r20)
        if not stocks or breadth_n < 100 or len(market_r20) < 100:
            return []
        breadth = breadth_up / breadth_n
        med_r20 = float(pd.Series(market_r20).median())
        breadth_expo = min(max((breadth - 0.30) / 0.40, 0.0), 1.0)
        momo_expo = min(max((med_r20 + 0.05) / 0.10, 0.0), 1.0)
        exposure = 0.5 * (breadth_expo + momo_expo)
        slots = int(math.floor(exposure * 5.0 + 0.5))
        if slots <= 0:
            return []
        ind_rows = []
        for industry, values in industries.items():
            if len(values) >= 3:
                ind_rows.append({"industry": industry, "ret": float(pd.Series(values).mean())})
        if not ind_rows:
            return []
        ind_df = pd.DataFrame(ind_rows).sort_values(["ret", "industry"], ascending=[False, True])
        leaders = set(ind_df.head(5)["industry"])
        df = pd.DataFrame(stocks)
        df = df[df["industry"].isin(leaders)].copy()
        if df.empty:
            return []
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        industry_count = {}
        for r in df.itertuples(index=False):
            used = industry_count.get(r.industry, 0)
            if used >= 2:
                continue
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"{r.industry}行业20日动量领先；个股20日{r.r20:.1%}、5日{r.r5:.1%}；广度{breadth:.0%}、市场r20中位数{med_r20:.1%}，软暴露{exposure:.0%}，今日名额{slots}只"))
            industry_count[r.industry] = used + 1
            if len(picks) >= self.top_n:
                break
        return picks


STRATEGY = IndMomoSoftBreadth()
