"""假设：行业相对强度必须先在全股票横截面计算，再聚合到行业；若在每个行业内部排名，行业间收益水平会被归一化，可能把弱行业误选为强行业。第1轮 bull 超额口径出现大幅负超额，第2轮的全池排名版本则因历史长度检查不足而未完成评估。本稿只修正这两处实现问题：至少61个有效交易日才计算 r60，个股 rel20、rel60 在全池转为百分位排名，行业内取均值并等权相加，选前5强行业。其余保持每日1只、个股 rel20 排名减5日反转排名、1.5σ止损、3σ止盈、5日持有、单行业≤2只、dd_med 0.30/0.22 刹车，并声明 target_regime='bull'。与现任全周期个股动量策略不同，本稿只检验牛市行业轮动能否跑赢沪深300，不做基准二次中性化。预期在行业动量反转、强行业快速切换或牛市样本不足时失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsBullXRankFix:
    name = "ind_rs_bullxrank_fix_2611"
    target_regime = "bull"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    industry_top = 5
    industry_cap = 2
    min_industry_members = 3

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        drawdowns = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.slow_rows)
            if len(h) < 61 or h.index[-1].date() != view.asof:
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
            r60 = float(c.iloc[-1] / c.iloc[-61] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(r60)):
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            high250 = float(c.max())
            if math.isfinite(high250) and high250 > 0:
                drawdowns.append(max(0.0, 1.0 - close / high250))
            rows.append({"code": code, "name": name, "industry": view.industry(code),
                         "close": close, "r20": r20, "r5": r5, "r60": r60,
                         "sigma": sigma})
        if not rows or not drawdowns:
            return []
        dd_med = float(pd.Series(drawdowns).median())
        if dd_med >= 0.30:
            return []
        if dd_med >= 0.22 and view.asof.toordinal() % 2 != 0:
            return []
        df = pd.DataFrame(rows)
        df["rel20"] = df["r20"].rank(pct=True)
        df["rel60"] = df["r60"].rank(pct=True)
        industry_scores = []
        for industry, members in df.groupby("industry", sort=True):
            if industry is None or len(members) < self.min_industry_members:
                continue
            ind_score = float(members["rel20"].mean() + members["rel60"].mean())
            industry_scores.append({"industry": industry, "ind_score": ind_score})
        if not industry_scores:
            return []
        ind_df = pd.DataFrame(industry_scores)
        ind_df = ind_df.sort_values(["ind_score", "industry"], ascending=[False, True])
        strong = set(ind_df.head(self.industry_top)["industry"])
        df = df[df["industry"].isin(strong)].copy()
        if df.empty:
            return []
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        industry_counts = {}
        for r in df.itertuples(index=False):
            industry = r.industry
            if industry_counts.get(industry, 0) >= self.industry_cap:
                continue
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            entry_max = round(r.close * 1.03, 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                reason=f"全池相对强度前5强行业{industry}，20日涨幅{r.r20:.1%}，60日涨幅{r.r60:.1%}，5日涨幅{r.r5:.1%}，σ={r.sigma:.1%}，回撤中位数{dd_med:.1%}"))
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if len(picks) >= 1:
                break
        return picks


STRATEGY = IndRsBullXRankFix()
