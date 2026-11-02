"""假设：现任 baseline_momo 的选股打分（20日动量排名−5日反转排名）已经捕捉到「中期趋势+短期回调」的信号，但 −7%/+10%、持有5天的退出结构盈亏比仅约1.43，且 −7%止损在A股个股5日持有窗口内被正常波动扫出的概率高。收紧为止损−4%、止盈+12%、持有8个交易日：止损只截断真正的坏交易（超过约1.5倍日波动），止盈保留动量展开空间（约3倍于止损幅度），用8天代替5天让信号兑现。与现任差异仅在退出参数，选股池、打分、限价买入规则完全一致。预期失效场景：低波动蓝筹中 −4% 止损过于敏感，会产生大量扫损后踏空；在趋势缓慢的震荡市里8天持有期可能把盈利拖回亏损。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MomoTight2610:
    name = "momo_tight_2610"
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
            close, prev = float(last["close"]), float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue
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
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.96, 2),
                 target=round(r.close * 1.12, 2), hold_days=8,
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，中期动量+短期回调，止损−4%止盈+12%持有8天，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = MomoTight2610()
