"""假设：上一轮 fast_or_lowweight_2612 的失败在于『或』连接的两个快变量阈值太松、单票权重压得太低（0.3~0.4），导致 train 牛市段大量时间被现金拖累，train 夏普从现任 0.68 掉到 0.53。诊断显示 DSR 在 n_trials=39 时等价于 train+val 拼接夏普，而 train 占 86% 天数，所以必须守住 train 收益。

本版改用用户方案：只在真正极端（dd250>25% 或 breadth<25% 或 mom20<-8%）清仓；温和档放宽到 3只×0.7（比上一轮 2只×0.5 现金拖累小）；强势档满仓 5只×1.0。选股与出场完全沿用现任（20日动量 rank − 5日反转 rank，止损 1.5σ、止盈 3σ、持有 5 日），只改仓位择时。

经济学理由：A股牛市里大部分时间都该在仓，择时信号应只识别真正的系统性深跌（2018Q4、2022Q1、2024Q1）；快变量（20日动量）噪声大、误杀多，只在极端时用。与现任差异：现任完全不择时，本版只在极端清仓，其余时间满仓或温和档，回撤略优而收益损失可控。

预期失效场景：若 train 里 2015-2024 的深跌都被 dd250/breadth/mom20 三条件同时漏检（比如 2015 股灾、2020 疫情初期这类下跌快但回撤未走完就反弹），train 最大回撤会突破 -45%；若 2019/2020 这类慢牛里 breadth 频繁跌破 25%，也会稀释收益。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class ExtremeOnlyGate:
    name = "extreme_only_gate_2612"
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
        mom20 = float(pd.Series(mom_vals).median()) if mom_vals else 0.0
        breadth = breadth_ok / breadth_n if breadth_n else 0.5
        dd250 = float(pd.Series(dd_vals).median()) if dd_vals else 0.0
        return mom20, breadth, dd250

    def _tier(self, mom20, breadth, dd250):
        if dd250 > 0.25 or breadth < 0.25 or mom20 < -0.08:
            return 0, 0.0
        if mom20 > 0.0 and breadth > 0.55:
            return 5, 1.0
        return 3, 0.7

    def select(self, view: DataView) -> list[Pick]:
        mom20, breadth, dd250 = self._market(view)
        n_pick, weight = self._tier(mom20, breadth, dd250)
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
                reason=(f"市场代理 mom20={mom20:.1%} breadth={breadth:.1%} dd250={dd250:.1%} 档位{n_pick}只×{weight:.1f}；"
                        f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={s:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日")))
            if len(picks) >= n_pick:
                break
        return picks


STRATEGY = ExtremeOnlyGate()
