import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class AlphaMomoVolAdd:
    name = "alpha_momo_voladd_2612"
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
            amt = h["amount"]
            a20 = float(amt.iloc[-20:].mean())
            if not (math.isfinite(a20) and a20 >= self.min_amount):
                continue
            a60 = float(amt.iloc[-60:].mean())
            if not (math.isfinite(a60) and a60 > 0):
                continue
            amt_ratio = a20 / a60
            if not math.isfinite(amt_ratio):
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
                         "r20": r20, "r5": r5, "sigma": sigma, "amt_ratio": amt_ratio})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["rank20"] = df["r20"].rank()
        df["rank5"] = df["r5"].rank()
        df["rank_sig"] = df["sigma"].rank()
        df["rank_amt"] = df["amt_ratio"].rank()
        df["score"] = df["rank20"] - df["rank5"] - 0.5 * df["rank_sig"] + 0.5 * df["rank_amt"]
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，20日均额/60日均额={r.amt_ratio:.2f}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有{hold}日"))
        return picks


STRATEGY = AlphaMomoVolAdd()
