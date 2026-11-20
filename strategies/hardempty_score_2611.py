"""假设：上一策略的『或』连接三条件过于激进，可能被单一维度极端（如只有广度<30% 但动量尚可）误杀。本变体把三个横截面统计量做成 0-3 分打分：市场20日动量中位数<-5%、广度（站上60日均线占比）<35%、距250日高点回撤中位数>18% 各得 1 分。分数>=2 时两个维度同时恶化，系统性风险确定，硬空仓 0 仓位；分数=1 时单一维度恶化，降到 3只×0.6 温和暴露；分数=0 时满仓 5只×1.0。与上一变体的差异：从『或』改为『计分制』，减少震荡行情中被单一指标错杀的概率；与现任差异：现任无整体仓位管理，本策略增加多维度市场状态打分与硬空仓。经济学理由：真长熊（2018Q4/2022Q1）三个维度会同步恶化，计分制能准确识别；震荡市里通常只有一两个指标短期恶化，计分制过滤掉噪声。预期失效场景：单边急跌（如 2018Q4）若某一维度数值因分母扰动未达标，可能漏识别，导致回撤拉大。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class HardEmptyScore:
    name = "hardempty_score_2611"
    top_n = 15
    min_rows = 250
    min_amount = 1e8

    def _proxy(self, view: DataView):
        rs_mom = []
        rs_dd = []
        n_st_on = 0
        n_total = 0
        for code in view.codes():
            if is_st(view.name(code)):
                continue
            h = view.history(code, self.min_rows)
            if len(h) < 60 or h.index[-1].date() != view.asof:
                continue
            close = float(h["close"].iloc[-1])
            if not (math.isfinite(close) and close > 0):
                continue
            n_total += 1
            window = h["close"].iloc[-61:]
            r20 = float(window.iloc[-1] / window.iloc[-21] - 1) if len(window) >= 21 else float("nan")
            if math.isfinite(r20):
                rs_mom.append(r20)
            ma60 = float(window.mean())
            if close >= ma60:
                n_st_on += 1
            full = h["close"]
            peak = float(full.max())
            dd = (peak - close) / peak if peak > 0 else float("nan")
            if math.isfinite(dd):
                rs_dd.append(dd)
        if n_total == 0 or not rs_mom or not rs_dd:
            return 0.0, 1.0, 0.0
        med_mom = float(pd.Series(rs_mom).median())
        med_dd = float(pd.Series(rs_dd).median())
        breadth = n_st_on / n_total
        return med_mom, breadth, med_dd

    def select(self, view: DataView) -> list[Pick]:
        med_mom, breadth, med_dd = self._proxy(view)
        score = 0
        if med_mom < -0.05:
            score += 1
        if breadth < 0.35:
            score += 1
        if med_dd > 0.18:
            score += 1
        if score >= 2:
            return []
        if score == 1:
            max_picks = 3
            weight = 0.6
        else:
            max_picks = 5
            weight = 1.0

        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, 60)
            if len(h) < 60 or h.index[-1].date() != view.asof:
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
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5, weight=weight,
                reason=f"市场打分{score}/3（20日动量{med_mom:.1%}/广度{breadth:.0%}/回撤{med_dd:.1%}）→暴露{weight:.0%}；20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，固定持有5日"))
            if len(picks) >= max_picks:
                break
        return picks


STRATEGY = HardEmptyScore()
