"""假设：行业相对强度有真实轮动alpha，但相对强不等于绝对安全。当全池20日收益高度同向、横截面离散度很低且中位数为负时，行业排序主要反映共同下跌的beta差异，此时继续满仓会造成深回撤。本稿以全池r20中位数与离散度构造软风险过滤：中位数低于-8%且离散度低于6%时不开新仓，其余时间按广度从0.25到0.65线性给予1至5个新名额；旧仓自然退出。行业按20日等权收益相对全行业均值排序，取前五，个股沿用20日动量减5日反转、1.5σ/3σ出场和5日持有，单行业最多2只。与上任硬闸不同，它只在共同下跌且缺乏横截面分化时收缩，而非要求市场绝对上涨；与上一稿不同，新增离散度过滤以处理普跌。预期失效于行业分化本身由少数极端股造成，或市场快速V形反转时开仓恢复滞后。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDispersionSoft:
    name = "ind_rs_dispersion_soft_2612"
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
        r20_s = pd.Series(all_r20)
        med = float(r20_s.median())
        disp = float(r20_s.std())
        if not (math.isfinite(med) and math.isfinite(disp)):
            return []
        if med < -0.08 and disp < 0.06:
            return []
        breadth = upper / breadth_n
        exposure = min(max((breadth - 0.25) / 0.40, 0.0), 1.0)
        slots = int(math.floor(exposure * 5.0 + 0.5))
        if slots <= 0:
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
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        counts = {}
        for r in df.itertuples(index=False):
            used = counts.get(r.industry, 0)
            if used >= 2:
                continue
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(code=r.code, name=r.name, score=float(r.score),
                              entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                              reason=f"{r.industry}相对行业强度{rel_map[r.industry]:.1%}；市场r20中位数{med:.1%}、离散度{disp:.1%}；广度{breadth:.0%}，今日名额{slots}只"))
            counts[r.industry] = used + 1
            if len(picks) >= self.top_n:
                break
        return picks


STRATEGY = IndRsDispersionSoft()
