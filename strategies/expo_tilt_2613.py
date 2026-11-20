"""假设：前四个择时变体（extreme_only_gate、wider_lower_weight、expo_curve_strong、expo_anti_chaos）一致证明——择时的价值在『减仓』而非『空仓』。expo_curve_strong 用 [0,1] 连续暴露拿到了本会话唯一的 train_max_dd (-41.2%) 达标，但 train 夏普仅 0.558，原因是长熊段暴露被压到接近 0，吃不到 train 反弹；expo_anti_chaos 把空仓推到极端又把 val 超额丢成 -3.85%。

本版改进：暴露函数向 0.6 倾斜（expo = 0.6 + 0.4 * 三因子平均），底仓约 0.4~1.0，绝不空仓，保留现任在 train 的牛市收益；入场打分改为双筛选而非加权分：20 日动量收益 rank 在上半区（保留中期趋势）并且 5 日收益 rank 在下半区（短期反转），这是两个独立条件，避免『20 日涨多但 5 日也涨多』的漏选——与现任（相减加权）在选股层面不同。波动率剃头（后 60%）保留以压回撤。

经济学理由：A 股是散户主导的过度反应市场，短期反转（5 日超跌）在中期趋势股里更可靠；中期趋势提供方向性 alpha，短期反转提供入场时点。择时只降仓不空仓，是因为 A 股长熊里反弹频繁且 T+1 下现金无法在盘中加回。

与现任差异：现任零择时+相减加权，本版择时降仓+双筛选交集。

预期失效：若 train 里 2018/2022 长熊被连续暴露压到 0.6 而回撤突破 -55%，说明该底仓太高；若中期趋势与短期反转在不同风格的截面里相关性高，双筛选交集可能过少导致候选池枯竭。"""
import math
import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class ExpoTilt:
    name = "expo_tilt_2613"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def _market(self, view: DataView):
        mom_vals = []
        breadth_ok = 0
        breadth_n = 0
        dd_vals = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, 60)
            if len(h) < 40 or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            if c.iloc[-1] <= 0:
                continue
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            if math.isfinite(r20):
                mom_vals.append(r20)
            ma60 = float(c.iloc[-60:].mean())
            breadth_n += 1
            if c.iloc[-1] > ma60:
                breadth_ok += 1
            h2 = view.history(code, 250)
            if len(h2) >= 120:
                hi = float(h2["high"].iloc[-250:].max())
                if hi > 0:
                    dd_vals.append(1.0 - float(c.iloc[-1]) / hi)
        mom20 = float(np.median(mom_vals)) if mom_vals else 0.0
        breadth = breadth_ok / breadth_n if breadth_n else 0.5
        dd250 = float(np.median(dd_vals)) if dd_vals else 0.0
        return mom20, breadth, dd250

    def _expo(self, mom20, breadth, dd250):
        e_mom = float(min(max((mom20 + 0.05) / 0.10, 0.0), 1.0))
        e_breadth = float(min(max((breadth - 0.20) / 0.40, 0.0), 1.0))
        e_dd = float(min(max((0.25 - dd250) / 0.20, 0.0), 1.0))
        base = (e_mom + e_breadth + e_dd) / 3.0
        return 0.60 + 0.40 * base

    def select(self, view: DataView) -> list[Pick]:
        mom20, breadth, dd250 = self._market(view)
        expo = self._expo(mom20, breadth, dd250)
        if expo >= 0.90:
            n_pick = 5
            weight = 0.50
        else:
            n_pick = 4
            weight = 0.35
        if expo < 0.50:
            n_pick = 3
            weight = 0.30

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
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if len(rows) < 20:
            return []
        df = pd.DataFrame(rows)
        r20_rank = df["r20"].rank(pct=True)
        r5_rank = df["r5"].rank(pct=True)
        sig_rank = df["sigma"].rank(pct=True)
        keep = (r20_rank > 0.50) & (r5_rank < 0.50) & (sig_rank <= 0.60)
        df = df[keep].copy()
        if df.empty:
            return []
        df["score"] = df["r20"].rank(pct=True) - df["r5"].rank(pct=True) + 0.15 * (1.0 - df["sigma"].rank(pct=True))
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)

        picks = []
        for r in df.itertuples(index=False):
            s = r.sigma
            stop_px = round(r.close * (1 - 1.5 * s), 2)
            target_px = round(r.close * (1 + 3.0 * s), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                weight=weight,
                reason=(f"expo={expo:.2f} mom20={mom20:.1%} breadth={breadth:.1%} dd250={dd250:.1%} "
                        f"档{n_pick}只×{weight:.2f}；20日涨幅{r.r20:.1%} 5日涨幅{r.r5:.1%} σ={s:.1%} "
                        f"止损{stop_px:.2f}/止盈{target_px:.2f} 持有5日")))
            if len(picks) >= n_pick:
                break
        return picks


STRATEGY = ExpoTilt()
