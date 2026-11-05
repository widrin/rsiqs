"""假设：momo_tight_2610 的退出参数（止损−4%/止盈+12%/持有8天）在 val 段已验证有效（sharpe 1.26、dsr 0.94），但 train_sharpe 仅 0.34，主因是 5 只满仓组合中混入 60 日实现波动率极高的彩票股——它们的20日涨幅排名天然偏极端，−4% 止损在正常波动中就会被扫出，把 train 段均值收益拖成噪声。low_iv_momo 证明「低波动作为连续打分偏置」无效（它扭曲了动量信号、train 近乎零收益），但低波动作为「硬过滤器」尚未被单独检验：直接剔除 60 日波动率在全池中处于前 20% 的股票，其余股票的打分顺序完全不变（仍为 r20_rank − r5_rank），只挡住边缘案例。经济依据：A股高波动右尾个股长期是负alpha的彩票品种，且与 −4% 紧止损机制天然互斥；80分位阈值是简单整数、不依赖历史拟合、不过度约束。与现任差异：退出参数同 momo_tight_2610，入场端多一道波动率硬过滤，选股池其余过滤（非ST、成交额≥1亿、收盘未封涨停、60行）不变。预期失效场景：若 val 段完全由高波动题材股驱动（2025年行情若如此），过滤器会踏空右尾收益，val_sharpe 从 1.26 明显回落；若 train 改善有限而 val 受损，则假设证伪。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MomoIvGate2611:
    name = "momo_ivgate_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    vol_gate_quantile = 0.8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
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
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            rets = c.pct_change().dropna()
            if len(rets) < 20:
                continue
            vol60 = float(rets.tail(60).std())
            if not math.isfinite(vol60) or vol60 <= 0:
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "vol60": vol60})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        vol_cut = float(df["vol60"].quantile(self.vol_gate_quantile))
        df = df[df["vol60"] <= vol_cut]
        if df.empty:
            return []
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.96, 2),
                 target=round(r.close * 1.12, 2), hold_days=8,
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，60日波动{r.vol60:.1%}未超前20%，止损−4%止盈+12%持有8天，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = MomoIvGate2611()
