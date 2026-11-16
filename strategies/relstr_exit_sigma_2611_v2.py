"""假设：现任 σ 自适应出场方向已验证（val 夏普1.15~1.50），瓶颈在入场选股的牛熊适应性。纯价格动量选出的高 beta 品种在大盘转熊时率先回落。
本版与 relstr_exit_sigma_2611 的唯一差异：相对强弱确认用「近5日个股涨幅跑赢沪深300（r5 − r5_mkt > 0）」而非20日窗口。
短期相对强弱更贴近入场时点的动能，能及时识别「20日涨过但近5日已跑输大盘」的假回调（这类正是熊市顶部放量下跌的股票）。
经济学依据：近5日既没有绝对下跌（r5 已被打分减去）、又跑赢大盘，说明回调是相对强势中的休整而非补跌。
与现任差异：只多一个5日指数相对强弱过滤，出场参数、持有期、打分结构不动。
预期失效场景：个股短期相对强弱因行业轮动快速反转时，过滤可能滞后；主要期望改善熊市 holdout 的最大回撤。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class RelStrExitSigmaV2:
    name = "relstr_exit_sigma_2611_v2"
    top_n = 15
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        idx_code = "000300.SH"
        if idx_code not in view.codes():
            idx_code = "000300.XSHG"
        mkt_r5 = None
        if idx_code in view.codes():
            mh = view.history(idx_code, self.min_rows)
            if len(mh) >= 6:
                mc = mh["close"]
                mv = float(mc.iloc[-1] / mc.iloc[-6] - 1)
                if math.isfinite(mv):
                    mkt_r5 = mv

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
            if mkt_r5 is not None and not r5 - mkt_r5 > 0:
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
            mkt_txt = f"，近5日跑赢大盘{mkt_r5:.1%}" if mkt_r5 is not None else ""
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}{mkt_txt}，σ={sigma:.1%}，自适应止损{stop_px:.2f}/止盈{target_px:.2f}，固定持有{hold}日"))
        return picks


STRATEGY = RelStrExitSigmaV2()
