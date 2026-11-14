"""假设：vol_exit_fixedhold_2611 已证明「按 20 日波动率 σ 自适应止损/止盈 + 固定 hold_days=5」方向正确——入场打分与股票池完全沿用基线，train 夏普 0.68（>基线0.61）、val 夏普 1.15、val 超额 +25%，仅 DSR 0.81 差 0.09 未过 0.9 门槛。本版在同一框架内把盈亏比从 1.5σ/3σ（2:1）微调为 1.2σ/3.3σ（约 2.7:1）：A 股动量趋势在 5 日持有窗口内往往延续，3σ 止盈过早截断盈利单；1.2σ 止损略宽以减少隔夜跳空造成的假扫损。经济学依据：让盈利单跟随动量跑、亏损单少被噪声扫出，抬高单笔期望盈亏比。与现任/前版差异：仅 stop/target 的 σ 倍数，入场打分、5 日持有、股票池全部不变。预期失效：σ 极低时 3.3σ target 仍可能过近、成本占比上升。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class VolExitRR27:
    name = "vol_exit_rr27_2611"
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
            stop_px = round(r.close * (1 - 1.2 * sigma), 2)
            target_px = round(r.close * (1 + 3.3 * sigma), 2)
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}（盈亏比约2.7:1），持有{hold}日"))
        return picks


STRATEGY = VolExitRR27()
