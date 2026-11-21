"""假设：行业与个股的相对强度主要在牛市通过资金沿强行业扩散而兑现，deepbrake30 的全周期绝对收益口径把熊市防守成本也计入，掩盖了牛市选股 alpha。本稿与 ind_rs_deepbrake30_2612 基底保持一致：行业 rel20+rel60 排名选前5强行业，个股 rel20 排名减5日反转排名，每日1只，1.5σ止损、3σ止盈、5日持有，单行业不超过2只，dd_med 0.30/0.22 深度回撤刹车；仅新增 target_regime='bull'，只在牛市日按相对沪深300超额收益评估。与现任全周期个股动量策略不同，本稿检验的是牛市行业轮动超额，而非全周期绝对收益。预期在强行业快速切换、相对动量失效或牛市样本过少时失效；保留持续建仓与刹车，避免全空仓式切换造成牛市日收益标准差不足。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsBullRegime:
    name = "ind_rs_bullregime_2611"
    target_regime = "bull"
    top_n = 15
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    industry_top = 5
    industry_cap = 2

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        industry_rows = {}
        drawdowns = []
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
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            industry = view.industry(code)
            if len(c) >= 61:
                r60 = float(c.iloc[-1] / c.iloc[-61] - 1)
                if math.isfinite(r60) and industry is not None:
                    if industry not in industry_rows:
                        industry_rows[industry] = []
                    industry_rows[industry].append({"code": code, "r20": r20, "r60": r60})
            high250 = float(c.max())
            if math.isfinite(high250) and high250 > 0:
                drawdowns.append(max(0.0, 1.0 - close / high250))
            rows.append({"code": code, "name": name, "industry": industry,
                         "close": close, "r20": r20, "r5": r5, "sigma": sigma})
        if not rows or not industry_rows or not drawdowns:
            return []
        dd_med = float(pd.Series(drawdowns).median())
        if dd_med >= 0.30:
            return []
        if dd_med >= 0.22 and view.asof.toordinal() % 2 != 0:
            return []
        industry_scores = []
        for industry, members in industry_rows.items():
            frame = pd.DataFrame(members)
            rel20 = float(frame["r20"].rank(pct=True).mean())
            rel60 = float(frame["r60"].rank(pct=True).mean())
            industry_scores.append({"industry": industry, "ind_score": rel20 + rel60})
        ind_df = pd.DataFrame(industry_scores)
        ind_df = ind_df.sort_values(["ind_score", "industry"], ascending=[False, True])
        strong = set(ind_df.head(self.industry_top)["industry"])
        df = pd.DataFrame(rows)
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
                reason=f"强行业{industry}，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={r.sigma:.1%}，全池回撤中位数{dd_med:.1%}，牛市相对强度"))
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if len(picks) >= 1:
                break
        return picks


STRATEGY = IndRsBullRegime()
