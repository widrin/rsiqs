"""假设：基线用原始 20 日/5 日收益率排名，天然偏好高波动股票（极端涨幅），但 A 股高波动个股的隔夜跳空、假突破与止损触发概率都更高，风险调整后收益未必占优。因此在同一框架下，把 r20 与 r5 分别除以近 20 日日收益率的标准差（日频），得到「单位波动的中期动量 − 单位波动的短期反转」分数，等价于对每个股票先做波动率中性化再排序。与现任 baseline_momo 的差异：打分从原始收益排名改为波动率调整后的收益排名，过滤条件、entry_max/stop/target/hold_days 完全不变。预期失效环境：低波动股集中的慢牛行情（波动率调整会压低此类股票得分，但它们恰是最稳的贡献者）。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class Voladjustedmomo2611V2:
    name = "vol_adjusted_momo_2611_v2"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

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
            close = float(last["close"])
            prev = float(last["prev_close"])
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
            rets = c.pct_change().iloc[-20:].dropna()
            if len(rets) < 10:
                continue
            vol = float(rets.std())
            if not (math.isfinite(vol) and vol > 0):
                continue
            adj_r20 = r20 / vol
            adj_r5 = r5 / vol
            entry_max = round(close * 1.03, 2)
            stop = round(close * 0.93, 2)
            target = round(close * 1.10, 2)
            if not (stop < entry_max < target):
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "adj_r20": adj_r20, "adj_r5": adj_r5,
                         "r20": r20, "r5": r5,
                         "entry_max": entry_max, "stop": stop, "target": target})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["adj_r20"].rank() - df["adj_r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=float(r.entry_max), stop=float(r.stop),
                 target=float(r.target), hold_days=5,
                 reason=f"波动调整后20日{r.adj_r20:.2f}/5日{r.adj_r5:.2f}（原始{r.r20:.1%}/{r.r5:.1%}），收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = Voladjustedmomo2611V2()
