"""假设：前任一系列大盘闸门策略因「等权指数代理算错」或「每天pd.concat 800只×120日重建序列超时」而失败，导致train最大回撤仍达-88%。本版用正确且轻量的大盘代理：单次遍历股票池，取每只股票最近20日累计收益，以其中位数作为市场20日动量；再统计收盘站上自身60日均线的股票占比作为广度。两个统计量都是单次 O(股票数×窗口) 遍历，不concat、不重建序列。闸门规则：只有市场20日动量>0 且 广度>55%（强市）才开5只×1.0；其余一律空仓。经济理由：弱市/普跌时动量选股的超额被beta吞掉，短期反转在下跌趋势中往往是接刀；识别市场状态能从源头砍掉系统性回撤段。与现任差异：不改入场打分与σ自适应出场，只在外部叠加市场状态硬闸门。预期失效：震荡市中20日动量与广度阈值频繁穿越，来回切换导致踏空反弹与换手成本。"""

import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktProxyGateBinary:
    name = "mkt_proxy_gate_binary_2612"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    mom_win = 20
    ma_win = 60

    def _market(self, view: DataView):
        """单次遍历：返回 (市场20日动量中位数, 站上60日均线占比)。"""
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
        strong = (mom > 0.0) and (breadth > 0.55)
        if not strong:
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
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"强市闸门(市场20日动量{mom:.1%},广度{breadth:.0%})下选中：20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={r.sigma:.1%}"))
        return picks


STRATEGY = MktProxyGateBinary()
