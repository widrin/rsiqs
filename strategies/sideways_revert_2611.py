"""假设：震荡市里指数本身不涨，个股的短期波动主要来自散户情绪与流动性冲击，存在可交易的均值回归。前任检验过的方向（牛市满仓动量、60日新高突破）在震荡段的年化夏普分别只有 0.09 与 -0.81，说明追强势在震荡市是负 alpha；反向做「超跌反弹」才符合震荡市的价格结构。

经济学理由：(1) A股散户占比高，短期下跌常由情绪与被动抛压驱动，缺乏基本面依据，随后被修正；(2) 震荡市没有单边趋势，追涨的动量资金缺少接力，超跌股的卖压反而更快衰竭；(3) 本系统按开盘价买入，隔夜恐慌性抛售后的次日开盘往往提供折价，契合「为过度反应提供流动性」的收益来源。

与现任差异：现任 vol_exit_fixedhold_2611 是「20日动量−5日反转」的追强逻辑，全周期满仓；本策略只在收盘跌破 20 日均线 1% 以上且 20 日跌幅未超过 25%（排除破位）的股票里，按「乖离深度 + 5 日跌幅」排序买入，目标回到 20 日均线，持有 5 日，并声明 target_regime='sideways'，只在震荡市的交易日上接受评估。

失效场景：(1) 震荡转为单边下跌（破位行情），超跌股继续下跌，止损被连续触发；(2) 震荡市里出现板块性趋势（如某一行业独立走强），反向选超跌会系统性错过领涨板块；(3) 极低波动期乖离不足，候选过少导致空仓、稀释绝对收益。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class SidewaysRevert:
    name = "sideways_revert_2611"
    target_regime = "sideways"
    top_n = 15
    min_rows = 65
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
            ma20 = float(c.iloc[-20:].mean())
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(ma20) and math.isfinite(r20) and math.isfinite(r5)):
                continue
            if ma20 <= 0:
                continue
            dev = close / ma20 - 1.0
            if dev > -0.01:
                continue
            if r20 < -0.25:
                continue
            dr = c.pct_change().dropna().iloc[-20:]
            if len(dr) < 10:
                continue
            sigma = float(dr.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": name, "close": close,
                         "dev": dev, "r5": r5, "ma20": ma20, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = (-df["dev"]).rank() + (-df["r5"]).rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - min(max(1.5 * sigma, 0.03), 0.07)), 2)
            target_px = round(max(r.ma20, r.close * 1.05), 2)
            entry_max = round(r.close * 1.02, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"跌破20日均线{r.dev:.1%}，5日跌幅{r.r5:.1%}，σ={sigma:.1%}，超跌反弹目标{target_px:.2f}，止损{stop_px:.2f}，持有5日"))
        return picks


STRATEGY = SidewaysRevert()
