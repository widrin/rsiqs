"""假设：与 ind_rs_deepbrake30_pair50_2611 完全同构的对照稿，唯一差异是 weight=0.6（每日最多2只，总名义暴露1.2，较基线1只×1.0轻微放大20%）。目的：主稿若 train 夏普抬过0.5，需要区分贡献来自「总暴露不变下的个股分散化」还是「暴露水平变化」；若本稿夏普与主稿接近而收益更高、回撤成比例加深，则主要是暴露效应，分散化本身贡献有限；若本稿回撤显著更深、夏普更低，则支持分散化是主因。其余逻辑（纯 rel20 行业相对强度、行业内 r20 相对排名−5日反转、1.5σ/3σ出场、5日持有、单行业≤2、dd_med 0.30/0.22 刹车）与基线 ind_rs_deepbrake30_2612 一致。预期失效场景：同主稿；此外暴露放大20%在熊市段会加深回撤，可能触及 train_max_dd 门槛。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepbrake30Pair60:
    name = "ind_rs_deepbrake30_pair60_2611"
    top_n = 15
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    weight = 0.6
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
                reason=f"行业{r.industry}相对强度{r.ind_score:.1%}，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，0.6仓对照，止损{stop_px:.2f}/止盈{target_px:.2f}，持有{hold}日"))
        return picks


STRATEGY = IndRsDeepbrake30Pair60()
