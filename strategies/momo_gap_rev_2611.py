"""假设：现任 baseline_momo 的打分（r20_rank − r5_rank）只利用收盘动量，未利用日内/隔夜价格路径。A股散户主导下，隔夜跳空高开（开盘价相对前收盘大幅高于后续收盘价）是短期情绪过度反应的典型信号，T+1 与开盘价成交机制天然放大这种修正：跳空高开的股票在随后1-3日内平均收益率显著低于无跳空股票。本策略在打分中加入「5日平均跳空幅度 = mean(open/prev_close − 1)」的反向排名项（权重 0.5）：隔夜跳空越大、得分越低，与中期动量方向一致（选中期上涨但近期没有疯狂高开的股票）。经济依据：跳空高开意味着集合竞价阶段散户买盘集中追入，开盘后抛压释放，A股长期存在「开盘溢价」的日内反转；与本系统「开盘价买入」的成交方式契合——我们本质上是在为别人的过度反应提供流动性。与现任差异：选股池过滤、entry_max、止损−7%、止盈+10%、持有5天均不变，仅打分多一个跳空反向项，以干净分离该效应。预期失效场景：强趋势市（如2025年 val 段若单边上涨）中，跳空高开是趋势延续信号，反向偏好会系统性卖出强势股，val_sharpe 受损；若该情形出现则假设证伪。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class MomoGapRev2611:
    name = "momo_gap_rev_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8
    gap_weight = 0.5

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
            open5 = h["open"].tail(5)
            prev5 = h["prev_close"].tail(5)
            gap5 = float((open5 / prev5 - 1.0).mean())
            if not math.isfinite(gap5):
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "gap5": gap5})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = (df["r20"].rank() - df["r5"].rank()
                       - self.gap_weight * df["gap5"].rank(ascending=False))
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.93, 2),
                 target=round(r.close * 1.10, 2), hold_days=5,
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，5日均跳空{r.gap5:.1%}偏低（避追高），中期动量+短期回调，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = MomoGapRev2611()
