"""
假设：熊市日要靠『低波动 + 相对抗跌 + 浅回撤』选防御票，而不是满仓动量。

经济学理由：(1) 熊市里指数负漂移主导，满仓动量被系统性打穿止损；而低波动、距近期高点回撤浅的票在熊市里有『避险资金 + 分红稳定 + 机构底仓』支撑，跌幅显著小于指数，等于相对超额；(2) T+1 与开盘滑点约束下，低波动票隔夜跳空风险更小，止损摩擦更少；(3) 用 20 日跌幅浅（r20 池内前 30%）筛选『相对抗跌』，同时用 σ 池内后 40% 剔除高波动票，两者互相验证。

与现任差异：入场完全改为防御/抗跌因子（低波动 + 浅回撤 + 相对抗跌），出场把止损收紧到 1.2σ、止盈放宽到 2.5σ（小赢就跑、亏小止损），持有 3 日（熊市信号快进快出）；并且只在熊市确认（mom20 < 0 且 breadth < 0.5）时选股，其他行情返回 []。

失效场景：(1) 熊市反抽（如 2019 年初、2024 年 9 月），低波动票在 V 型反弹里跑输——但 target_regime='bear' 与内部熊市确认都会大幅避免此时满仓；(2) 熊市确认滞后：用 mom20<0 + breadth<0.5 双确认约滞后 2 周，可能在熊市末期误判；(3) 极端系统性风险（如 2015 年股灾后期、2018 年 Q4），低波动也会普跌，此时只能靠更紧的止损减少损失。
"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class BearDefensive:
    name = "bear_defensive_2612"
    target_regime = "bear"
    top_n = 15
    min_rows = 65
    min_amount = 5e7

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
        if not (mom20 < 0 and breadth < 0.5):
            return []
        r20_p30 = float(pd.Series(r20s).quantile(0.30))

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
            if not math.isfinite(r20):
                continue
            # 相对抗跌：r20 位于池内前 30%（跌幅较浅）
            if r20 < r20_p30:
                continue
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            # 低波动：σ 池内后 40%（这里先收集后统一比较；推迟排序）
            sigma = min(max(sigma, 0.01), 0.06)
            # 距 60 日高点回撤
            hh60 = float(h["high"].iloc[-60:].max())
            dd60 = close / hh60 - 1.0 if hh60 > 0 else 0.0
            rows.append({"code": code, "name": nm, "close": close,
                         "r20": r20, "sigma": sigma, "dd60": dd60})
        if len(rows) < 40:
            return []
        df = pd.DataFrame(rows)
        # 低波动门槛：σ 位于池内后 40%
        sig_p60 = float(df["sigma"].quantile(0.60))
        cand = df[df["sigma"] <= sig_p60]
        if len(cand) < 5:
            cand = df
        # 打分：抗跌（r20 高）+ 低波动（σ 低）+ 回撤浅
        d2 = cand.copy()
        d2["score"] = d2["r20"].rank(ascending=True) - 0.5 * d2["sigma"].rank(ascending=True) + 0.3 * d2["dd60"].rank(ascending=True)
        d2 = d2.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in d2.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.2 * sigma), 2)
            target_px = round(r.close * (1 + 2.5 * sigma), 2)
            if not stop_px < r.close * 1.02 < target_px:
                # 若 target 过远，放宽 target 至 close*1.06
                target_px = round(r.close * 1.06, 2)
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.02, 2),
                stop=stop_px, target=target_px, hold_days=3,
                reason=f"熊市防御(mom20={mom20:.1%},广度={breadth:.0%})；相对抗跌20日{r.r20:.1%}，σ={sigma:.1%}(低波动组)，距60日高点{r.dd60:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有3日"))
        return picks


STRATEGY = BearDefensive()
