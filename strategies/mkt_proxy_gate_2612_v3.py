"""假设：上轮 mkt_proxy_gate_binary_2612 用正确轻量的等权代理（全池20日累计收益中位数的市场动量 + 站上自身60日均线占比的广度）成功压低了 train 回撤(-44.7%)，但 train 夏普仅0.41、val 超额-0.15%，根因有二：(1) 强市条件「广度>55%」过严——2025 单边上涨行情大部分时间广度在 40%~55% 之间，导致闸门长期空仓，踏空上涨、val 超额为负；(2) 弱市条件「动量<-3%或广度<40%」在熊市反弹中频繁穿越，反复空仓/满仓切换。本版修正：(a) 强市满仓只要「市场20日动量>0」；(b) 弱市硬空仓只在「动量<-5% 或 广度<45%」，更极端弱势才空仓；(c) 其余温和区开 3 只×0.8。经济理由：市场短期动量转正意味着系统性 beta 友好，广度只是以及时确认的工具，不宜做硬性入场门槛；广度崩塌(<45%)才真正对应系统性卖压。与现任差异：不改入场打分与 σ 自适应出场，只叠加市场状态仓位控制。预期失效：震荡市中动量在正负之间频繁穿越，温和区 0.8 仓位的换手成本增加。"""

import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktProxyGateV3:
    name = "mkt_proxy_gate_2612_v3"
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
        weak = (mom < -0.05) or (breadth < 0.45)
        strong = mom > 0.0
        if weak:
            return []
        if strong:
            n_max = 5
            weight = 1.0
        else:
            n_max = 3
            weight = 0.8
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


STRATEGY = MktProxyGateV3()
