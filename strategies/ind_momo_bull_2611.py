"""假设：行业动量+行业内动量−反转的 alpha 主要在牛市兑现，熊市满仓行业轮动是 ind_momo_leader_2611 回撤−73.8%的根源。本稿声明 target_regime='bull'，评估口径改为牛市日的超额收益（策略−沪深300），入场加自身牛市确认：全池等权指数收盘高于120日均线且指数20日动量为正时才给出候选，其余时间空仓。这样把「能不能在牛市跑赢指数」与「熊市择时对不对」解耦，前者是选股问题、后者交给行情口径。与 bull_momo_vol_exit_2611 的差异：那稿用全池个股打分，本稿先选前5强行业再在行业内选龙头，行业层提供独立alpha。预期失效：牛市里强行业已拥挤、超额被beta淹没（bull_relstrength_2612 已提示绝对动量在牛市跑不赢指数的风险），若本稿牛市超额仍为负，说明行业动量需要改成相对强度口径。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoBull:
    name = "ind_momo_bull_2611"
    target_regime = "bull"
    top_n = 15
    min_rows = 130
    min_amount = 1e8
    top_ind = 5

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        ind_ret = {}
        mkt_close = []
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
            ind = view.industry(code) or "未知"
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma, "ind": ind})
            ind_ret.setdefault(ind, []).append(r20)
            mkt_close.append(c)
        if not rows or len(mkt_close) < 50:
            return []
        mkt = pd.concat(mkt_close, axis=1).mean(axis=1)
        ma120 = float(mkt.iloc[-120:].mean())
        now = float(mkt.iloc[-1])
        mkt_r20 = float(mkt.iloc[-1] / mkt.iloc[-21] - 1)
        if not (ma120 > 0 and math.isfinite(now) and math.isfinite(mkt_r20)):
            return []
        if not (now > ma120 and mkt_r20 > 0):
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
                reason=f"牛市确认(指数>MA120且20日动量{mkt_r20:.1%})，{r.ind}行业动量前{self.top_ind}，个股20日{r.r20:.1%}/5日{r.r5:.1%}，σ={sigma:.1%}止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = IndMomoBull()
