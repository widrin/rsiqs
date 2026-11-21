"""假设：相对行业强度选股具有样本外alpha，但每日1只造成暴露不足，纯20日行业排名又容易受短期噪音影响。本稿以 ind_rs_deepbrake_2612 为基底，仅将深度回撤刹车放宽为 dd_med>=0.35 暂停、0.30~0.35 隔日开仓，并将每日名额提高到2只；行业打分改为0.7倍rel20排名加0.3倍rel60排名，以中期趋势平滑短期噪音。个股仍按20日相对强度排名减5日反转排名，单行业最多2只，1.5σ止损、3σ止盈、5日持有。与现任的差异是先做相对行业配置再选行业内个股，并用全池深度回撤控制新仓节奏。预期在行业趋势突然反转或长期普跌且回撤刹车反应过慢时失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsWideBrake2:
    name = "ind_rs_widebrake2_2612"
    top_n = 15
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    stop_dd = 0.35
    slow_dd = 0.30

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        industry_rets = {}
        dd_values = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.slow_rows)
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
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            industry = view.industry(code)
            if industry is not None:
                industry_rets.setdefault(industry, []).append(r20)
            if len(c) >= self.slow_rows:
                peak = float(c.max())
                if math.isfinite(peak) and peak > 0:
                    dd_values.append(max(0.0, 1.0 - close / peak))
            rows.append({"code": code, "name": name, "industry": industry,
                         "close": close, "r20": r20, "r5": r5, "sigma": sigma})
        if not rows or not industry_rets or not dd_values:
            return []
        dd_med = float(pd.Series(dd_values).median())
        if not math.isfinite(dd_med) or dd_med >= self.stop_dd:
            return []
        if dd_med >= self.slow_dd and view.asof.toordinal() % 2 == 0:
            return []
        industry_r20 = {k: float(pd.Series(v).mean()) for k, v in industry_rets.items()}
        market_r20 = float(pd.Series(list(industry_r20.values())).mean())
        industry_r60 = {}
        for industry in industry_rets:
            values = []
            for row in rows:
                if row["industry"] != industry:
                    continue
                h60 = view.history(row["code"], self.min_rows)
                if len(h60) < self.min_rows:
                    continue
                c60 = h60["close"]
                value = float(c60.iloc[-1] / c60.iloc[-21] - 1)
                if math.isfinite(value):
                    values.append(value)
            if values:
                industry_r60[industry] = float(pd.Series(values).mean())
        if not industry_r60:
            return []
        market_r60 = float(pd.Series(list(industry_r60.values())).mean())
        ind_df = pd.DataFrame([{"industry": k,
                                "rel20": industry_r20[k] - market_r20,
                                "rel60": industry_r60.get(k, industry_r20[k]) - market_r60}
                               for k in industry_r20])
        ind_df["ind_score"] = 0.7 * ind_df["rel20"].rank() + 0.3 * ind_df["rel60"].rank()
        ind_scores = {r.industry: float(r.ind_score) for r in ind_df.itertuples(index=False)}
        df = pd.DataFrame(rows)
        df = df[df["industry"].isin(ind_scores)].copy()
        if df.empty:
            return []
        r20_med = float(df["r20"].median())
        r5_med = float(df["r5"].median())
        df["stock_score"] = (df["r20"] - r20_med).rank() - (df["r5"] - r5_med).rank()
        df["ind_score"] = df["industry"].map(ind_scores)
        df["score"] = df["ind_score"] + df["stock_score"] / max(len(df), 1)
        df = df.sort_values(["score", "code"], ascending=[False, True])
        selected = []
        industry_count = {}
        for r in df.itertuples(index=False):
            count = industry_count.get(r.industry, 0)
            if count >= 2:
                continue
            selected.append(r)
            industry_count[r.industry] = count + 1
            if len(selected) >= 2:
                break
        picks = []
        for r in selected:
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"{r.industry}相对强度20/60日加权，个股20日涨幅{r.r20:.1%}、5日涨幅{r.r5:.1%}，全池回撤中位数{dd_med:.1%}，σ={r.sigma:.1%}，持有5日"))
        return picks


STRATEGY = IndRsWideBrake2()
