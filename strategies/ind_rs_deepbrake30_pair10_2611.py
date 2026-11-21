"""假设：与 ind_rs_deepbrake30_2612 完全同构，仅把每日新开仓名额从 1 只改为 2 只、并给每只 Pick.weight=0.1。

权重口径（engine/broker.py:128）：weight 是占总资金比例，budget = total_capital * weight；
基线不设 weight 时走 capital_per_pick = 总资金/5（每票 20%），稳态 5 只 × 20% = 100%。
本稿 2 只/日 × 5 日持有 = 10 只 × 10% = 100%，稳态总暴露与基线相同，只把持仓从 5 只分散到 10 只。
预期：总暴露不变则回撤不应恶化；个股特异性风险减半，train 夏普有望抬过 0.5。若 train 夏普反而下降，
说明个股打分的 alpha 集中在排名第 1 的票上、第 2 名是噪音。
"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepBrake30Pair10:
    name = "ind_rs_deepbrake30_pair10_2611"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    top_industries = 5
    max_per_industry = 2
    dd_stop = 0.30
    dd_slow = 0.22
    daily_picks = 2
    pick_weight = 0.1

    def select(self, view: DataView) -> list[Pick]:
        dds = []
        r20s = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.slow_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            close = float(c.iloc[-1])
            if not (math.isfinite(close) and close > 0):
                continue
            hi = float(c.max())
            if math.isfinite(hi) and hi > 0:
                dds.append(1.0 - close / hi)
            if len(c) >= 21:
                base = float(c.iloc[-21])
                if math.isfinite(base) and base > 0:
                    r20s.append(close / base - 1.0)
        if len(dds) < 50 or len(r20s) < 50:
            return []
        dd_med = float(pd.Series(dds).median())
        if not math.isfinite(dd_med):
            return []
        if dd_med >= self.dd_stop:
            return []
        if dd_med >= self.dd_slow and view.asof.toordinal() % 2 == 1:
            return []
        r20_med = float(pd.Series(r20s).median())
        if not math.isfinite(r20_med):
            return []
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            ind = view.industry(code)
            if not ind:
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
            r60 = float(c.iloc[-1] / c.iloc[0] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(r60)):
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": name, "ind": ind, "close": close,
                         "r20": r20, "r5": r5, "r60": r60, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        ind20 = df.groupby("ind")["r20"].mean()
        ind60 = df.groupby("ind")["r60"].mean()
        ind_score = (ind20 - ind20.median()) + (ind60 - ind60.median())
        strong = set(ind_score.sort_values(ascending=False).head(self.top_industries).index)
        df = df[df["ind"].isin(strong)].copy()
        if df.empty:
            return []
        df["rel20"] = df["r20"] - r20_med
        df["score"] = df["rel20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        used = {}
        for r in df.itertuples(index=False):
            if used.get(r.ind, 0) >= self.max_per_industry:
                continue
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            used[r.ind] = used.get(r.ind, 0) + 1
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                weight=self.pick_weight,
                reason=f"{r.ind}相对行业强度居前，20日涨幅{r.r20:.1%}（相对全池中位数{r.rel20:.1%}），5日涨幅{r.r5:.1%}，全池回撤中位数{dd_med:.1%}，每票10%分散持仓，σ={r.sigma:.1%}，持有5日"))
            if len(picks) >= self.daily_picks:
                break
        return picks


STRATEGY = IndRsDeepBrake30Pair10()
