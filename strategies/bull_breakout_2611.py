"""假设：牛市里真正有 alpha 的是『新高突破』而不是『中期动量 + 短期回调』。前任的 20日动量−5日反转打分在牛市里会系统性地偏好在上升趋势中刚回调过的票（r5 越低越靠前），而回调在牛市往往只是噪声；相反，创 60 日新高、站上 60 日均线、20 日涨幅位于池内前 30% 的票承接的是关注度与增量资金，是牛市里最不容易被套的一段。

经济学理由：(1) 注意力效应——创新高会被行情软件和媒体反复推送，吸引散户与趋势资金接力买入；(2) 机构调仓的『上破加仓』规则使得新高附近买盘更厚；(3) 牛市段内反转因子收益为负，用 r5 排序会扣掉最有动量的票。

与现任差异：完全不同的入场因子（新高+趋势位置+相对强度），而非『中期动量减短期反转』的复合打分；出场沿用了波动率自适应 stop/target（1.5σ 止损、3σ 止盈、5 日持有），因为现任已证明这套出场对 train/val 都友好。

失效场景：假突破频发的震荡牛市（如 2017 年 4-6 月、2020 年 7 月冲高回落），新高票次日跌回的风险高，止损会吃掉收益；牛熊切换的顶部（广度已经在恶化但指数仍在 120 日均线上方）会接到最后一棒。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class BullBreakout:
    name = "bull_breakout_2611"
    target_regime = "bull"
    top_n = 15
    min_rows = 65
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        codes = view.codes()
        r20s = []
        breadth_hit = 0
        breadth_cnt = 0
        for code in codes:
            h = view.history(code, 65)
            if len(h) < 62 or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            if not (float(c.iloc[-21]) > 0):
                continue
            m = float(c.iloc[-1] / c.iloc[-21] - 1)
            ma = float(c.iloc[-60:].mean())
            if math.isfinite(m) and math.isfinite(ma):
                r20s.append(m)
                breadth_cnt += 1
                if float(c.iloc[-1]) > ma:
                    breadth_hit += 1
        if len(r20s) < 100:
            return []
        mom20 = float(pd.Series(r20s).median())
        breadth = breadth_hit / breadth_cnt if breadth_cnt > 0 else 0.0
        if not (mom20 > 0 and breadth > 0.5):
            return []
        p70 = float(pd.Series(r20s).quantile(0.70))

        rows = []
        for code in codes:
            nm = view.name(code)
            if is_st(nm):
                continue
            h = view.history(code, 62)
            if len(h) < 62 or h.index[-1].date() != view.asof:
                continue
            if not h["amount"].iloc[-20:].mean() >= self.min_amount:
                continue
            last = h.iloc[-1]
            close, prev = float(last["close"]), float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue
            up, _ = limit_prices(code, nm, prev, view.asof)
            if at_limit_up(close, up):
                continue
            c = h["close"]
            high = h["high"]
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            if not math.isfinite(r20) or r20 < p70:
                continue
            ma60 = float(c.iloc[-60:].mean())
            if not (float(c.iloc[-1]) > ma60):
                continue
            hh = float(high.iloc[-60:-1].max())
            if not (float(c.iloc[-1]) >= hh):
                continue
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            break_pct = close / hh - 1.0
            rows.append({"code": code, "name": nm, "close": close,
                         "r20": r20, "sigma": sigma, "brk": break_pct})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        # 新高票内部再用 r20 排一次，避免同类扎堆
        df = df.sort_values(["brk", "r20", "code"], ascending=[False, False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.r20 * 100),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"牛市确认(mom20={mom20:.1%},广度={breadth:.0%})；创60日新高+{r.r20:.1%}(20日涨幅在池内前30%)，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
        return picks


STRATEGY = BullBreakout()