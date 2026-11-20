"""假设：前两轮失败版本的硬空仓要么太激进（『或』三条件，train 夏普 0.23）要么太晚（计分 >=2，train 回撤 -79%），共性是单票 1/5 的大敞口叠加 5 日持有。本变体与 fast_or_lowweight_2612 对照，改两处：(1) 空仓条件用『且』连接两个快变量——mom20<-4% 且 breadth<0.40，只有两个维度同时恶化才确认系统性普跌，减少震荡市里因单一维度触发而反复空仓/满仓带来的误伤与换手成本；(2) 把慢变量 dd250 降级为『减仓』刹车：dd250>15%（长熊确认后）时只留 1只×0.4，其余 5只×0.35 满仓、3只×0.4 温和区。核心仍是全时段真实降低单票 weight，把单票回撤贡献从 1/5 降到约 1/12。经济学理由：真长熊是渐次恶化的——快变量先反映急跌（mom20、breadth），慢变量后确认（dd250）；用『且』连接快变量能避免单一指标在震荡顶部被误杀，用 dd250 作为最后的慢速刹车防止在熊市反弹段重新满仓。与现任差异：现任无仓位管理、单票 1/5；本策略有快变量确认式硬空仓 + 慢变量减仓 + 全时段降权。预期失效场景：2016/2019 震荡顶部 breadth 与 mom20 可能只恶化一个，无法确认空仓，吃到局部回撤；牛市段降权压低收益、可能伤 val 夏普。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class TwoDimSlowBrake:
    name = "two_dim_slowbrake_2612"
    top_n = 15
    min_rows = 250
    min_amount = 1e8
    w_full = 0.35
    w_mid = 0.4
    w_low = 0.4

    def _proxy(self, view: DataView):
        mom_list = []
        dd_list = []
        n_on = 0
        n_total = 0
        for code in view.codes():
            if is_st(view.name(code)):
                continue
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            close = float(h["close"].iloc[-1])
            if not (math.isfinite(close) and close > 0):
                continue
            n_total += 1
            r20 = float(h["close"].iloc[-1] / h["close"].iloc[-21] - 1)
            if math.isfinite(r20):
                mom_list.append(r20)
            ma60 = float(h["close"].iloc[-61:].mean())
            if close >= ma60:
                n_on += 1
            peak = float(h["close"].max())
            if peak > 0:
                dd = (peak - close) / peak
                if math.isfinite(dd):
                    dd_list.append(dd)
        if n_total == 0 or not mom_list or not dd_list:
            return 0.0, 1.0, 0.0
        med_mom = float(pd.Series(mom_list).median())
        med_dd = float(pd.Series(dd_list).median())
        breadth = n_on / n_total
        return med_mom, breadth, med_dd

    def select(self, view: DataView) -> list[Pick]:
        med_mom, breadth, med_dd = self._proxy(view)
        if med_mom < -0.04 and breadth < 0.40:
            return []
        if med_dd > 0.15:
            max_picks = 1
            weight = self.w_low
            regime = f"慢刹车(中位回撤 {med_dd:.1%})"
        elif breadth < 0.55 or med_mom < 0.0:
            max_picks = 3
            weight = self.w_mid
            regime = f"温和(mom20 {med_mom:.1%}/广度 {breadth:.0%})"
        else:
            max_picks = 5
            weight = self.w_full
            regime = f"满仓(mom20 {med_mom:.1%}/广度 {breadth:.0%})"

        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, 60)
            if len(h) < 60 or h.index[-1].date() != view.asof:
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
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5, weight=weight,
                reason=f"{regime}→单票{weight:.0%}；20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
            if len(picks) >= max_picks:
                break
        return picks


STRATEGY = TwoDimSlowBrake()
