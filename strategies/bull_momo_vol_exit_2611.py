"""假设：现任 vol_exit_fixedhold_2611 的『20日动量−5日反转』打分与波动率自适应出场在牛市确实有 alpha，但在 2018/2022 等熊市段满仓只是在贡献负样本。本策略把同一套入场与出场逻辑保留，但加上自身的牛市确认（全池 20 日收益中位数为正 且 站上 60 日均线占比>0.5），不满足时返回 [] 空仓；同时声明 target_regime='bull'，评估器只在牛市日打分，就能回答『这套动量在它设计要赚钱的环境里是否真的赚钱』。

经济学理由：中期动量是机构资金持续买入的痕迹，牛市段的动量延续概率高于均值回归；而熊市里同样的打分会被系统性负漂移打穿止损。两者是同一因子的两种环境实现，不应该混在一个口径里评估。

与现任差异：入场打分、出场方式（σ 自适应 stop/target、持有5日）完全相同，唯一差别是牛熊确认 + target_regime 声明。所以它是对『口径』的干净对照：若它过门而现任全周期口径不过门，说明门卡在熊市段而不是选股本身。

失效场景：极端牛市后段（如 2021 年初抱团松动），广度指标会滞后，可能在牛市日末段满仓被砸；以及市场状态识别本身（120日均线+20日动量）与实际拐点偏差时会误判，导致在牛市日空仓、错过部分收益。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class BullMomoVolExit:
    name = "bull_momo_vol_exit_2611"
    target_regime = "bull"
    top_n = 15
    min_rows = 65
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        # 先算全池代理：20 日收益中位数 + 站上 60 日均线占比
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
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": nm, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
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
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"牛市确认(mom20={mom20:.1%},广度={breadth:.0%})；20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
        return picks


STRATEGY = BullMomoVolExit()