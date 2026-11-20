"""假设：震荡市缺乏持续指数趋势时，资金更偏向容量大、可进出的高流动性防御股票，而价格波动收敛表示短期分歧和交易冲击下降。本策略先用20日平均成交额做横截面排名，只保留前20%的高容量股票，再要求最近20日平均真实振幅低于此前20日、收盘不低于60日均线且20日收益非负。这里的高成交额是横截面上的资金容量代理，不是上一轮已证伪的『相对自身历史缩量』；波动收敛也不是直接押注突破，而是与趋势未破坏共同刻画防御状态。该效应可能未被完全套利，因为大盘防御票单次收益较小、在快速牛市明显落后，且机构需要承担相对排名风险。与现任动量−反转策略相比，本策略不使用短期涨幅差打分；与纯低波策略相比，核心暴露是高流动性与资金容量，低波仅由振幅收敛辅助确认。采用10日持有和较窄固定止损止盈以降低换手，不做内部大盘择时。预期在高成交额实际对应高beta热门股、蓝筹持续估值压缩或波动收敛后向下选择方向时失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class SidewaysLiquidContract:
    name = "sideways_liquid_contract_2611"
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
            amount20 = float(amounts.iloc[-20:].mean())
            if not (math.isfinite(amount20) and amount20 >= self.min_amount):
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
            ma60 = float(c.iloc[-60:].mean())
            prev_c = h["prev_close"]
            tr1 = h["high"] - h["low"]
            tr2 = (h["high"] - prev_c).abs()
            tr3 = (h["low"] - prev_c).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1) / prev_c
            amp_recent = float(tr.iloc[-20:].mean())
            amp_prior = float(tr.iloc[-40:-20].mean())
            if not all(math.isfinite(x) for x in (r20, ma60, amp_recent, amp_prior)):
                continue
            if r20 < 0 or close < ma60 or amp_prior <= 0 or amp_recent >= amp_prior:
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "amount20": amount20,
                         "contract": amp_recent / amp_prior})
        if len(rows) < 20:
            return []
        df = pd.DataFrame(rows)
        amount_cut = float(df["amount20"].quantile(0.80))
        df = df[df["amount20"] >= amount_cut].copy()
        if df.empty:
            return []
        df["score"] = df["amount20"].rank() - 0.5 * df["contract"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            entry_max = round(r.close * 1.02, 2)
            stop_px = round(r.close * 0.95, 2)
            target_px = round(r.close * 1.08, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px,
                hold_days=10,
                reason=f"20日均额{r.amount20 / 1e8:.1f}亿元，位于高流动性前20%；近20日振幅/此前20日={r.contract:.2f}，20日涨幅{r.r20:.1%}且不低于60日均线，持有10日"))
        return picks


STRATEGY = SidewaysLiquidContract()
