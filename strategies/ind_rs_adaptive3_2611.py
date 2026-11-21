"""假设：行业 rel20+rel60 动量与个股 rel20−5日反转在强势市场有效，但弱势市场继续选高弹性强动量股会放大回撤。
用当日横截面 breadth（收盘高于自身60日均线占比）与 mkt20（全池20日收益中位数）把选股分三档：进攻档沿用基线；
中性档要求个股 rel20 高于全池中位数且单行业≤1；防守档把行业放宽到 rel20 前8、要求相对强度为正且只选
60日日收益波动率处于全池下半区的低波股。每日仍只开 1 只、不设 weight（broker 按总资金/5 即 20% 建仓），
出场 1.5σ/3σ/5日持有，深回撤刹车 0.30/0.22 不变，不新增二元空仓。
与上一版（超时版）的差异：每只票只取一次 250 日历史（原版取 3 次导致评估超过 30 分钟），且每日名额固定 1 只
（原版 top_n=15 会一次开 15 只，暴露不可控）。
"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsAdaptive3:
    name = "ind_rs_adaptive3_2611"
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

        industry_cap = 2 if regime == "进攻" else 1
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
                        f"{r.industry}行业相对强度入选，个股20日{r.r20:+.1%}、5日{r.r5:+.1%}，"
                        f"60日波动{r.sigma60:.1%}，全池回撤中位{dd_med:.1%}，σ止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日")))
            counts[r.industry] = counts.get(r.industry, 0) + 1
            if len(picks) >= self.daily_picks:
                break
        return picks


STRATEGY = IndRsAdaptive3()
