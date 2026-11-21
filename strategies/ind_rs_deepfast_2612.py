"""假设：ind_rs_deepbrake_2612 证明把降速限定在深度回撤区（全池个股距250日高点回撤中位数 dd_med≥0.25 暂停新开仓、0.20~0.25 隔日1只）能把 train 回撤压到-20.9%、val 回撤-11.2%，但 train 夏普0.483、年化仅6.1%、DSR 0.286，瓶颈是每日1只、5日到期的自然周转使资金利用率约只有1/5；ind_rs_deepbrake30 更晚刹车反而 train 夏普0.456、回撤-32.8%，说明继续放松阈值不是出路。本版保持深度回撤区刹车规则逐字不变，仅把常态区（dd_med<0.20）建仓速度从每日1只提高到每日2只，让常态区资金利用率翻倍。经济学理由：相对行业强度是横截面 alpha，其收益兑现需要足够的资金暴露；深度区刹车负责切除系统性下跌段的负期望建仓，常态区则应全速兑现行业动量，而不是被建仓速度人为限流。与 ddpace/ddflow 的区别：它们全程按回撤调名额导致反弹期踏空，本版常态区固定2只、只有深度区降速。与现任差异：现任无行业层配置、每日最多5只同步满仓，本策略单行业≤2只、用横截面深度回撤标量控制建仓节奏。预期失效：2018、2022年下跌初段 dd_med 尚未到0.25时暴露翻倍，train 回撤可能从-21%扩大到约-30%（仍在-45%门槛内）；若 val 长期落在0.20~0.25区间则提速无效、结果与 deepbrake 接近。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepFast:
    name = "ind_rs_deepfast_2612"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    top_industries = 5
    max_per_industry = 2
    dd_stop = 0.25
    dd_slow = 0.20
    daily_picks = 2

    def select(self, view: DataView) -> list[Pick]:
        dds = []
        r20s = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.slow_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            close = float(c.iloc[-1])
            if not (math.isfinite(close) and close > 0):
                continue
            hi = float(c.max())
            if math.isfinite(hi) and hi > 0:
                dds.append(1.0 - close / hi)
            if len(c) >= 21:
                base = float(c.iloc[-21])
                if math.isfinite(base) and base > 0:
                    r20s.append(close / base - 1.0)
        if len(dds) < 50 or len(r20s) < 50:
            return []
        dd_med = float(pd.Series(dds).median())
        if not math.isfinite(dd_med):
            return []
        if dd_med >= self.dd_stop:
            return []
        limit_n = self.daily_picks
        if dd_med >= self.dd_slow:
            if view.asof.toordinal() % 2 == 1:
                return []
            limit_n = 1
        r20_med = float(pd.Series(r20s).median())
        if not math.isfinite(r20_med):
            return []
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            ind = view.industry(code)
            if not ind:
                continue
            h = view.history(code, self.min_rows)
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
            r60 = float(c.iloc[-1] / c.iloc[0] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(r60)):
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": name, "ind": ind, "close": close,
                         "r20": r20, "r5": r5, "r60": r60, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        ind20 = df.groupby("ind")["r20"].mean()
        ind60 = df.groupby("ind")["r60"].mean()
        ind_score = (ind20 - ind20.median()) + (ind60 - ind60.median())
        strong = set(ind_score.sort_values(ascending=False).head(self.top_industries).index)
        df = df[df["ind"].isin(strong)].copy()
        if df.empty:
            return []
        df["rel20"] = df["r20"] - r20_med
        df["score"] = df["rel20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picks = []
        used = {}
        for r in df.itertuples(index=False):
            if used.get(r.ind, 0) >= self.max_per_industry:
                continue
            stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
            target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            used[r.ind] = used.get(r.ind, 0) + 1
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"{r.ind}相对行业强度居前，20日涨幅{r.r20:.1%}（相对全池中位数{r.rel20:.1%}），5日涨幅{r.r5:.1%}，全池回撤中位数{dd_med:.1%}，σ={r.sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}，持有5日"))
            if len(picks) >= limit_n:
                break
        return picks


STRATEGY = IndRsDeepFast()
