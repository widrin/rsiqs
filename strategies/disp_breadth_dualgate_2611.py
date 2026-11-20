"""假设：前几轮证明「动量/回撤类闸门」在 train 与 val 间零和拉锯——弱市阈值严则 val 被误杀，阈值松则 train 回撤与夏普双输；上一轮 dispersion_gate_2611 用横截面离散度一个维度就拿到 train 0.70 / val 1.05（DSR 0.5909 仅差千分位），说明「收益质量」维度是有增量的，但单靠它不够稳健。

本版把两个正交维度并联成硬条件：
- 离散度 disp = 全池近 20 日累计收益的横截面标准差，取其在近 60 个观测中的滚动分位 pct_d。
- 广度 breadth = 全池站上自身 60 日均线的股票占比。
规则：
- 双高（pct_d > 0.5 且 breadth > 0.5）→ 5 只 × 1.0：个股分化充分且分化方向整体向上，动量选股有肉吃，这是最该满仓的状态。
- 双低（pct_d < 0.2 且 breadth < 0.4）→ 空仓：既无分化（alpha 被压扁）、又整体在均线下方（BETA 向下），此时选股无意义而止损频发——train 里的低质量普跌段主要集中在这样的日子，敢于空仓是压低 train 回撤与提高夏普的关键。
- 其余（一高一低，或都处中段）→ 1 只 × 0.5：温和参与，避免错杀 val 里「分化在但广度暂未过」的反弹初期。

经济理由：(1) 离散度衡量「有无肉」，广度衡量「方向对否」，二者相关但不等价——普涨段离散度低但广度高（不敢重仓，因为没有分化），普跌段离散度可能高（恐慌抛售分化）但广度低（全在均线下方，一高一下落入中间态 0.5，恰好是保守而不空仓的折中）。(2) 只有在双低时才空仓，比纯动量闸门空仓的条件「苛刻但更有逻辑」，因为它是用截面结构（不只方向）来判断，不容易在熊市反弹里反复切换。(3) 与其他版本差异：前几版是单一维度的连续/分档缩放，本版是两维并联的二值硬条件 + 中间态，函数形式不同，信号组合也不同。

与现任差异：现任恒定满仓无择时；本版只在大约双高时段满仓，弱市/低质量时段大幅降暴露甚至空仓。

预期失效：(1) 离散度在急跌日会跳升（恐慌分化），此时若 breadth 也跌得不够深，可能落入中间态 0.5，仍保留部分仓位吃到下一跌；(2) val 牛市若广度长期在 0.45~0.5 边缘，「双高」难以触发，退回 0.5 档，val 收益被削——这是最需要 val 段检验的风险。"""
import math

import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class DispBreadthDualGate:
    name = "disp_breadth_dualgate_2611"
    top_n = 15
    min_rows = 80
    min_amount = 1e8

    def _market_state(self, view: DataView):
        codes = []
        for code in view.codes():
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            if not float(h["close"].iloc[-1]) > 0:
                continue
            codes.append(code)
        if len(codes) < 50:
            return 0.5, 0.5
        closes = []
        for code in codes:
            h = view.history(code, 80)
            closes.append(h["close"].to_numpy(dtype=float)[-60:])
        arr = np.vstack(closes).T
        disps = []
        for k in range(20, 60):
            base = arr[k - 20]
            cur = arr[k]
            r = cur / base - 1.0
            r = r[np.isfinite(r)]
            if len(r) < 30:
                continue
            disps.append(float(np.std(r)))
        pct_d = 0.5
        if disps:
            cur = disps[-1]
            below = sum(1 for d in disps if d <= cur)
            pct_d = below / len(disps)
        last = arr[-1]
        above = 0
        total = 0
        for j in range(arr.shape[1]):
            col = arr[:, j]
            col = col[np.isfinite(col)]
            if len(col) < 60:
                continue
            ma = float(np.mean(col[-60:]))
            cl = float(col[-1])
            if ma <= 0 or cl <= 0:
                continue
            total += 1
            if cl > ma:
                above += 1
        breadth = above / total if total > 0 else 0.5
        return pct_d, breadth

    def select(self, view: DataView) -> list[Pick]:
        pct_d, breadth = self._market_state(view)
        if pct_d > 0.5 and breadth > 0.5:
            n_slots, weight = 5, 1.0
        elif pct_d < 0.2 and breadth < 0.4:
            n_slots, weight = 0, 0.0
        else:
            n_slots, weight = 1, 0.5
        if n_slots <= 0:
            return []

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
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(min(self.top_n, n_slots * 3))
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
                reason=f"离散度{pct_d:.0%}分位、广度{breadth:.0%}→{n_slots}只×{weight}；个股20日涨幅{r.r20:.1%}、5日回调{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = DispBreadthDualGate()
