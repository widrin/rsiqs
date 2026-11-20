"""假设：上一稿val超额为正但回撤仍深，部分原因可能是强行业内仍按绝对r20追逐高beta个股，并且5日持有迫使行业轮动频繁换手。本稿同时相对化行业和个股：行业取20日等权收益减全行业均值的前五，个股打分为自身r20减当日全池r20中位数，再减去短期5日反转排名；持有期延长到20日，止损2σ、止盈4σ，让中期行业趋势有时间兑现并减少交易。仅在60日区间上半部占比不低于0.25时开仓，广度从0.25到0.65映射为1至5个新名额，极弱市场空仓。与现任差异是双重相对强度和低换手行业配置，而非继续调整紧止损。预期失效于行业趋势持续期短于20日、慢出场扩大个股尾部损失，或长期相对抗跌但绝对收益持续为负。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsSlowRelative:
    name = "ind_rs_slow_relative_2612"
    min_rows = 60
    min_amount = 1e8
    top_n = 15

    def select(self, view: DataView) -> list[Pick]:
        stocks = []
        ind_sum = {}
        ind_n = {}
        all_r20 = []
        upper = 0
        breadth_n = 0
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
            close = float(last["close"])
            prev = float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue
            c = h["close"]
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            rets = c.pct_change().dropna().iloc[-20:]
            if len(rets) < 10 or not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            sigma = float(rets.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            lo = float(c.min())
            hi = float(c.max())
            if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
                breadth_n += 1
                if close >= 0.5 * (lo + hi):
                    upper += 1
            all_r20.append(r20)
            industry = view.industry(code)
            stocks.append({"code": code, "name": name, "industry": industry,
                           "close": close, "r20": r20, "r5": r5, "sigma": sigma})
            if industry is not None:
                ind_sum[industry] = ind_sum.get(industry, 0.0) + r20
                ind_n[industry] = ind_n.get(industry, 0) + 1
        if len(all_r20) < 100 or breadth_n < 100:
            return []
        breadth = upper / breadth_n
        if breadth < 0.25:
            return []
        exposure = min(max((breadth - 0.25) / 0.40, 0.0), 1.0)
        slots = int(math.floor(exposure * 5.0 + 0.5))
        if slots <= 0:
            return []
        med = float(pd.Series(all_r20).median())
        if not math.isfinite(med):
            return []
        ind_rows = []
        for industry, total in ind_sum.items():
            count = ind_n[industry]
            if count >= 3:
                ind_rows.append({"industry": industry, "ret": total / count})
        if len(ind_rows) < 5:
            return []
        market = sum(r["ret"] for r in ind_rows) / len(ind_rows)
        for r in ind_rows:
            r["relative"] = r["ret"] - market
        ind_df = pd.DataFrame(ind_rows).sort_values(["relative", "industry"], ascending=[False, True])
        leaders = set(ind_df.head(5)["industry"])
        rel_map = dict(zip(ind_df["industry"], ind_df["relative"]))
        df = pd.DataFrame(stocks)
        df = df[df["industry"].isin(leaders)].copy()
        if df.empty:
            return []
        df["relative_stock"] = df["r20"] - med
        df["score"] = df["relative_stock"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        counts = {}
        for r in df.itertuples(index=False):
            used = counts.get(r.industry, 0)
            if used >= 2:
                continue
            stop_px = round(r.close * (1 - 2.0 * r.sigma), 2)
            target_px = round(r.close * (1 + 4.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(code=r.code, name=r.name, score=float(r.score),
                              entry_max=entry_max, stop=stop_px, target=target_px, hold_days=20,
                              reason=f"{r.industry}相对行业强度{rel_map[r.industry]:.1%}；个股相对全池20日强度{r.relative_stock:.1%}、5日{r.r5:.1%}；广度{breadth:.0%}，20日低换手持有"))
            counts[r.industry] = used + 1
            if len(picks) >= self.top_n:
                break
        return picks


STRATEGY = IndRsSlowRelative()
