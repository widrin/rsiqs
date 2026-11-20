"""假设：现任策略只有个股择时、无整体仓位管理，在 2018/2022 长熊里单票被打穿导致 train 最大回撤 -60%~-84%。本轮引入硬空仓：用全池三个轻量横截面统计量（semom20=20日累计收益中位数、breadth=收盘站上自身60日均线占比、sdd250=距自身250日高点回撤中位数）做『或』连接三条件闸门——mom20<-5% 或 breadth<0.30 或 dd250>0.20 时返回空列表 0 仓位；中位数回撤 >10% 或广度<45% 时降到 2只×0.5；其余满仓 5只×1.0。经济学理由：全池中位数动量/广度/回撤刻画的是系统性普跌（无差别抛售），此时个股动量 alpha 被系统性风险淹没；硬空仓能在长熊中完全离场。与现任差异：现任只有个股级 σ 自适应出场，本策略增加大盘级硬断仓，且断仓是 0 仓位而非软减仓。预期失效场景：2016/2019 式震荡顶部（大盘未崩但个股分化严重）时被一刀切空仓错过局部反弹。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class HardEmptyTriple:
    name = "hardempty_triple_2611"
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
        if med_mom < -0.05 or breadth < 0.30 or med_dd > 0.20:
            return []
        if med_dd > 0.10 or breadth < 0.45:
            max_picks = 2
            weight = 0.5
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
                reason=f"市场代理：20日动量{med_mom:.1%}/广度{breadth:.0%}/回撤{med_dd:.1%}→暴露{weight:.0%}；20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，固定持有5日"))
            if len(picks) >= max_picks:
                break
        return picks


STRATEGY = HardEmptyTriple()
