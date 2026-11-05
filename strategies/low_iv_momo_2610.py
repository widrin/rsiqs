"""假设：现任 baseline_momo 只按收益排名打分，同等20日动量下会选到波动率最高的股票（高波动股票涨幅的右尾更极端），这些「彩票型」个股在A股长期风险调整后收益偏差，且日波动越大，−7%止损被扫出的概率越高、T+1隔夜跳空损失越大。本策略在保留「20日动量排名−5日反转排名」的基础上，叠加60日close-to-close日收益标准差的排名：score = (r20_rank − r5_rank) − 0.5 × vol60_rank，低波动股票额外加分。经济依据：A股低波动异象长期存在，低beta/低特质波动组合在控制方向暴露后风险调整后收益优于高波动组；与开盘成交、T+1机制契合——低波动股隔夜跳空幅度小，开盘价成交的滑点风险低。与现任差异：仅在打分中加入低波动偏置，选股池过滤、entry_max、止损−7%、止盈+10%、持有5天均保持不变，以干净分离低波动叠加的效果。预期失效场景：牛市主升浪中低波动股票显著跑输高波动品种，会牺牲绝对收益；在2025年 val 段若市场呈现强动量/小票风格，低波动偏好可能拖累 val_sharpe。"""
import math

import numpy as np
import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class LowIvMomo2610:
    name = "low_iv_momo_2610"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    vol_weight = 0.5

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
        df["score"] = (df["r20"].rank() - df["r5"].rank()
                       - self.vol_weight * df["vol60"].rank(ascending=False))
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.93, 2),
                 target=round(r.close * 1.10, 2), hold_days=5,
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，60日波动{r.vol60:.1%}偏低，中期动量+短期回调+低波动，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = LowIvMomo2610()
