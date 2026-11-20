"""
假设：牛市里满仓绝对动量会被 beta 冒充 alpha。

上一轮 bull_momo_vol_exit_2611 与 bull_breakout_2611 在 bull 交易日口径上夏普都很好（train 1.65/1.07，val 3.06/3.61），max_dd 也都远在 -0.45 内，但 gate 都没过，卡点全是 train 超额 -0.144 与 -0.410——满仓 5×1.0 吃的是牛市 beta，而选股打分带来的相对收益在 train 段甚至为负。

经济学理由：(1) 牛市 alpha 的本质是持续跑赢指数，绝对 r20 排序会把靠 beta 抬起来的票和真 alpha 票混在一起，必须用相对强度筛；(2) 要求 20 日收益 > 5 日收益，剔除刚被短期爆拉、马上会均值回归的票（A 股牛市末尾常见）；(3) 低波动过滤作为软门槛，用『20 日波动率不超过池内 70 分位』作为入场条件，避免止损被高波动票频繁打穿。

与现任差异：入场打分从 rank(r20)−rank(r5) 改为『相对市场强度 + 趋势未见短期爆拉』的组合打分；出场沿用现任已经验证有效的波动自适应 stop/target（1.5σ 止损、3σ 止盈、持有 5 日）。

失效场景：牛市中反弹末端市场宽度快速恶化（广度较 mom20 滞后 2-3 周），或风格极端轮动（如 2021 年初抱团松动），此时相对强度票会被高 beta 砸穿止损。
"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class BullRelStrength:
    name = "bull_relstrength_2612"
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
        # 相对强度阈值：rel20 位于池内前 30%
        rel_pool = [x - mom20 for x in r20s]
        rel_p70 = float(pd.Series(rel_pool).quantile(0.70))
        sig_p70 = None
        sigma_pool = []
        for code in codes:
            h = view.history(code, 25)
            if len(h) < 25 or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            s = float(dr.std())
            if math.isfinite(s) and s > 0:
                sigma_pool.append(s)
        if len(sigma_pool) >= 100:
            sig_p70 = float(pd.Series(sigma_pool).quantile(0.70))

        rows = []
        for code in codes:
            nm = view.name(code)
            if is_st(nm):
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
            up, _ = limit_prices(code, nm, prev, view.asof)
            if at_limit_up(close, up):
                continue
            c = h["close"]
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            # 趋势未见短期爆拉：20 日涨幅 > 5 日涨幅
            if not (r20 > r5):
                continue
            rel20 = r20 - mom20
            if rel20 < rel_p70:
                continue
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            if sig_p70 is not None and sigma > sig_p70:
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": nm, "close": close,
                         "r20": r20, "r5": r5, "rel20": rel20, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["rel20"].rank() - 0.5 * df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"牛市确认(mom20={mom20:.1%},广度={breadth:.0%})；相对市场强度{rel20:+.1%}(池内前30%)，20日{r.r20:.1%}、5日{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
        return picks


STRATEGY = BullRelStrength()
