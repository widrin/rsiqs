"""假设：本系统按次日开盘价买入，T+1 下买入后第一个隔夜跳空由策略被动承担。A股隔夜（收盘到开盘）涨幅在情绪高点常常提前透支当日涨幅，追高买入的滑点冲击与回撤都更大；同时个股动量若只是随大盘（beta）同向，超额收益有限。因此在基线框架上增加两条微观结构过滤：(1) 近5日累计隔夜收益（每日 open/prev_close − 1 求和）≤0，即近期没有连续高开透支；(2) asof 当日个股相对沪深300强弱为正（个股当日涨幅 ≥ 沪深300当日涨幅），确认是个股 alpha。与现任 baseline_momo 的差异：新增隔夜跳空与相对强弱条件，其余打分、风控参数不变。预期失效环境：单边逼空/连板行情（连续高开恰是最强信号，会被误杀），以及系统性反弹（个股alpha趋同，相对强弱过滤几乎不改变选择）。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick

CSI300 = "000300.SH"


class Overnightmomo2611:
    name = "overnight_momo_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        bench_hist = view.history(CSI300, 6)
        if len(bench_hist) < 6 or bench_hist.index[-1].date() != view.asof:
            return []
        bench = bench_hist["close"]
        bench_ret = float(bench.iloc[-1] / bench.iloc[-2] - 1)
        if not math.isfinite(bench_ret):
            return []
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
            open_px = float(last["open"])
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
            overnight = 0.0
            valid = True
            for i in range(-5, 0):
                o = float(h["open"].iloc[i])
                pc = float(h["prev_close"].iloc[i])
                if not (math.isfinite(o) and math.isfinite(pc) and pc > 0):
                    valid = False
                    break
                overnight += o / pc - 1.0
            if not valid or not math.isfinite(overnight):
                continue
            if overnight > 0.0:
                continue
            day_ret = close / prev - 1.0
            if not math.isfinite(day_ret) or day_ret < bench_ret:
                continue
            rows.append({"code": code, "name": name, "close": close, "r20": r20, "r5": r5})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.93, 2),
                 target=round(r.close * 1.10, 2), hold_days=5,
                 reason=f"20日涨幅{r.r20:.1%}、5日涨幅{r.r5:.1%}，近5日隔夜未透支且当日强于沪深300，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = Overnightmomo2611()
