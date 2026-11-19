"""假设：现任 vol_exit_fixedhold_2611 的入场打分（20日动量−5日反转）与 σ 自适应出场（stop=close·(1−1.5σ)、target=close·(1+3σ)、固定5日）已被验证为方向正确且 val 三项硬指标达标，唯一失败项是训练段最大回撤过深（-66%~-79% vs 门槛 -45%）。根因是策略无任何市场状态感知，在 2018/2022 系统性下跌段仍满仓 5 只高动量股，这些股在熊市里 beta 高、动量迅速反转。
本版不改入场与出场，只用股票池横截面代理构造大盘状态：对全池取每日中位数收盘价序列（不依赖任何指数代码，指数不在 view.codes() 内），计算 (a) close/ma120−1 趋势、(b) 20 日动量 close/close[-21]−1。双指标均≥0 判为强势（5 只 × weight 1.0）；仅一项≥0 判为中性（3 只 × 1.0）；双负判为弱势（2 只 × weight 0.7）。回测与模拟盘只取前 5 个 Pick，减少 Pick 数量与 weight 缩放都能真实降低暴露。
与现任差异：新增市场状态择时层，持仓数量与单票仓位随大盘强弱变化。
失效场景：横截面中位数无法代表大盘（极端分化行情中小盘独立走强而指数弱）时会过早降仓损失超额；ma120 在快速 V 型反转中滞后，反转初期可能仍处弱势档。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktRegimeExpo:
    name = "mktregime_expo_2611"
    top_n = 15
    min_rows = 130
    min_amount = 1e8

    def _market_state(self, view: DataView) -> tuple[bool, bool]:
        closes = []
        for code in view.codes():
            h = view.history(code, 130)
            if len(h) >= 130:
                closes.append(h["close"])
        if len(closes) < 20:
            return True, True
        med = pd.concat(closes, axis=1).median(axis=1)
        if len(med) < 130:
            return True, True
        c = float(med.iloc[-1])
        ma = float(med.iloc[-120:].mean())
        if not (math.isfinite(c) and math.isfinite(ma) and ma > 0):
            return True, True
        trend_up = c / ma - 1 >= 0
        mom20 = c / float(med.iloc[-21]) - 1
        mom_up = mom20 >= 0
        return trend_up, mom_up

    def select(self, view: DataView) -> list[Pick]:
        trend_up, mom_up = self._market_state(view)
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
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        if trend_up and mom_up:
            n_slots, weight, regime = 5, 1.0, "强势"
        elif trend_up or mom_up:
            n_slots, weight, regime = 3, 1.0, "中性"
        else:
            n_slots, weight, regime = 2, 0.7, "弱势"
        df = df.head(min(n_slots, 5))
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
                reason=f"{regime}市况(降/升暴露至{n_slots}只,权重{weight:.0f})，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
        return picks


STRATEGY = MktRegimeExpo()
