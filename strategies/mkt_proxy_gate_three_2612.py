"""假设：前任大盘闸门策略因代理算法错误或超时而失败。本版沿用正确且轻量的大盘代理（全池近20日累计收益中位数的市场动量 + 站上自身60日均线占比的广度，单次遍历、不concat），但把闸门分三档：弱市（市场20日动量<-3% 或 广度<40%）直接空仓；强市（市场20日动量>0 且 广度>55%）5只×1.0；温和区（介于两者之间）仅 1只×0.3 极低仓位。经济理由：完全空仓会踏空温和市中的结构性反弹，而弱市中的动量/反转信号胜率很低且换手成本高，三档能以极低成本保留温和暴露同时砍掉系统性回撤段。与现任差异：仅叠加市场状态仓位控制，不改入场打分与σ自适应出场。预期失效：震荡市中 20 日动量与广度在阈值附近反复穿越，温和区低仓换手仍产生成本，且阈值本身可能滞后。"""

import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktProxyGateThree:
    name = "mkt_proxy_gate_three_2612"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    mom_win = 20
    ma_win = 60

    def _market(self, view: DataView):
        r20 = []
        above = 0
        total = 0
        for code in view.codes():
            h = view.history(code, self.ma_win)
            if len(h) < self.ma_win:
                continue
            c = h["close"]
            v = float(c.iloc[-1])
            if not (math.isfinite(v) and v > 0):
                continue
            r = float(c.iloc[-1] / c.iloc[-1 - self.mom_win] - 1)
            ma = float(c.iloc[-self.ma_win:].mean())
            if not (math.isfinite(r) and math.isfinite(ma) and ma > 0):
                continue
            r20.append(r)
            if v >= ma:
                above += 1
            total += 1
        if not r20 or total == 0:
            return None, None
        mom = float(pd.Series(r20).median())
        breadth = above / total
        return mom, breadth

    def select(self, view: DataView) -> list[Pick]:
        mom, breadth = self._market(view)
        if mom is None or breadth is None:
            return []
        weak = (mom < -0.03) or (breadth < 0.40)
        strong = (mom > 0.0) and (breadth > 0.55)
        if weak:
            return []
        if strong:
            n_max = 5
            weight = 1.0
        else:
            n_max = 1
            weight = 0.3
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
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(n_max)
        picks = []
        for r in df.itertuples(index=False):
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            tier = "强市" if strong else "温和"
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                weight=weight,
                reason=f"{tier}闸门(市场20日动量{mom:.1%},广度{breadth:.0%})下选中：20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={r.sigma:.1%}"))
        return picks


STRATEGY = MktProxyGateThree()
