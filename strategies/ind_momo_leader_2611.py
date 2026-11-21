"""假设：A股行业收益存在中期动量——政策/产业景气与机构调仓以周为单位推进，资金在行业间迁移不是一天完成，因此近20日等权涨幅领先的行业，其后数日仍有相对强度；该效应未被完全套利，因为行业轮动需要跨行业比较与组合化执行，散户难以做、机构受基准约束调仓慢。策略：先按申万一级行业等权20日收益排序取前5个行业，再在这些行业内用现任的 rank(r20)−rank(r5) 选个股，出场沿用现任的波动率自适应止损止盈与5日持有。与现任差异：现任在全池直接比较个股，20日涨幅里混入行业beta，本策略把行业选择显式前置，只在强势行业内部做个股动量−反转。预期失效：存量博弈、行业快速轮动的市场（如2016-2017、2023下半年）行业动量反转为行业反转，强行业买入即站岗。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoLeader:
    name = "ind_momo_leader_2611"
    top_n = 15
    top_ind = 5
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            ind = view.industry(code)
            if ind is None:
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
            rows.append({"code": code, "name": name, "ind": ind, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        ind_r20 = df.groupby("ind")["r20"].mean().sort_values(ascending=False)
        strong = set(ind_r20.head(self.top_ind).index)
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
                reason=f"{r.ind}行业20日等权涨幅{ind_r20[r.ind]:.1%}居前{self.top_ind}，个股20日{r.r20:.1%}/5日{r.r5:.1%}，σ={sigma:.1%}，强行业内动量回调龙头"))
        return picks


STRATEGY = IndMomoLeader()
