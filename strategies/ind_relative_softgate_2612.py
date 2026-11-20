"""假设：上一轮行业策略的深回撤可能来自把市场共同上涨误当行业alpha。本稿不直接按行业绝对20日收益排序，而是从每个行业收益中减去当日所有行业的等权收益，选择相对强度前五；因此下跌市场中也可配置相对抗跌行业，而非被迫等待绝对动量转正。新开仓采用温和软闸：60日区间上半部股票占比从0.2到0.7线性映射为0至5个名额，不另设二元市场开关；旧持仓自然到期。行业内仍用20日动量减5日反转，单行业最多2只，出场沿用现任1.5σ止损、3σ止盈和5日持有。与第一稿的差异是行业打分完全相对化且闸只使用广度，用于区分行业选择与仓位控制各自的贡献。预期失效于防御行业持续阴跌、相对强但绝对收益为负，以及市场快速反转时相对强行业切换滞后。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRelativeSoftGate:
    name = "ind_relative_softgate_2612"
    min_rows = 60
    min_amount = 1e8
    top_n = 15

    def select(self, view: DataView) -> list[Pick]:
        stocks = []
        ind_sum = {}
        ind_n = {}
        upper = 0
        valid_breadth = 0
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
                valid_breadth += 1
                if close >= 0.5 * (lo + hi):
                    upper += 1
            industry = view.industry(code)
            stocks.append({"code": code, "name": name, "industry": industry,
                           "close": close, "r20": r20, "r5": r5, "sigma": sigma})
            if industry is not None:
                ind_sum[industry] = ind_sum.get(industry, 0.0) + r20
                ind_n[industry] = ind_n.get(industry, 0) + 1
        if not stocks or valid_breadth < 100:
            return []
        breadth = upper / valid_breadth
        exposure = min(max((breadth - 0.20) / 0.50, 0.0), 1.0)
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
        market_ind_ret = sum(r["ret"] for r in ind_rows) / len(ind_rows)
        for r in ind_rows:
            r["relative"] = r["ret"] - market_ind_ret
        ind_df = pd.DataFrame(ind_rows).sort_values(["relative", "industry"], ascending=[False, True])
        leaders = set(ind_df.head(5)["industry"])
        relative_map = dict(zip(ind_df["industry"], ind_df["relative"]))
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
            rel = relative_map[r.industry]
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"{r.industry}20日相对全行业超额{rel:.1%}；个股20日{r.r20:.1%}、5日{r.r5:.1%}；广度{breadth:.0%}对应软暴露{exposure:.0%}，今日名额{slots}只"))
            counts[r.industry] = used + 1
            if len(picks) >= self.top_n:
                break
        return picks


STRATEGY = IndRelativeSoftGate()
