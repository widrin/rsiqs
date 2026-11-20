"""假设：震荡市中低波动异象能够提供绝对收益。杠杆约束、彩票偏好与相对排名考核使投资者为高波动票支付过高价格，低波票则因换手低、回撤小而具有更好的风险调整收益；这一效应难以完全套利，因为低波组合在快速牛市会明显落后，且机构不愿长期承担相对排名风险。本策略在流动性合格的大中盘股中按20日日收益波动率从低到高排序，并要求20日收益非负、收盘不在20日低位，排除持续阴跌造成的伪低波。与现任『20日动量−5日反转』不同，本策略不以短期涨幅差作为入场依据，也不做大盘择时；出场采用较窄的固定止损止盈和10日持有期，降低换手与震荡市反复触发出场的摩擦。预期在全面快速牛市、低波蓝筹估值持续压缩或防御行业集中下跌时失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class SidewaysLowVol:
    name = "sideways_lowvol_2611"
    target_regime = "sideways"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
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
            low20 = float(c.iloc[-20:].min())
            if not (math.isfinite(r20) and math.isfinite(low20)):
                continue
            if r20 < 0 or close <= low20 * 1.01:
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 20:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = -df["sigma"]
        df = df.sort_values(["sigma", "code"], ascending=[True, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            entry_max = round(r.close * 1.02, 2)
            stop_px = round(r.close * 0.95, 2)
            target_px = round(r.close * 1.08, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px,
                hold_days=10,
                reason=f"20日波动率{r.sigma:.1%}处于低波候选前列，20日涨幅{r.r20:.1%}且未贴近20日低位；缩量防御持有10日，止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = SidewaysLowVol()
