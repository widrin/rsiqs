"""假设：基线「20日动量−5日反转」的入场打分有效，但固定 93% 止损 / 110% 止盈 / 5 日持有对低波动蓝筹过宽（永远触发不到）、对高波动小票过紧（隔夜一跳即被扫损），是拖累超额收益与夏普的主要来源。本策略保持基线打分完全不变，仅把每只候选股的 stop / target / hold_days 按其自身 20 日日收益率波动率 σ 自适应：stop=close·(1−1.5σ)、target=close·(1+3σ)（约 2:1 盈亏比），hold_days 按 σ 分档取 3/5/8 个交易日。经济学依据：A 股散户主导、个股截面波幅差异大，ATR 式波动定出场比固定百分比更贴合个股真实波幅，减少假止损与无效持有时长。与现任差异：现任是一刀切出场，本版是波动率自适应出场，入场信号与股票池完全一致。预期失效场景：波动率长期极低的蓝筹股，σ 过小会使 target 过近（<3%），交易成本占比上升而失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class VolTargetExit:
    name = "vol_target_exit_2611"
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
            if sigma <= 0.02:
                hold = 8
            elif sigma <= 0.04:
                hold = 5
            else:
                hold = 3
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，20日波动率σ={sigma:.1%}，自适应止损{stop_px:.2f}/止盈{target_px:.2f}，持有{hold}日"))
        return picks


STRATEGY = VolTargetExit()