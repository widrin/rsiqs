# strategies/baseline_momo.py
"""基线：中期动量 + 短期反转。分数 = rank(20日涨幅) - rank(5日涨幅)。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class BaselineMomo:
    name = "baseline_momo"
    top_n = 15  # 盘前流程最多取前 15 个复核；回测和结算只取前 5 个
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.min_rows)  # 只取需要的行：回测每天对约 800 只股票调用
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            if not h["amount"].iloc[-20:].mean() >= self.min_amount:  # NaN 也排除
                continue
            last = h.iloc[-1]
            close, prev = float(last["close"]), float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue  # 数据缺失
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue  # asof 收于涨停（按当天的规则，容忍一分钱的复权舍入）
            c = h["close"]
            r20, r5 = float(c.iloc[-1] / c.iloc[-21] - 1), float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            rows.append({"code": code, "name": name, "close": close, "r20": r20, "r5": r5})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.93, 2),
                 target=round(r.close * 1.10, 2), hold_days=5,
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，中期动量+短期回调，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = BaselineMomo()
