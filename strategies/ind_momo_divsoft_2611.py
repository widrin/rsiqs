"""假设：ind_momo_leader_2611 的行业等权20日动量+行业内rank(r20)-rank(r5)打分有效（val超额15.3%），回撤来自单行业集中与大盘系统性下跌段的满仓暴露。本稿不动打分，只做两处风控：(1) 单行业最多2只进入候选前列，降低行业黑天鹅与拥挤回撤；(2) 用全池等权指数相对120日均线的偏离做连续仓位映射——指数在均线上方给满15只候选，跌破后按偏离度线性缩减候选数（最少1只），实现软减仓而非二元空仓，避免均线附近反复切换的换手成本。与现任差异：现任无行业层、无择时；与 ind_momo_leader 差异：加了行业配额与软市场闸。预期失效：软闸在V型反转中减仓踏空、行业配额把最强行业的龙头挤出导致alpha稀释。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoDivSoft:
    name = "ind_momo_divsoft_2611"
    top_n = 15
    min_rows = 130
    min_amount = 1e8
    top_ind = 5
    per_ind = 2

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
        if not rows:
            return []
        expo = 1.0
        if len(mkt_close) >= 50:
            mkt = pd.concat(mkt_close, axis=1).mean(axis=1)
            ma120 = float(mkt.iloc[-120:].mean())
            now = float(mkt.iloc[-1])
            if ma120 > 0 and math.isfinite(now):
                dev = now / ma120 - 1.0
                expo = min(1.0, max(0.1, 1.0 + dev / 0.10)) if dev < 0 else 1.0
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
        cap = max(1, int(round(self.top_n * expo)))
        picked = []
        ind_cnt = {}
        for r in df.itertuples(index=False):
            if len(picked) >= cap:
                break
            if ind_cnt.get(r.ind, 0) >= self.per_ind:
                continue
            ind_cnt[r.ind] = ind_cnt.get(r.ind, 0) + 1
            picked.append(r)
        picks = []
        for r in picked:
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"{r.ind}行业20日动量前{self.top_ind}，个股20日{r.r20:.1%}/5日{r.r5:.1%}，市场暴露{expo:.0%}，单行业≤{self.per_ind}只，σ={sigma:.1%}止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = IndMomoDivSoft()
