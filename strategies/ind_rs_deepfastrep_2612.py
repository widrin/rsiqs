"""假设：ind_rs_deepbrake_2612 的两个独立瓶颈分别有明确证据——①每日1只慢建仓使资金利用率约1/5，train 年化仅6.1%、夏普0.483、DSR 0.286，属于暴露不足；②250日回撤为慢变量，dd_med≥0.25 一律暂停使 val 超额从 singleflow 的7.0%降到3.05%，属于熊市修复段踏空。本版合并两个针对性改动：常态区（dd_med<0.20）建仓速度提高到每日2只；最深档（dd_med≥0.25）内用全池20日收益中位数 r20_med 区分下跌与修复，r20_med≤0 才暂停新开仓，r20_med>0 改为隔日1只参与修复；0.20~0.25 维持隔日1只，旧仓始终按到期/止损自然退出。经济学理由：横截面相对行业强度 alpha 的兑现需要足够暴露，而系统性下跌段的负期望建仓应由深度回撤刹车切除；修复确认避免把慢变量造成的假『深熊』信号延续到已启动的反弹中。与 ddpace/ddflow 的区别：它们全程按回撤调名额导致反弹踏空，本版常态区固定2只、只有深度区降速且带修复确认。与现任差异：现任无行业层配置、每日最多5只同步满仓，本策略单行业≤2只、用横截面标量控制建仓节奏、不靠紧止损控回撤。预期失效：下跌初段暴露翻倍叠加熊市假反弹，train 回撤可能扩大到-30%左右（仍在-45%门槛内）；若两个改动存在负交互（提速放大假反弹亏损），结果会差于两个单独变体，届时以单独变体为准。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepFastRep:
    name = "ind_rs_deepfastrep_2612"
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
        r20_med = float(pd.Series(r20s).median())
        if not (math.isfinite(dd_med) and math.isfinite(r20_med)):
            return []
        limit_n = self.daily_picks
        if dd_med >= self.dd_stop:
            if r20_med <= 0:
                return []
            if view.asof.toordinal() % 2 == 1:
                return []
            limit_n = 1
        elif dd_med >= self.dd_slow:
            if view.asof.toordinal() % 2 == 1:
                return []
            limit_n = 1
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


STRATEGY = IndRsDeepFastRep()
