"""假设：ind_rs_deepbrake_2612 的深度回撤刹车把 train 回撤压到-20.9%，但 val 超额从 ind_rs_singleflow_2612 的7.0%降到3.05%——250日回撤是慢变量，熊市第一段快速反弹时全池个股距250日高点回撤中位数 dd_med 仍高于0.25，一律暂停新开仓会踏空修复行情，这正是超额被切掉的来源。本版只在最深档内用另一个横截面标量『全池20日收益中位数 r20_med』区分下跌中与修复中：dd_med≥0.25 且 r20_med≤0（典型股票深回撤且近20日仍下跌）才当日不开新仓；dd_med≥0.25 但 r20_med>0（深回撤但近20日已转正，修复已启动）改为隔日开1只，以降速方式参与修复而非全停；0.20~0.25 维持隔日1只、<0.20 维持每日1只，旧仓始终按到期/止损自然退出。经济学理由：深度回撤区的负期望建仓主要来自『仍在下跌』的补跌段，一旦横截面中位数20日收益转正，强行业的相对强度 alpha 重新具备兑现条件，完全空仓反而损失风险溢价；这不是二元牛熊判断，仍由连续回撤深度分档，仅在最深档内用短期横截面动量确认修复。与现任差异：现任无行业层配置、每日最多5只同步满仓，本策略每日最多1只、单行业≤2只、用横截面标量控制建仓节奏。预期失效：r20_med 短暂转正后又转负的熊市假反弹会带来额外建仓亏损，train 回撤略大于 deepbrake；若 val 超额仍不足5%，说明超额损失并非来自最深档踏空。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsDeepRepair:
    name = "ind_rs_deeprepair_2612"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    top_industries = 5
    max_per_industry = 2
    dd_stop = 0.25
    dd_slow = 0.20

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
        if dd_med >= self.dd_stop:
            if r20_med <= 0:
                return []
            if view.asof.toordinal() % 2 == 1:
                return []
        elif dd_med >= self.dd_slow:
            if view.asof.toordinal() % 2 == 1:
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
            if len(picks) >= 1:
                break
        return picks


STRATEGY = IndRsDeepRepair()
