"""假设：动量选股（20日动量−5日反转）的收益来源是横截面分化——高分位股跑赢低分位股。当全池个股走势高度同步（横截面离散度低）时，分化度不足，动量组合的超额被压扁，且此时大盘方向主导一切、个股 alpha 被 beta 淹没，止损被反复触发。离散度是一个与大盘涨跌方向互不相关的时机信号，可作为大盘闸门的正交补充。

度量：disp(t) = 全池个股近 20 日累计收益的横截面标准差。为得到缓慢的分位，用最近 60 个交易日观测的滚动分位（每天用近 60 日的 disp 观测值排序，取当前 disp 的百分位）。为免跨日状态，本版改为在单次遍历内**直接计算近 60 个交易日的 disp 序列**：对每个观测日 t-k（k=0..59），用当日 view 可见的历史切片计算全池 20 日收益的截面 std。计算量为 60 × 800 只 × 切片，偏重但可接受（只取 60+20=80 行切片）。

分档：当前 disp 在近 60 观测的 >60 分位 → 5 只×1.0；20~60 分位 → 3 只×0.8；<20 分位 → 1 只×0.5（不空仓）。

经济理由：(1) 离散度高意味着个股分化充分，动量的横截面脊线清晰，策略有肉可吃；(2) 离散度低（如系统性冲击、政策统一利好/利空的普涨普跌段）时，选股无意义，且反转噪声大；(3) 离散度是慢变量（rolling 60 分位），换手低于日频动量闸门，成本更省。

与现任差异：现任恒定满仓；本版只在低离散度时降暴露。与前两版的差异：闸门输入完全不同（不是方向、不是深度，而是截面分化度）。预期失效：离散度与大盘走势在极端行情中同向（大跌即高离散度），可能在大跌时反而满仓，需要 train 检验。"""
import math

import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class DispersionGate:
    name = "dispersion_gate_2611"
    top_n = 15
    min_rows = 80
    min_amount = 1e8

    def _dispersion_series(self, view: DataView) -> list[float]:
        # 计算过去 60 个观测日的 disp_t：每日全池 20 日累计收益的截面 std
        codes = []
        for code in view.codes():
            h = view.history(code, self.min_rows)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            c = h["close"]
            if len(c) < 80:
                continue
            if not float(c.iloc[-1]) > 0:
                continue
            codes.append(code)
        if len(codes) < 50:
            return []
        # 用末 60 日的收盘价构造 T×N 矩阵（每行一个交易日，每列一只股票），同代码行号对齐
        closes = []
        for code in codes:
            h = view.history(code, 80)
            closes.append(h["close"].to_numpy(dtype=float)[-60:])
        arr = np.vstack(closes).T  # 60 行 × N 列，最后一行 = asof
        # 每列是 60 日收盘；对第 k 行（距末尾 k 步），20 日收益 = arr[k] / arr[k-20] - 1
        disps = []
        n = 60
        for k in range(20, n):
            base = arr[k - 20]
            cur = arr[k]
            r = cur / base - 1.0
            r = r[np.isfinite(r)]
            if len(r) < 30:
                continue
            disps.append(float(np.std(r)))
        return disps

    def select(self, view: DataView) -> list[Pick]:
        disps = self._dispersion_series(view)
        cur_disp = disps[-1] if disps else None
        pct = 0.5
        if disps and cur_disp is not None:
            below = sum(1 for d in disps if d <= cur_disp)
            pct = below / len(disps)
        if pct > 0.6:
            n_slots, weight = 5, 1.0
        elif pct > 0.2:
            n_slots, weight = 3, 0.8
        else:
            n_slots, weight = 1, 0.5

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
            if len(picks) >= n_slots:
                break
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            disp_s = f"{cur_disp:.3%}" if cur_disp is not None else "NA"
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold, weight=weight,
                reason=f"横截面离散度{disp_s}处近60日{pct:.0%}分位→档位{n_slots}只×{weight}；个股20日涨幅{r.r20:.1%}、5日回调{r.r5:.1%}，σ={sigma:.1%}，止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = DispersionGate()
