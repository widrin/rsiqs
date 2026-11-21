"""假设：基线 ind_rs_deepbrake30_2612（纯 rel20 行业相对强度 + 行业内 r20 相对排名 − 5日反转、每日1只、1.5σ止损/3σ止盈/5日持有、dd_med 0.30/0.22 深回撤刹车）train 夏普 0.456、回撤 -32.8%，说明行业与个股信号有效，但每日1只×weight1.0 使净值波动高度依赖单票特异性风险。本稿唯一改动：每日最多2只（df.head(2)，单行业≤2不变），每只 weight=0.5，总名义暴露与1只×1.0相同。经济学理由：在总暴露不变时把持仓拆成两份，个股特质波动按分散化原理下降，而行业动量与个股相对强度的系统性 alpha 保留，风险调整收益应上升；该效应未被套利掉是因为它是组合构造层面的改进而非新信号。与现任 vol_exit_fixedhold_2611 的差异：现任是个股全池打分、无行业层、无深回撤刹车；本稿是行业相对强度轮动+慢建仓+刹车。预期失效场景：两只持仓同属高相关行业、同涨同跌，分散化收益消失；或 weight 减半后最低佣金占比上升侵蚀超额。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepbrake30Pair50:
    name = "ind_rs_deepbrake30_pair50_2611"
    top_n = 15
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    weight = 0.5
    brake_stop = 0.30
    brake_slow = 0.22

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        dd_list = []
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
            if len(c) >= self.slow_rows:
                peak = float(c.max())
                if peak > 0:
                    dd_list.append(1.0 - close / peak)
            ind = view.industry(code)
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma,
                         "industry": ind if ind is not None else "无行业"})
        if not rows:
            return []
        if dd_list:
            dd_med = float(pd.Series(dd_list).median())
        else:
            dd_med = 0.0
        if dd_med >= self.brake_stop:
            return []
        df = pd.DataFrame(rows)
        ind_rel = df.groupby("industry")["r20"].mean()
        ind_rel = ind_rel - ind_rel.mean()
        strong = ind_rel.sort_values(ascending=False)
        strong_inds = set(strong.index[:5])
        df = df[df["industry"].isin(strong_inds)].copy()
        if df.empty:
            return []
        df["ind_score"] = df["industry"].map(ind_rel)
        r20_med = float(df["r20"].median())
        df["rel20"] = df["r20"] - r20_med
        df["score"] = df["rel20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        df["ind_rank"] = df.groupby("industry").cumcount()
        df = df[df["ind_rank"] < 2]
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(2)
        if dd_med >= self.brake_slow:
            if view.asof.toordinal() % 2 == 0:
                return []
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                weight=self.weight,
                reason=f"行业{r.industry}相对强度{r.ind_score:.1%}，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，半仓分散，止损{stop_px:.2f}/止盈{target_px:.2f}，持有{hold}日"))
        return picks


STRATEGY = IndRsDeepbrake30Pair50()
