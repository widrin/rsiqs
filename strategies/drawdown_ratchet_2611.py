"""假设：前任各版大盘闸门都建立在「20 日动量 + 广度」这两个快变量上，阈值抖动在熊市反弹段反复误判（空仓错过反弹、满仓吃到下一跌），导致 train 与 val 零和拉锯。本版改用慢变量：全池等权代理相对其 250 日高点的回撤深度。

代理构造（轻量）：每一只股票用近 60 个交易日（足够算 20 日累计收益）计算 20 日累计收益，取中位数作为代理的「阶段收益」，并按日累积成一条简要的代理净值序列——但每天遍历全池重建序列代价高，故用等价且更轻的近似：直接取全池「近 20 日累计收益中位数」为 mr；把代理 P(t) 理解为一个滚动量，本版用另一种完全无需建序列的刻画——以全池个股「自身距其 250 日高点的回撤中位数」作为市场回撤深度 dd（个股层面先算，再取中位数），它单调、慢、无需跨日状态。

分档：dd < 5% 满仓 5×1.0；5%~10% 3×0.8；10%~18% 2×0.6；>18% 1×0.4（不空仓）。

经济理由：(1) 250 日高点回撤是典型的慢变量，具有强持续性，不会因单周反弹而从深处跳回浅档，能覆盖 2018/2022 这种持续一年的长熊，这正是 mkt_ew_dd_brake 的思路；(2) 但 mkt_ew_dd_brake 用「代理净值」序列，构建重且对等权成分变动敏感；本版改用个股回撤中位数，稳健、无需维护跨日状态、单次遍历即可；(3) 不空仓保底 1×0.4 保住 val 牛市回调段的参与度。

与现任差异：现任无择时满仓恒定，本版按市场回撤深度做风险预算。与动量闸门的差异：回撤是方向无关的深度量，与 20 日动量相关性低。预期失效：市场在高位长期横盘（回撤浅但个股不涨）时闸门一直满仓，此时无超额；或急跌后快速 V 反（如 2019 初）时因回撤深而低仓，错过反弹。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class DrawdownRatchet:
    name = "drawdown_ratchet_2611"
    top_n = 15
    min_rows = 250
    min_amount = 1e8

    def _market_dd(self, view: DataView) -> float:
        # 全池个股距自身 250 日高点回撤的中位数
        dds = []
        for code in view.codes():
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            hi = float(c.iloc[-250:].max())
            last = float(c.iloc[-1])
            if not (math.isfinite(hi) and math.isfinite(last)) or hi <= 0 or last <= 0:
                continue
            dds.append(1.0 - last / hi)
        if len(dds) < 50:
            return 0.05
        return float(pd.Series(dds).median())

    def select(self, view: DataView) -> list[Pick]:
        dd = self._market_dd(view)
        if dd < 0.05:
            n_slots, weight = 5, 1.0
        elif dd < 0.10:
            n_slots, weight = 3, 0.8
        elif dd < 0.18:
            n_slots, weight = 2, 0.6
        else:
            n_slots, weight = 1, 0.4

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
            if len(picks) >= n_slots:
                break
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold, weight=weight,
                reason=f"全池距250日高点回撤中位数{dd:.1%}→档位{n_slots}只×{weight}；个股20日涨幅{r.r20:.1%}、5日回调{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = DrawdownRatchet()
