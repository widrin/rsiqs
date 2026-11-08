"""假设：基线中期动量+短期反转中并非所有回调都可买。A股散户主导下，缩量回调（获利盘出清、抛压轻）后续更可能延续中期趋势；放量破位则说明资金在撤离，是陷阱。因此在基线打分基础上，只保留「收盘价仍在60日均线上方」（中期趋势未破坏）且「近5日平均成交额 ≤ 近20日平均成交额」（回调期缩量，非放量出货）的股票。与现任 baseline_momo 的差异：新增趋势线与量能两个过滤条件，其余入场/止损/止盈/持有参数完全相同。预期失效环境：高位无量阴跌（缩量但趋势已悄悄走坏）或V型反转初期（破位后迅速拉起，被误排除）。本版修复上一版 Pick 构造错误并显式校验 stop<entry_max<target。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class Momotrend2611V2:
    name = "momo_trend_2611_v2"
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
            amt = h["amount"]
            ma60 = float(c.mean())
            if not (math.isfinite(ma60) and close > ma60):
                continue
            amt5 = float(amt.iloc[-5:].mean())
            amt20 = float(amt.iloc[-20:].mean())
            if not (math.isfinite(amt5) and math.isfinite(amt20) and amt20 > 0 and amt5 <= amt20):
                continue
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            entry_max = round(close * 1.03, 2)
            stop = round(close * 0.93, 2)
            target = round(close * 1.10, 2)
            if not (stop < entry_max < target):
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5,
                         "entry_max": entry_max, "stop": stop, "target": target})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=float(r.entry_max), stop=float(r.stop),
                 target=float(r.target), hold_days=5,
                 reason=f"20日涨幅{r.r20:.1%}、5日涨幅{r.r5:.1%}，站上60日线且回调缩量，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = Momotrend2611V2()
