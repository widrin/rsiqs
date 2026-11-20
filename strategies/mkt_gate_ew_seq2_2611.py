"""假设：等权全池指数（按同一交易日横截面平均日收益、累乘为净值序列）是比沪深300更干净的可交易广度代理。用它做非对称硬闸门：距 120 日高点回撤 >12% 或 20 日动量 <-2% 时完全空仓（不试探、不留低仓），只有同时站上 120 日均线且 20 日动量 >0 才恢复 5 只×1.0 满仓；温和区（回撤 6%~12% 且动量 -2%~0）只留 1 只×0.5。

经济学理由：A 股等权小盘指数在熊市初期（跌破 120 日高点回撤 12%）通常领先沪深300权重指数走弱，因为流动性收缩先打击小票；在这个阶段继续持仓会被个股隔夜跳空连续吃掉净值。硬闸门（而非连续降仓）才能真正切断下跌途中的反复开仓，这是上一轮连续降仓变体 train 回撤 -88% 的主因。

与现任差异：入场打分（rank(r20)-rank(r5)）、σ 自适应 stop/target（1.5σ/3σ）、5 日持有完全沿用现任；只加一道基于等权指数时序的仓位开关。预期失效场景：V 型急速反弹（空仓期错过第一波拉升）、或市场在 120 日均线附近反复震荡（频繁进出），此时可用 1 只×0.5 温和区缓冲。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktGateEwSeq2:
    name = "mkt_gate_ew_seq2_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    idx_rows = 120
    dd_hard = 0.12
    dd_soft = 0.06
    mom_hard = -0.02

    def _proxy(self, view: DataView) -> pd.Series | None:
        cols = []
        for code in view.codes():
            h = view.history(code, self.idx_rows)
            if len(h) < 80:
                continue
            c = h["close"].astype(float)
            p = h["prev_close"].astype(float)
            if c.iloc[-1] <= 0 or p.iloc[-1] <= 0:
                continue
            r = c / p - 1.0
            r = r.replace([float("inf"), float("-inf")], float("nan")).dropna()
            if len(r) < 80:
                continue
            cols.append(r)
        if len(cols) < 30:
            return None
        panel = pd.concat(cols, axis=1, join="inner")
        if panel.shape[1] < 30 or panel.shape[0] < 80:
            return None
        mean_r = panel.mean(axis=1)
        eq = (1.0 + mean_r).cumprod()
        return eq

    def _state(self, view: DataView) -> str:
        s = self._proxy(view)
        if s is None or len(s) < 30:
            return "full"
        last = float(s.iloc[-1])
        peak = float(s.max())
        dd = last / peak - 1.0
        ma120 = float(s.iloc[-120:].mean()) if len(s) >= 120 else float(s.mean())
        mom20 = last / float(s.iloc[-21]) - 1.0 if len(s) >= 21 else 0.0
        if dd < -self.dd_hard or mom20 < self.mom_hard:
            return "off"
        if last > ma120 and mom20 > 0.0:
            return "full"
        if dd < -self.dd_soft:
            return "light"
        return "full"

    def select(self, view: DataView) -> list[Pick]:
        state = self._state(view)
        if state == "off":
            return []
        max_n = 5 if state == "full" else 1
        weight = 1.0 if state == "full" else 0.5
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
            rows.append({"code": code, "name": name, "close": close, "r20": r20, "r5": r5, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(max_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(code=r.code, name=r.name, score=float(r.score),
                              entry_max=round(r.close * 1.03, 2), stop=stop_px, target=target_px,
                              hold_days=5, weight=weight,
                              reason=f"等权指数闸门[{state}]，{max_n}只×{weight:.1f}；20日{r.r20:.1%}，5日{r.r5:.1%}，σ={sigma:.1%}"))
        return picks


STRATEGY = MktGateEwSeq2()
