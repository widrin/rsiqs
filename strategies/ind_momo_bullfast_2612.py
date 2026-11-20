"""假设：行业动量+行业内动量−反转的 alpha 主要在牛市兑现，熊市满仓行业轮动是回撤根源。本稿声明 target_regime='bull'，评估口径改为牛市日超额收益（策略−沪深300），把「牛市能否跑赢指数」与「熊市择时」解耦。入场用轻量横截面统计做牛市确认：广度（收盘价>自身120日均线的股票占比）>0.5 且全池 r20 中位数>0，两者同时满足才满仓给出候选，否则空仓。与上一轮超时的 ind_momo_bull_2611 的差异：那稿每天对800只股票各取130行再 pd.concat 成等权指数导致超时，本稿改用横截面标量统计（广度+中位数），每天只需 O(N) 次运算、无拼接，计算量降低一个数量级。与 ind_momo_hardgate_2612 的差异：那稿是全周期绝对收益口径，本稿是牛市日超额口径，专门检验行业轮动在牛市的相对价值。预期失效：牛市里强行业已拥挤、超额被beta淹没（bull_relstrength_2612 已提示绝对动量在牛市跑不赢指数的风险）；若本稿牛市超额为负，说明行业动量需要改成相对强度口径。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoBullFast:
    name = "ind_momo_bullfast_2612"
    target_regime = "bull"
    top_n = 15
    min_rows = 125
    min_amount = 1e8
    top_ind = 5

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        ind_ret = {}
        above_ma = 0
        total = 0
        r20_list = []
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
            ma120 = float(c.iloc[-120:].mean())
            total += 1
            if close > ma120:
                above_ma += 1
            r20_list.append(r20)
            ind = view.industry(code) or "未知"
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma, "ind": ind})
            ind_ret.setdefault(ind, []).append(r20)
        if not rows or total < 50:
            return []
        breadth = above_ma / total
        mkt_r20 = float(pd.Series(r20_list).median())
        if not (breadth > 0.5 and mkt_r20 > 0):
            return []
        ind_score = {k: float(pd.Series(v).mean()) for k, v in ind_ret.items() if len(v) >= 3}
        if not ind_score:
            return []
        strong = sorted(ind_score, key=lambda k: ind_score[k], reverse=True)[:self.top_ind]
        df = pd.DataFrame(rows)
        df = df[df["ind"].isin(strong)].copy()
        if df.empty:
            return []
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"牛市确认(广度{breadth:.0%},中位r20={mkt_r20:.1%})，{r.ind}行业动量前{self.top_ind}，个股20日{r.r20:.1%}/5日{r.r5:.1%}，σ={sigma:.1%}止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = IndMomoBullFast()
