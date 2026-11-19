"""假设：A 版 mktregime_expo_2611 用「ma120 趋势 + 20 日动量」双条件分三档控仓。但 ma120 是 120 日平滑，在系统性下跌的拐点与 V 型反转处严重滞后，可能让策略在熊市初期仍停留于强势档。本版改用更敏感、更单一的代理：全池中位数收盘价的 20 日动量（close/close[-21]−1，不依赖指数代码）。
按动量强度连续分三档，降暴露更激进：(1) mom20 ≥ 0 强势 → 5 只 × weight 1.0；(2) −5% ≤ mom20 < 0 中性 → 4 只 × weight 0.8；(3) mom20 < −5% 弱势 → 2 只 × weight 0.45。弱势日暴露仅为满仓的约 18%，预期能更深地压住训练段最大回撤（目标 ≥ -45%），代价是动量刚翻负的短暂假信号期会错失部分反弹。
入场打分与 σ 自适应出场与现任完全一致，只在持仓数量/权重上做差异，便于与 A 版做归因对比。
与现任差异：单指标 20 日大盘动量 + 连续降暴露。
失效场景：20 日动量在高波动震荡市频繁正负切换，导致仓位来回跳、暴露不稳；若下跌是慢跌（每月 −2% 但持续数年），20 日动量可能长期在 -5% 附近反复，档位抖动。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktMomScaled:
    name = "mktmom_scaled_2611"
    top_n = 15
    min_rows = 30
    min_amount = 1e8

    def _market_mom(self, view: DataView) -> float | None:
        closes = []
        for code in view.codes():
            h = view.history(code, 25)
            if len(h) >= 25:
                closes.append(h["close"])
        if len(closes) < 20:
            return None
        med = pd.concat(closes, axis=1).median(axis=1)
        if len(med) < 21:
            return None
        c = float(med.iloc[-1])
        p = float(med.iloc[-21])
        if not (math.isfinite(c) and math.isfinite(p) and p > 0):
            return None
        return c / p - 1

    def select(self, view: DataView) -> list[Pick]:
        mom20 = self._market_mom(view)
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
        if mom20 is None:
            n_slots, weight, regime = 5, 1.0, "未知"
        elif mom20 >= 0:
            n_slots, weight, regime = 5, 1.0, "强势"
        elif mom20 >= -0.05:
            n_slots, weight, regime = 4, 0.8, "中性"
        else:
            n_slots, weight, regime = 2, 0.45, "弱势"
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(min(n_slots, 5))
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
                reason=f"{regime}市况(大盘20日{mom20:.1%}，暴露{n_slots}只,权重{weight:.2f})，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
        return picks


STRATEGY = MktMomScaled()
