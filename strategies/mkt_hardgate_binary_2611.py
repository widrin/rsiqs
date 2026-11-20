import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktHardGateBinary:
    name = "mkt_hardgate_binary_2611"
    top_n = 14
    min_rows = 60
    min_amount = 1e8
    idx_rows = 250
    dd_ok = 0.10

    def _regime(self, view: DataView) -> bool:
        rr = []
        for code in view.codes():
            h = view.history(code, self.idx_rows)
            if len(h) < 200:
                continue
            c = h["close"].astype(float)
            p = h["prev_close"].astype(float)
            x = c.iloc[-1] / p.iloc[-1] - 1
            if c.iloc[-1] <= 0 or not math.isfinite(x):
                continue
            rr.append(x)
        if len(rr) < 30:
            return False
        eq = [1.0]
        for x in rr:
            eq.append(eq[-1] * (1.0 + x))
        s = pd.Series(eq)
        last = float(s.iloc[-1])
        peak = float(s.max())
        dd = last / peak - 1.0
        ma120 = float(s.iloc[-120:].mean())
        mom20 = last / float(s.iloc[-21]) - 1.0
        strong = last > ma120 and mom20 > 0.0 and -dd < self.dd_ok
        return strong

    def select(self, view: DataView) -> list[Pick]:
        if not self._regime(view):
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
            rows.append({"code": code, "name": name, "close": close, "r20": r20, "r5": r5, "sigma": sigma})
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
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(code=r.code, name=r.name, score=float(r.score),
                              entry_max=round(r.close * 1.03, 2), stop=stop_px, target=target_px,
                              hold_days=5, weight=1.0,
                              reason=f"二元强市闸门开启，4只满仓；20日{r.r20:.1%}，5日{r.r5:.1%}，σ={sigma:.1%}"))
        return picks


STRATEGY = MktHardGateBinary()
