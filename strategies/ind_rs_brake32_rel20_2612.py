"""假设：这是0.32/0.27深度回撤刹车的纯rel20对照。相对行业强度具有样本外选股能力，但每日多只同步建仓会在长熊中放大回撤；本稿严格保持每日最多1只、不设weight，以全池个股距各自250日高点回撤的中位数控制新开仓：dd_med≥0.32暂停，0.27~0.32隔日开仓，低于0.27正常开仓。行业选择只使用rel20，即行业20日等权收益减全行业等权均值；行业内仍按个股r20相对全池中位数的排名减5日反转排名选股。除行业打分外，其余逻辑与ind_rs_brake32_rel60_2612完全一致，用于隔离rel60降噪的贡献。与现任全池动量策略不同，本稿先选相对强行业再选相对强个股，并以慢建仓和深度回撤刹车控制系统性暴露。预期失效场景：长熊中0.32刹车过晚导致回撤重新扩大，或纯rel20受短期噪声影响而降低train夏普；不依赖紧止损控制回撤。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndRsBrake32Rel20:
    name = "ind_rs_brake32_rel20_2612"
    min_rows = 60
    slow_rows = 250
    min_amount = 1e8
    stop_dd = 0.32
    slow_dd = 0.27

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
            c = h["close"]
            r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
            r5 = float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            if len(c) >= 61:
                r60 = float(c.iloc[-1] / c.iloc[-61] - 1)
            else:
                r60 = r20
            if not math.isfinite(r60):
                continue
            high250 = float(c.max())
            if not (math.isfinite(high250) and high250 > 0):
                continue
            dd_values.append(max(0.0, 1.0 - close / high250))
            industry = view.industry(code)
            if industry is not None:
                if industry not in industry_rets:
                    industry_rets[industry] = []
                industry_rets[industry].append((r20, r60))
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue
            rows.append({"code": code, "name": name, "industry": industry,
                         "close": close, "r20": r20, "r5": r5, "sigma": sigma})
        if not rows or not dd_values or not industry_rets:
            return []
        dd_med = float(pd.Series(dd_values).median())
        if dd_med >= self.stop_dd:
            return []
        if dd_med >= self.slow_dd and view.asof.toordinal() % 2 != 0:
            return []
        ind_rows = []
        all_r20 = []
        all_r60 = []
        for industry, values in industry_rets.items():
            pair = pd.DataFrame(values, columns=["r20", "r60"])
            mean20 = float(pair["r20"].mean())
            mean60 = float(pair["r60"].mean())
            ind_rows.append({"industry": industry, "raw20": mean20, "raw60": mean60})
            all_r20.extend(pair["r20"].tolist())
            all_r60.extend(pair["r60"].tolist())
        market20 = float(pd.Series(all_r20).mean())
        market60 = float(pd.Series(all_r60).mean())
        ind_df = pd.DataFrame(ind_rows)
        ind_df["rel20"] = ind_df["raw20"] - market20
        ind_df["rel60"] = ind_df["raw60"] - market60
        ind_df["ind_score"] = ind_df["rel20"].rank()
        ind_df = ind_df.sort_values(["ind_score", "industry"], ascending=[False, True])
        stock_df = pd.DataFrame(rows)
        r20_med = float(stock_df["r20"].median())
        stock_df["rel_stock"] = stock_df["r20"] - r20_med
        stock_df["stock_score"] = stock_df["rel_stock"].rank() - stock_df["r5"].rank()
        picks = []
        industry_count = {}
        for ind in ind_df.itertuples(index=False):
            candidates = stock_df[stock_df["industry"] == ind.industry]
            if candidates.empty:
                continue
            candidates = candidates.sort_values(["stock_score", "code"], ascending=[False, True])
            for r in candidates.itertuples(index=False):
                used = industry_count.get(r.industry, 0)
                if used >= 2:
                    break
                entry_max = round(r.close * 1.03, 2)
                stop_px = round(r.close * (1 - 1.5 * r.sigma), 2)
                target_px = round(r.close * (1 + 3.0 * r.sigma), 2)
                if not stop_px < entry_max < target_px:
                    continue
                score = float(ind.ind_score) * 1000.0 + float(r.stock_score)
                picks.append(Pick(
                    code=r.code, name=r.name, score=score,
                    entry_max=entry_max, stop=stop_px, target=target_px, hold_days=5,
                    reason=f"{r.industry}相对行业20日{ind.rel20:.1%}、60日{ind.rel60:.1%}，个股20日{r.r20:.1%}、5日{r.r5:.1%}，全池回撤中位数{dd_med:.1%}，每日最多1只，σ止损{stop_px:.2f}/止盈{target_px:.2f}"))
                industry_count[r.industry] = used + 1
                return picks
        return picks


STRATEGY = IndRsBrake32Rel20()
