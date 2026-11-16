"""假设：上一轮 relstr_exit_sigma_2611 的相对强弱过滤因硬编码指数 code（000300.SH/000300.XSHG）不在股票池内而被静默跳过——mkt_r20 恒为 None，过滤从未生效，结果与现任 vol_exit_fixedhold_2611 完全相同（train 0.68/val 1.15 复刻）。
本版修复：不硬编码指数名，遍历 view.codes() 动态识别沪深300指数行（code 以 '000300' 开头），取其近20日涨幅作为基准 mkt_r20。
经济学依据：只跟大盘上涨的高beta伪动量票在大盘转熊时率先回落；有正alpha的相对强势股更抗跌。只要求相对强弱为正、不要求绝对涨幅，避免像 momo_trend 的绝对均线那样在熊市段把股票池砍太狠（train 仅0.25）。
与现任差异：只多一个真正生效的20日指数相对强弱过滤，σ自适应stop/target、固定5日、rank打分结构完全不动。
预期失效场景：单边普涨的beta牛市选股变窄、跑输纯动量，但该环境动量策略本身已能赚钱；主要期望改善熊市holdout最大回撤。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class RelStrScan:
    name = "relstr_scan_2611"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        mkt_r20 = None
        for code in view.codes():
            if code.startswith("000300"):
                mh = view.history(code, self.min_rows)
                if len(mh) >= 21:
                    mc = mh["close"]
                    mv = float(mc.iloc[-1] / mc.iloc[-21] - 1)
                    if math.isfinite(mv):
                        mkt_r20 = mv
                break

        rows = []
        for code in view.codes():
            if code.startswith("000300"):
                continue
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
            if mkt_r20 is not None and not r20 - mkt_r20 > 0:
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
            hold = 5
            if not stop_px < r.close * 1.03 < target_px:
                continue
            mkt_txt = f"，跑赢20日大盘{mkt_r20:.1%}" if mkt_r20 is not None else ""
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}{mkt_txt}，5日涨幅{r.r5:.1%}，σ={sigma:.1%}，自适应止损{stop_px:.2f}/止盈{target_px:.2f}，固定持有{hold}日"))
        return picks


STRATEGY = RelStrScan()
