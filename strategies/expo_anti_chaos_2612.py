import math
import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class ExpoAntiChaos:
    name = "expo_anti_chaos_2612"
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

    def _state(self, mom20, breadth, dd250):
        if breadth > 0.65 and dd250 < 0.10 and mom20 > -0.02:
            return 5, 0.55
        if breadth > 0.50 and dd250 < 0.18 and mom20 < 0.0:
            return 2, 0.30
        if breadth > 0.35 and dd250 < 0.25 and mom20 > 0.02:
            return 1, 0.20
        return 0, 0.0

    def select(self, view: DataView) -> list[Pick]:
        mom20, breadth, dd250 = self._state_check(view)
        n_pick, weight = self._state(mom20, breadth, dd250)
        if n_pick == 0 or weight <= 0:
            return []

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
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        if "sigma" in df.columns:
            vol_cut = float(df["sigma"].quantile(0.60))
            df = df[df["sigma"] <= vol_cut]

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
                reason=(f"state={n_pick}只×{weight:.2f} mom20={mom20:.1%} breadth={breadth:.1%} dd250={dd250:.1%}；"
                        f"20日涨幅{r.r20:.1%} 5日涨幅{r.r5:.1%} σ={s:.1%} "
                        f"止损{stop_px:.2f}/止盈{target_px:.2f} 持有5日")))
            if len(picks) >= n_pick:
                break
        return picks

    def _state_check(self, view: DataView):
        return self._market(view)


STRATEGY = ExpoAntiChaos()
