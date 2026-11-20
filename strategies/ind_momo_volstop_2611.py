"""假设：ind_momo_leader_2611 的大回撤有两个候选来源——市场系统性下跌（需要择时）与个股尾部亏损+行业集中（需要出场与分散）。本稿作为对照实验：完全不加市场闸、恒定满仓，只把单行业限制为≤2只、止损从1.5σ收紧到1.0σ、止盈从3σ降到2.5σ（更快落袋、缩短尾部暴露）、entry_max从1.03降到1.02（减少开盘追高滑点）。若本稿能把 train/val max_dd 压进门槛，说明无需择时、保留全部牛市收益；若仍深亏，则下一轮集中资源做软市场闸。与现任差异：加行业层与行业配额、更紧的波动自适应出场。预期失效：1.0σ止损在T+1下被日内噪声频繁扫出，换手成本上升侵蚀alpha。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoVolStop:
    name = "ind_momo_volstop_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    top_ind = 5
    per_ind = 2

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        ind_ret = {}
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
        if not rows:
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
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picked = []
        ind_cnt = {}
        for r in df.itertuples(index=False):
            if len(picked) >= self.top_n:
                break
            if ind_cnt.get(r.ind, 0) >= self.per_ind:
                continue
            ind_cnt[r.ind] = ind_cnt.get(r.ind, 0) + 1
            picked.append(r)
        picks = []
        for r in picked:
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.0 * sigma), 2)
            target_px = round(r.close * (1 + 2.5 * sigma), 2)
            if not stop_px < r.close * 1.02 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.02, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"{r.ind}行业动量前{self.top_ind}，单行业≤{self.per_ind}只，个股20日{r.r20:.1%}/5日{r.r5:.1%}，紧止损1.0σ={stop_px:.2f}/止盈2.5σ={target_px:.2f}，持有5日"))
        return picks


STRATEGY = IndMomoVolStop()
