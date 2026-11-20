"""假设：震荡市的量价 alpha 来自抛压衰减，而非追涨或超跌接刀。前期分歧与恐慌交易会放大成交额，当最近5日平均成交额降至此前20日均值的60%以下，同时近5日不再创20日新低、20日动量非负且价格位于20日均线上方时，更可能表示浮筹清洗和卖方耗尽。该效应未被完全套利，是因为缩量既可能是企稳也可能是关注度流失，信号需要价格稳定共同确认，且收益分散在大量小额交易中。本策略以缩量幅度为主、20日强度为辅排序，与现任的动量−反转打分及低波策略的方差排序均不同，不做内部大盘择时。预期在缩量演变为长期流动性枯竭、市场进入放量单边趋势或防御板块集中补跌时失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class SidewaysDryUp:
    name = "sideways_dryup_2611"
    target_regime = "sideways"
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
            amounts = h["amount"]
            if not amounts.iloc[-20:].mean() >= self.min_amount:
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
            ma20 = float(c.iloc[-20:].mean())
            low20 = float(c.iloc[-20:].min())
            low5 = float(c.iloc[-5:].min())
            if not all(math.isfinite(x) for x in (r20, ma20, low20, low5)):
                continue
            if r20 < 0 or close < ma20 or low5 <= low20 * 1.001:
                continue
            amount5 = float(amounts.iloc[-5:].mean())
            amount_prior20 = float(amounts.iloc[-25:-5].mean())
            if not (math.isfinite(amount5) and math.isfinite(amount_prior20) and amount_prior20 > 0):
                continue
            dry_ratio = amount5 / amount_prior20
            if dry_ratio > 0.60:
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "dry_ratio": dry_ratio})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = -df["dry_ratio"].rank() + 0.25 * df["r20"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            entry_max = round(r.close * 1.02, 2)
            stop_px = round(r.close * 0.94, 2)
            target_px = round(r.close * 1.09, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px,
                hold_days=10,
                reason=f"近5日均额/此前20日均额={r.dry_ratio:.2f}，近5日未创20日新低，20日涨幅{r.r20:.1%}且站上20日均线；缩量企稳持有10日"))
        return picks


STRATEGY = SidewaysDryUp()
