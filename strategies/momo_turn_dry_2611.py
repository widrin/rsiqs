"""假设：现任 baseline_momo 的打分只用收盘价，未利用成交量信息。A股微观结构下，成交量急剧放大（尤其是价格涨但量比异常）往往对应散户情绪顶点/主力出货，后续1-5日收益显著为负；而「中期动量 + 近期缩量回调」的组合（r20 高、r5 低且成交量相对20日均量回落）说明抛压衰竭、是更干净的入场点。本策略在打分中加入「20日量比 = 近5日均量 / 前20日均量」的反向排名项（权重 0.5）：量比越大得分越低。经济依据：量价配合的经典微观结构逻辑——放量滞涨/放量上涨是分歧加大信号，缩量回调是浮筹清洗；与开盘价成交机制契合，我们买的是回调后缩量企稳的股票，而非追放量阳线。与现任差异：选股池过滤（含20日日均成交额≥1亿）、entry_max、止损−7%、止盈+10%、持有5天均不变，仅打分多一个量能反向项。预期失效场景：强趋势启动初期（val 段若处于主升浪），放量上涨是趋势确认信号，反向量能偏好会踏空领涨股；若 train 改善不明显而 val 受损，则假设证伪。"""
import math

import pandas as pd

from config import get_settings
from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MomoTurnDry2611:
    name = "momo_turn_dry_2611"
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
            v = h["volume"].astype(float)
            recent_avg = float(v.iloc[-5:].mean())
            prior_avg = float(v.iloc[-25:-5].mean())
            if prior_avg <= 0 or not math.isfinite(recent_avg) or not math.isfinite(prior_avg):
                continue
            vol_ratio = recent_avg / prior_avg
            if not math.isfinite(vol_ratio) or vol_ratio <= 0:
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "vol_ratio": vol_ratio})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = (df["r20"].rank() - df["r5"].rank()
                        - self.vol_weight * df["vol_ratio"].rank(ascending=False))
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.93, 2),
                 target=round(r.close * 1.10, 2), hold_days=get_settings().hold_days_for(self.name),
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，近5日量比{r.vol_ratio:.2f}（缩量回调优先），中期动量+短期回调，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = MomoTurnDry2611()
