"""假设：本会话前两轮证明硬空仓本身不足以把 train 最大回撤压到 -45% 以内（hardempty_triple -72%、hardempty_score -79%），主因是每个 Pick 默认按约 1/5 初始资金成交，满仓 5 只时任何一只被 T+1 跳空打穿即造成巨大单笔损失，累计回撤超过闸门能保护的范围。本变体做两件事：(1) 硬空仓只用两个快变量——全池 20 日累计收益中位数 mom20 与收盘价站上自身 60 日均线的股票占比 breadth，mom20<-3% 或 breadth<0.35 时返回 [] 完全持币；dd250 是慢变量，在 2018Q4/2022Q1 急跌初期不会超过 20%，放进『或』条件会在震荡市误杀却在急跌时滞后，因此完全弃用。(2) 满仓时把单票 weight 压到 0.3（5只×0.3，总暴露 1.5 而不是 5.0），半仓 2只×0.4，让单票占初始资金从 1/5 降到约 1/10 量级，直接砍掉单笔回撤贡献。经济学理由：个股动量的超额来自横截面分化，系统性普跌时中位数动量转负、广度崩塌，此时个股 alpha 抵不过 beta，应完全离场；而单票 1/5 的巨大敞口是长熊里回撤的主要来源，靠空仓无法及时覆盖跳空段。与现任 vol_exit_fixedhold_2611 的差异：现任无整体仓位管理、单票 1/5、无空仓；本策略有快变量硬空仓 + 全时段降权。预期失效场景：2020-02 类 V 型急跌 breadth 在 1-2 日内跌破 35%，空仓会滞后一天；牛市段降权会减少收益，可能压低 val 夏普。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class FastOrLowWeight:
    name = "fast_or_lowweight_2612"
    top_n = 15
    min_rows = 61
    min_amount = 1e8
    w_full = 0.3
    w_half = 0.4

    def _proxy(self, view: DataView):
        mom_list = []
        n_on = 0
        n_total = 0
        for code in view.codes():
            if is_st(view.name(code)):
                continue
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            close = float(h["close"].iloc[-1])
            if not (math.isfinite(close) and close > 0):
                continue
            n_total += 1
            r20 = float(h["close"].iloc[-1] / h["close"].iloc[-21] - 1)
            if math.isfinite(r20):
                mom_list.append(r20)
            ma60 = float(h["close"].iloc[-61:].mean())
            if close >= ma60:
                n_on += 1
        if n_total == 0 or not mom_list:
            return 0.0, 1.0
        med_mom = float(pd.Series(mom_list).median())
        breadth = n_on / n_total
        return med_mom, breadth

    def select(self, view: DataView) -> list[Pick]:
        med_mom, breadth = self._proxy(view)
        if med_mom < -0.03 or breadth < 0.35:
            return []
        if breadth < 0.50 or med_mom < 0.0:
            max_picks = 2
            weight = self.w_half
            regime = f"半仓(mom20 {med_mom:.1%}/广度 {breadth:.0%})"
        else:
            max_picks = 5
            weight = self.w_full
            regime = f"满仓(mom20 {med_mom:.1%}/广度 {breadth:.0%})"

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
                stop=stop_px, target=target_px, hold_days=5, weight=weight,
                reason=f"{regime}→单票{weight:.0%}；20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
            if len(picks) >= max_picks:
                break
        return picks


STRATEGY = FastOrLowWeight()
