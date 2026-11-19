import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktEWVolBudget:
    name = "mkt_ew_volbudget_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def _market_vol(self, view: DataView):
        rets = []
        for code in view.codes():
            h = view.history(code, 60)
            if len(h) < 60:
                continue
            c = h["close"]
            r = (c / c.shift(1) - 1).iloc[-25:]
            if len(r) >= 20 and math.isfinite(float(r.iloc[-1])):
                rets.append(r.reset_index(drop=True))
        if len(rets) < 20:
            return None
        s = pd.concat(rets, axis=1).mean(axis=1).fillna(0.0).iloc[-20:]
        if len(s) < 20:
            return None
        v = float(s.std())
        if not (math.isfinite(v) and v > 0):
            return None
        return v * math.sqrt(252)

    def select(self, view: DataView) -> list[Pick]:
        mvol = self._market_vol(view)
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
        if mvol is None:
            n_slots, weight, tag = 5, 1.0, "未知市况"
            mv_txt = 0.0
        else:
            mv_txt = mvol
            if mvol <= 0.18:
                n_slots, weight, tag = 5, 1.0, "低波市"
            elif mvol <= 0.26:
                n_slots, weight, tag = 3, 0.7, "中波市"
            else:
                n_slots, weight, tag = 2, 0.4, "高波市"
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(n_slots)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                weight=weight,
                reason=f"{tag}(大盘年化波动{mv_txt:.1%};暴露{n_slots}只×{weight:.2f})，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
        return picks


STRATEGY = MktEWVolBudget()