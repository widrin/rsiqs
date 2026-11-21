"""假设：ind_rs_adaptive3_2611 的 train 侧已经达标（夏普 0.599、回撤 -20.3%），但 val 超额 -4.4%，
怀疑中性档「单行业≤1 + rel20>0」在震荡上行段过于严格、踏空。本稿只放宽一处：中性档单行业上限
由 1 改为 2（防守档仍为 1），其余全部与 adaptive3 相同——三档模板阈值、每日 1 只、不设 weight、
1.5σ/3σ/5日持有、0.30/0.22 深回撤刹车都不变。
预期：用 train 回撤的余量（-20.3% vs 门槛 -45%）换取 val 期参与度，val 超额转正；若 val 超额仍为负，
说明问题不在集中度而在防守档的择时滞后。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsAdaptive4:
    name = "ind_rs_adaptive4_2611"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    daily_picks = 1
    dd_stop = 0.30
    dd_slow = 0.22

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        dds = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            hs = view.history(code, self.slow_rows)
            if len(hs) < self.min_rows or hs.index[-1].date() != view.asof:
                continue
            if not hs["amount"].iloc[-20:].mean() >= self.min_amount:
                continue
            h = hs.iloc[-self.min_rows:]
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
            r60 = float(c.iloc[-1] / c.iloc[0] - 1)
            ma60 = float(c.mean())
            daily_ret = c.pct_change().dropna()
            sigma20 = float(daily_ret.iloc[-20:].std())
            sigma60 = float(daily_ret.std())
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(r60)
                    and math.isfinite(ma60) and math.isfinite(sigma20)
                    and math.isfinite(sigma60) and ma60 > 0 and sigma20 > 0 and sigma60 > 0):
                continue
            peak = float(hs["close"].max())
            if math.isfinite(peak) and peak > 0:
                dds.append(max(0.0, 1.0 - close / peak))
            industry = view.industry(code) or "未知"
            rows.append({"code": code, "name": name, "industry": industry, "close": close,
                         "r20": r20, "r5": r5, "r60": r60, "above60": close > ma60,
                         "sigma20": sigma20, "sigma60": sigma60})
        if len(rows) < 50 or len(dds) < 50:
            return []

        df = pd.DataFrame(rows)
        breadth = float(df["above60"].mean())
        mkt20 = float(df["r20"].median())
        r20_med = mkt20
        sigma_med = float(df["sigma60"].median())
        dd_med = float(pd.Series(dds).median())
        if not (math.isfinite(breadth) and math.isfinite(dd_med)):
            return []
        if dd_med >= self.dd_stop:
            return []
        if dd_med >= self.dd_slow and view.asof.toordinal() % 2 == 1:
            return []

        defensive = breadth <= 0.40 and mkt20 <= -0.02
        offensive = breadth >= 0.55 or mkt20 >= 0.02
        regime = "进攻" if offensive and not defensive else ("防守" if defensive else "中性")

        ind = df.groupby("industry", as_index=False).agg(
            ind_r20=("r20", "median"), ind_r60=("r60", "median"))
        ind["ind_score"] = ind["ind_r20"].rank(pct=True) + ind["ind_r60"].rank(pct=True)
        if regime == "防守":
            ind = ind.sort_values(["ind_r20", "industry"], ascending=[False, True]).head(8)
        else:
            ind = ind.sort_values(["ind_score", "industry"], ascending=[False, True]).head(5)

        df = df[df["industry"].isin(set(ind["industry"]))].copy()
        if df.empty:
            return []
        if regime != "进攻":
            df = df[df["r20"] > r20_med]
        if regime == "防守":
            df = df[df["sigma60"] <= sigma_med]
        if df.empty:
            return []

        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])

        # 与 adaptive3 的唯一差异：中性档单行业上限 1 → 2
        industry_cap = 1 if regime == "防守" else 2
        picks = []
        counts = {}
        for r in df.itertuples(index=False):
            if counts.get(r.industry, 0) >= industry_cap:
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
                reason=(f"{regime}档：广度{breadth:.0%}、全池20日中位{mkt20:+.1%}，"
                        f"{r.industry}行业相对强度入选（单行业上限{industry_cap}），个股20日{r.r20:+.1%}、"
                        f"5日{r.r5:+.1%}，60日波动{r.sigma60:.1%}，全池回撤中位{dd_med:.1%}，"
                        f"σ止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日")))
            counts[r.industry] = counts.get(r.industry, 0) + 1
            if len(picks) >= self.daily_picks:
                break
        return picks


STRATEGY = IndRsAdaptive4()
