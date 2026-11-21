"""假设：基线 ind_rs_deepbrake30_2612 的行业相对强度与个股打分有效，但每日1只使净值过度依赖单票特质风险。上一轮每日2只并设置 weight=0.5/0.6 后，train 回撤分别达到-89.45%/-83.70%，且两档日波动几乎相同，说明当前 weight 与资金分配存在不可控交互，不能视为简单的等价暴露缩放。本稿不再使用 weight：每日最多2只、强制来自不同行业，每只 entry_max=close×1.015，使两只票合计可接受买入价格敞口近似等于基线1只×close×1.03。经济学理由是总价格敞口预算不变时，跨行业拆分可降低个股及单行业特质波动，保留纯 rel20 行业动量与个股相对强度 alpha。与现任 vol_exit_fixedhold_2611 的差异仍是行业轮动、慢建仓和深回撤刹车。失效场景：entry_max 降低同时改变高开成交样本，导致实际资金暴露并不等价；或强行业间仍高度同涨同跌，分散化收益不足。其余打分、1.5σ止损/3σ止盈、5日持有及0.30/0.22刹车均与基线一致。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepbrake30PairCap:
    name = "ind_rs_deepbrake30_paircap_2611"
    top_n = 15
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
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
        df = df.sort_values(["score", "code"], ascending=[False, True])
        df = df[df["ind_rank"] < 1].head(2)
        if dd_med >= self.brake_slow:
            if view.asof.toordinal() % 2 == 0:
                return []
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            entry_cap = round(r.close * 1.015, 2)
            hold = 5
            if not stop_px < entry_cap < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_cap,
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"行业{r.industry}相对强度{r.ind_score:.1%}，20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，跨行业双票等价价格敞口，止损{stop_px:.2f}/止盈{target_px:.2f}，持有{hold}日"))
        return picks


STRATEGY = IndRsDeepbrake30PairCap()
