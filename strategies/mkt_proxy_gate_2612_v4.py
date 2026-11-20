"""假设：本会话前几轮已解决大盘代理算法与性能问题（单次遍历全池20日累计收益中位数作为市场动量 + 站上自身60日均线占比作为广度，不concat、不重建序列），且第2轮 mkt_proxy_gate_2612_v3 证明「动量>0 才满仓、动量<-5% 或广度<45% 才空仓」这套宽松闸门在 val 段是对的（val 夏普1.44、val超额44.5%），但 train 回撤 -76% 依然严重超标。诊断：回撤不是来自弱市空仓，而是来自温和区（动量在 -5%~0）给了 3只×0.8 = 总暴露 2.4 的高仓位，A 股 2018/2022 长熊中动量反复在 -5%~0 之间震荡（熊市反弹），这段区间满打满算高仓位，系统性回撤全从这里漏进来。本版修正：温和区改为只开 1 只×1.0（集中暴露最强的1只、砍掉3份选股误差）；弱市（动量<-5% 或 广度<45%）仍完全空仓；强市（动量>0）5只×1.0 满仓。经济理由：温和震荡市里beta和alpha都不稳，分散3只×0.8只会放大选股误差、不产生alpha；集中1只×1.0 更省、回撤更小、且保留少量alpha暴露不至于踏空反弹。与现任差异：不改入场打分与σ自适应出场，只叠加市场状态仓位控制。预期失效：温和区集中在单只标的会放大个股波动，若那只标的恰在熊市里领跌则回撤反而更集中在单日；2025 上涨行情大部分时间动量>0（强市），温和区影响小，val 超额应能保住。"""

import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MktProxyGateV4:
    name = "mkt_proxy_gate_2612_v4"
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
        weak_tight = (mom < -0.03) or (breadth < 0.50)
        strong = mom > 0.0
        if weak:
            return []
        if strong:
            n_max = 5
            weight = 1.0
        elif weak_tight:
            return []
        else:
            n_max = 1
            weight = 1.0
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
            tier = "强市" if strong else "温和集中"
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                weight=weight,
                reason=f"{tier}闸门(市场20日动量{mom:.1%},广度{breadth:.0%})下选中：20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={r.sigma:.1%}"))
        return picks


STRATEGY = MktProxyGateV4()
