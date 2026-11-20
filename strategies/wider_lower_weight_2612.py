"""假设：上一轮 fast_or_lowweight_2612 失败的一个重要原因是中间档 2只×0.5，标的太少、每笔冲击集中。本版做对照：极端档依旧清仓（收紧到 dd250>22% 或 breadth<28% 或 mom20<-6%），但中间档改为 4只×0.6（多一档标的、轻一点权重），检验『摊宽标的』比『集中高权重』是否对 train 夏普更友好。

经济学理由：个股 alpha 高度分散，单票 0.6 的冲击比 0.7 小；在 train 大量时间属于中间档的情景下，多一只股票能多捕捉一份 rank 头部的 alpha，降低单票意外的拖累。选股和出场沿用现任。

与现任差异：现任不择时；本版在温和/偏弱市场维持 4 只低权重，只在极端时清仓，目标是把 train 最大回撤压到 -40% 附近同时保住收益。

预期失效场景：若个股 alpha 相关性在高波动期同步放大，4只×0.6 的分散效果不如预期，train 回撤可能与主方案类似而非更小；若中间档选股质量下降，train 夏普可能不升反降，说明『摊宽标的』不如『集中』。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class WiderLowerWeight:
    name = "wider_lower_weight_2612"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def _market(self, view: DataView):
        mom_vals = []
        breadth_ok = 0
        breadth_n = 0
        dd_vals = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, 60)
            if len(h) < 40 or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            if c.iloc[-1] <= 0:
                continue
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            if math.isfinite(r20):
                mom_vals.append(r20)
            ma60 = float(c.iloc[-60:].mean())
            breadth_n += 1
            if c.iloc[-1] > ma60:
                breadth_ok += 1
            h2 = view.history(code, 250)
            if len(h2) >= 120:
                hi = float(h2["high"].iloc[-250:].max())
                if hi > 0:
                    dd_vals.append(1.0 - float(c.iloc[-1]) / hi)
        mom20 = float(pd.Series(mom_vals).median()) if mom_vals else 0.0
        breadth = breadth_ok / breadth_n if breadth_n else 0.5
        dd250 = float(pd.Series(dd_vals).median()) if dd_vals else 0.0
        return mom20, breadth, dd250

    def _tier(self, mom20, breadth, dd250):
        if dd250 > 0.22 or breadth < 0.28 or mom20 < -0.06:
            return 0, 0.0
        if mom20 > 0.0 and breadth > 0.55:
            return 5, 1.0
        return 4, 0.6

    def select(self, view: DataView) -> list[Pick]:
        mom20, breadth, dd250 = self._market(view)
        n_pick, weight = self._tier(mom20, breadth, dd250)
        if n_pick == 0 or weight <= 0:
            return []

        rows = []
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
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
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
            s = r.sigma
            stop_px = round(r.close * (1 - 1.5 * s), 2)
            target_px = round(r.close * (1 + 3.0 * s), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                weight=weight,
                reason=(f"市场代理 mom20={mom20:.1%} breadth={breadth:.1%} dd250={dd250:.1%} 档位{n_pick}只×{weight:.1f}；"
                        f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={s:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日")))
            if len(picks) >= n_pick:
                break
        return picks


STRATEGY = WiderLowerWeight()
