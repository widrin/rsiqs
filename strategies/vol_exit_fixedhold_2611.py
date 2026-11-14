"""假设：基线「20日动量−5日反转」入场打分有效，但固定 93%/110% 出场对低波动股过宽、高波动股过紧。前一版 vol_target_exit_2611 证明「止损/止盈按 20 日波动率 σ 自适应」可让 val 夏普/回撤/超额全部达标，但其按 σ 把持有期分档（低波动给8日、高波动给3日）使 train 夏普从0.61降到0.54——A股动量短期衰减快，低波动股拉长持有吃到反转。本版仅保留波动自适应 stop=close·(1−1.5σ)、target=close·(1+3σ)，把 hold_days 统一回基线的5日，不动入场打分与股票池。预期失效场景：σ 长期极低使 target 过近、交易成本占比上升。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class VolExitFixedHold:
    name = "vol_exit_fixedhold_2611"
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
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，自适应止损{stop_px:.2f}/止盈{target_px:.2f}，固定持有{hold}日"))
        return picks


STRATEGY = VolExitFixedHold()
