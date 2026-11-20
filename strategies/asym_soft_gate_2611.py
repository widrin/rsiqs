"""假设：前任三档硬闸门（mkt_proxy_gate_2612 系列）在 train 与 val 之间呈零和拉锯——three（弱市空仓、强市需广度确认）train 0.58 / val 0.50，v3（弱市放宽、去掉广度确认）train 0.49 / val 1.44，正好互为镜像，说明继续在同一参数轴上挪阈值已无增量。

本版改变闸门的函数形式：不做「空仓 / 1只×0.3 / 5只×1.0」的离散跳变，而是用连续的暴露系数
expo = clip((mkt_mom20 + 4%) / 8%, 0, 1) × (0.6 + 0.4 × breadth)
其中 mkt_mom20 为全池 20 日累计收益的中位数（市场动量），breadth 为站上自身 60 日均线的股票占比。持仓数 = 1 + round(4 × expo)，每只 weight = 0.4 + 0.6 × expo。

经济学理由：(1) 大盘状态是连续量，把它映射成连续暴露比分档更稳，避免了在阈值附近的日子反复空仓/满仓——这种抖动在 2018/2022 的熊市反弹里尤其致命（空仓错过反弹、满仓吃到下一跌），是 train 夏普被拖低的主因之一；(2) 不空仓保底 1 只×0.4，保证在 val 这类牛市回调段仍有参与度，避免误杀反弹；(3) 广度作为乘数而非门控，给强市一点加成但不做硬性一票否决，正是 three 在 val 被卡住的地方。

与现任差异：现任无大盘择时，恒定开满；本版在弱市把总暴露压到 0.4~0.8、强市拉到 5×1.0，是一个平滑的风险预算。预期失效场景：市场动量在 0 附近长期横盘时 expo 在 0.5 上下浮动，暴露变化贡献不了 alpha，此时策略退化到「半仓版现任」，收益低于满仓。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class AsymSoftGate:
    name = "asym_soft_gate_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def _market(self, view: DataView):
        # 单次遍历全池：收集 20 日累计收益与是否站上 60 日均线
        rets = []
        above = 0
        total = 0
        for code in view.codes():
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            last = float(c.iloc[-1])
            base = float(c.iloc[-21])
            ma60 = float(c.iloc[-60:].mean())
            if not (math.isfinite(last) and math.isfinite(base) and math.isfinite(ma60)):
                continue
            if base <= 0 or ma60 <= 0:
                continue
            rets.append(last / base - 1.0)
            total += 1
            if last > ma60:
                above += 1
        if total < 50 or not rets:
            return 0.0, 0.5
        mom = float(pd.Series(rets).median())
        breadth = above / total
        return mom, breadth

    def select(self, view: DataView) -> list[Pick]:
        mom, breadth = self._market(view)
        expo = max(0.0, min(1.0, (mom + 0.04) / 0.08)) * (0.6 + 0.4 * breadth)
        n_slots = 1 + int(round(4.0 * expo))
        weight = round(0.4 + 0.6 * expo, 3)

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
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(min(self.top_n, max(n_slots, 1) * 3))
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
                reason=f"市场20日动量{mom:.1%}、广度{breadth:.0%}→暴露系数{expo:.2f}(共{n_slots}只×{weight})；个股20日涨幅{r.r20:.1%}、5日回调{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = AsymSoftGate()
