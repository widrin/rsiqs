"""假设：用户指出的平衡方案是「three 的严弱市空仓 + v3 的无广度强市满仓 + 温和区 1×0.5」。前几轮 three/v3 零和的根源是「温和区」的质量无法用动量/广度区分：温和反弹该开仓、温和下跌该空仓，但两者在 mom≈0 附近长得一样。

本版改温和区的判别器：用上一轮验证有增量的横截面离散度（全池近 20 日收益截面 std 在近 60 观测中的滚动分位）来区分温和反弹与温和下跌——温和反弹里个股分化更充分（离散度分位高），温和下跌里个股趋同（离散度分位低）。

规则：
- 强市：市场 20 日动量（全池 20 日累计收益中位数）> 0 → 5 只 × 1.0（去掉广度确认，保住 val 牛市）。
- 弱市：mom < -3% 或 广度 < 40% → 空仓（保住 train 熊市）。
- 中间区：mom 在 [-3%, 0]，且广度 ≥ 40% → 离散度分位 > 0.5 则 1 只 × 0.5，否则 1 只 × 0.3。

经济理由：(1) 强市阈值 mom>0 不设广度门槛，避免 three 在 val 牛市初期广度卡在 45-55% 只开 0.3 的问题；(2) 弱市保留 three 的严阈值（-3%/40%），在 2018/2022 长熊中空仓；门槛已在试验中证明能压住 train 回撤；(3) 中间态用离散度分位，把「有肉但方向未明」的温和反弹留下来，把「无肉且方向偏弱」的温和下跌缩到 0.3。

与现任差异：现任无择时；本版在弱市空仓、中间区减仓、强市满仓，形成风险预算。与前两版差异：本版弱市阈值沿用 three 的严阈值（这是 train 达标的已验证基线），只在温和区的判别器上叠加离散度，是对已知平衡点的最小改动。

预期失效：(1) 弱市阈值 -3%/40% 在 train 的 V 型反弹里可能反复空仓/满仓，误杀反弹；(2) 若离散度与「温和反弹 vs 温和下跌」的相关性弱，中间态判别器形同虚设，退回 three 的 1×0.3（val 仍可能偏弱）；(3) 强市去掉广度确认后，牛市顶部（mom>0 但广度骤降）可能仍满仓吃到回撤。"""
import math

import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class UserPlanDispMid:
    name = "userplan_disp_mid_2611"
    top_n = 15
    min_rows = 80
    min_amount = 1e8

    def _market_state(self, view: DataView):
        codes = []
        rets = []
        above = 0
        total = 0
        for code in view.codes():
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            if not float(h["close"].iloc[-1]) > 0:
                continue
            codes.append(code)
        if len(codes) < 50:
            return 0.0, 0.5, 0.5
        closes = []
        for code in codes:
            h = view.history(code, 80)
            closes.append(h["close"].to_numpy(dtype=float)[-60:])
        arr = np.vstack(closes).T
        last = arr[-1]
        base = arr[-21]
        r = last / base - 1.0
        r = r[np.isfinite(r)]
        mom = float(np.median(r)) if len(r) >= 30 else 0.0
        disps = []
        for k in range(20, 60):
            b = arr[k - 20]
            cu = arr[k]
            rr = cu / b - 1.0
            rr = rr[np.isfinite(rr)]
            if len(rr) < 30:
                continue
            disps.append(float(np.std(rr)))
        pct_d = 0.5
        if disps:
            cur = disps[-1]
            below = sum(1 for d in disps if d <= cur)
            pct_d = below / len(disps)
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
        return mom, breadth, pct_d

    def select(self, view: DataView) -> list[Pick]:
        mom, breadth, pct_d = self._market_state(view)
        if mom < -0.03 or breadth < 0.40:
            return []
        if mom > 0.0:
            n_slots, weight = 5, 1.0
        else:
            if pct_d > 0.5:
                n_slots, weight = 1, 0.5
            else:
                n_slots, weight = 1, 0.3

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
                reason=f"市场动量{mom:.1%}、广度{breadth:.0%}→{n_slots}只×{weight}（中间区离散度{pct_d:.0%}分位）；个股20日涨幅{r.r20:.1%}、5日回调{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = UserPlanDispMid()
