import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktHardGateEw:
    name = "mkt_hardgate_ew_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    idx_rows = 250
    dd_cut = 0.15
    mom_cut = -0.03

    def _mkt_proxy(self, view: DataView) -> pd.Series | None:
        vals = []
        for code in view.codes():
            h = view.history(code, self.idx_rows)
            if len(h) < 40:
                continue
            c = h["close"].astype(float)
            c = c[c > 0]
            if len(c) < 40:
                continue
            r = c.iloc[-1] / c.iloc[0] - 1
            if math.isfinite(r):
                vals.append(r)
        if len(vals) < 30:
            return None
        rets = []
        n = self.idx_rows
        for code in view.codes():
            h = view.history(code, n)
            if len(h) < 40:
                continue
            c = h["close"].astype(float)
            if c.iloc[-1] <= 0 or c.iloc[0] <= 0:
                continue
            r = float(c.iloc[-1] / c.iloc[0] - 1)
            if math.isfinite(r):
                rets.append(r)
        return pd.Series(rets) if rets else None

    def _regime(self, view: DataView) -> tuple[bool, float]:
        rows = []
        for code in view.codes():
            h = view.history(code, self.idx_rows)
            if len(h) < self.idx_rows // 2:
                continue
            c = h["close"].astype(float)
            if c.iloc[-1] <= 0:
                continue
            if c.iloc[0] <= 0:
                continue
            r = float(c.iloc[-1] / c.iloc[0] - 1)
            if math.isfinite(r):
                rows.append(r)
        if len(rows) < 30:
            return True, 1.0
        closes = []
        for code in view.codes():
            h = view.history(code, self.idx_rows)
            if len(h) < self.idx_rows:
                continue
            c = h["close"].astype(float)
            p = h["prev_close"].astype(float)
            rr = c.iloc[-1] / p.iloc[-1] - 1
            if math.isfinite(rr):
                closes.append(rr)
        if len(closes) < 30:
            return True, 1.0
        eq = [1.0]
        for x in closes:
            eq.append(eq[-1] * (1.0 + x))
        s = pd.Series(eq)
        last = float(s.iloc[-1])
        peak = float(s.max())
        dd = last / peak - 1.0
        ma120 = float(s.iloc[-120:].mean())
        mom20 = last / float(s.iloc[-21]) - 1.0 if len(s) >= 21 else 0.0
        bear = dd < -self.dd_cut or (last < ma120 and mom20 < self.mom_cut)
        return bear, mom20

    def select(self, view: DataView) -> list[Pick]:
        bear, mom = self._regime(view)
        if bear:
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
                              reason=f"强势区满仓；20日{r.r20:.1%}，5日{r.r5:.1%}，σ={sigma:.1%}"))
        return picks

    def select_wrap(self, view: DataView) -> list[Pick]:
        return self.select(view)


STRATEGY = MktHardGateEw()
