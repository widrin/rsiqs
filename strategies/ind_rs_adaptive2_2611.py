"""假设：这是三档自适应策略的受控对照。行业 rel20+rel60 排名、个股 rel20−5日反转、每日1只、1.5σ/3σ出场、5日持有和0.30/0.22深度回撤刹车均与主稿一致；进攻档也完全相同。唯一差异是不满足进攻条件时，无论中性还是防守，都只要求个股 rel20 高于全池中位数并限制单行业1只，不放宽到rel20前8行业，也不增加60日低波过滤。若主稿优于本稿，才支持弱势市场采用低波防御模板；若两稿接近，则适应性收益主要来自相对强度过滤和行业集中度约束。预期在快速反转或防御股补跌时，两种模板都可能落后于原进攻模板。"""
import math

import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsAdaptive2:
    name = "ind_rs_adaptive2_2611"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    top_n = 15

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        slow_dd = []
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
            ma60 = float(c.mean())
            daily_ret = c.pct_change().dropna()
            sigma20 = float(daily_ret.iloc[-20:].std())
            sigma60 = float(daily_ret.std())
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(ma60)
                    and math.isfinite(sigma20) and math.isfinite(sigma60) and sigma20 > 0
                    and sigma60 > 0 and ma60 > 0):
                continue
            industry = view.industry(code)
            if industry is None:
                industry = "未知"
            rows.append({"code": code, "name": name, "industry": industry,
                         "close": close, "r20": r20, "r5": r5,
                         "above60": close > ma60, "sigma20": sigma20,
                         "sigma60": sigma60})
            hs = view.history(code, self.slow_rows)
            if len(hs) >= self.min_rows and hs.index[-1].date() == view.asof:
                cs = hs["close"]
                peak = float(cs.max())
                last_slow = float(cs.iloc[-1])
                if math.isfinite(peak) and math.isfinite(last_slow) and peak > 0 and last_slow > 0:
                    slow_dd.append(max(0.0, 1.0 - last_slow / peak))
        if not rows:
            return []
        df = pd.DataFrame(rows)
        breadth = float(df["above60"].mean())
        mkt20 = float(df["r20"].median())
        dd_med = float(np.median(slow_dd)) if slow_dd else 0.0
        if dd_med >= 0.30:
            return []
        if dd_med >= 0.22 and view.asof.toordinal() % 2 == 0:
            return []
        defensive = breadth <= 0.40 and mkt20 <= -0.02
        offensive = breadth >= 0.55 or mkt20 >= 0.02
        if offensive and not defensive:
            regime = "进攻"
        elif defensive:
            regime = "防守"
        else:
            regime = "中性"
        ind = df.groupby("industry", as_index=False).agg(ind_r20=("r20", "median"))
        r60_rows = []
        for code in df["code"]:
            h = view.history(code, self.min_rows)
            c = h["close"]
            industry = view.industry(code)
            if industry is None:
                industry = "未知"
            r60 = float(c.iloc[-1] / c.iloc[0] - 1)
            if math.isfinite(r60):
                r60_rows.append({"code": code, "industry": industry, "r60": r60})
        if not r60_rows:
            return []
        r60_df = pd.DataFrame(r60_rows)
        ind60 = r60_df.groupby("industry", as_index=False).agg(ind_r60=("r60", "median"))
        ind = ind.merge(ind60, on="industry", how="inner")
        ind["ind_r20_rank"] = ind["ind_r20"].rank(pct=True)
        ind["ind_r60_rank"] = ind["ind_r60"].rank(pct=True)
        ind["ind_score"] = ind["ind_r20_rank"] + ind["ind_r60_rank"]
        ind = ind.sort_values(["ind_score", "industry"], ascending=[False, True]).head(5)
        df = df.merge(ind[["industry", "ind_score"]], on="industry", how="inner")
        if df.empty:
            return []
        med20 = float(df["r20"].median())
        if regime != "进攻":
            df = df[df["r20"] > med20]
        if df.empty:
            return []
        df["r20_rank"] = df["r20"].rank(pct=True)
        df["r5_rank"] = df["r5"].rank(pct=True)
        df["score"] = df["r20_rank"] - df["r5_rank"]
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        industry_count = {}
        industry_cap = 2 if regime == "进攻" else 1
        for r in df.itertuples(index=False):
            if industry_count.get(r.industry, 0) >= industry_cap:
                continue
            sigma = min(max(float(r.sigma20), 0.01), 0.06)
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"{regime}档：广度{breadth:.1%}，全池20日中位{mkt20:.1%}；{r.industry}行业相对强度入选，个股20日{r.r20:.1%}、5日{r.r5:.1%}，60日波动{r.sigma60:.1%}，1.5σ止损/3σ止盈，持有5日"))
            industry_count[r.industry] = industry_count.get(r.industry, 0) + 1
            if len(picks) >= self.top_n:
                break
        return picks


STRATEGY = IndRsAdaptive2()
