"""假设：上一轮 mkt_hardgate_binary_2611 用 250 日高点 + 10% 回撤阈值太慢，闸门在熊市启动后很久才关闭，导致 train 回撤 -90%。本版沿用「真实日频等权指数序列」（横截面日收益对齐累乘，而非单日快照重复），把高点窗口缩短到 120 日、回撤阈值收紧到 8%、动量阈值提到 >0，且完全去掉温和区低仓位：只要不在强市四条件（站上120日线、20日动量>0、距120日高点回撤<8%）就一律空仓。

经济学理由：等权指数相对 120 日高点的回撤是「近期可交易广度恶化」的直接度量；一旦超过 8% 说明个股层面已经普遍走弱，即使权重指数未跌，个股动量也会失效。严格空仓的好处是不必在弱市中反复开仓、反复止损（换手成本与隔夜跳空都在吃净值），这直接转化为更小的最大回撤。

与现任差异：入场打分与 σ 自适应出场沿用现任，但用硬闸门完全替代仓位管理；与变体 A 的差异是去掉温和区、把回撤阈值从 12% 收紧到 8%。预期失效场景：2016 年牛市初期、2019/2024 震荡上行期间，指数可能长期在 120 日均线附近徘徊，导致大量时间空仓、错过趋势段，train 夏普被拖到很低。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktGateEwStrict2:
    name = "mkt_gate_ew_strict2_2611"
    top_n = 12
    min_rows = 60
    min_amount = 1e8
    idx_rows = 120
    dd_ok = 0.08

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
        eq = (1.0 + panel.mean(axis=1)).cumprod()
        return eq

    def _strong(self, view: DataView) -> bool:
        s = self._proxy(view)
        if s is None or len(s) < 30:
            return True
        last = float(s.iloc[-1])
        peak = float(s.max())
        dd = last / peak - 1.0
        ma120 = float(s.iloc[-120:].mean()) if len(s) >= 120 else float(s.mean())
        mom20 = last / float(s.iloc[-21]) - 1.0 if len(s) >= 21 else 0.0
        return last > ma120 and mom20 > 0.0 and -dd < self.dd_ok

    def select(self, view: DataView) -> list[Pick]:
        if not self._strong(view):
            return []
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
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(5)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(code=r.code, name=r.name, score=float(r.score),
                              entry_max=round(r.close * 1.03, 2), stop=stop_px, target=target_px,
                              hold_days=5, weight=1.0,
                              reason=f"严格强市闸门开启（120日均线上方+动量正+回撤<8%）；20日{r.r20:.1%}，5日{r.r5:.1%}，σ={sigma:.1%}"))
        return picks


STRATEGY = MktGateEwStrict2()
