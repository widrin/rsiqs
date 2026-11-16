"""假设：relstr_scan_2611 用20日相对强弱（r20−r20_mkt>0）识别短期alpha；本对照变体把窗口拉长到60日（r60−r60_mkt>0）。
经济学依据：60日相对强弱衡量的是更持续的中期alpha，抗短期行业轮动噪声，识别的是有真实资金/基本面推动的持续性强势而非一次性脉冲；在熊市中，能持续跑赢大盘3个月的股票抗跌性更强。
与 relstr_scan_2611 的差异：仅相对强弱窗口从20日改为60日（需取60行历史），动态指数识别、σ自适应stop/target、固定5日、rank打分结构不动。
预期失效场景：市场风格快速切换时，60日相对强弱滞后于行情；主要期望它提供一个更稳健的对照，判断相对强弱方向是否真的有效以及哪个窗口更合适。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class RelStr60Scan:
    name = "relstr60_scan_2611"
    top_n = 15
    min_rows = 70
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        mkt_r60 = None
        for code in view.codes():
            if code.startswith("000300"):
                mh = view.history(code, self.min_rows)
                if len(mh) >= 61:
                    mc = mh["close"]
                    mv = float(mc.iloc[-1] / mc.iloc[-61] - 1)
                    if math.isfinite(mv):
                        mkt_r60 = mv
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
            r60 = float(c.iloc[-1] / c.iloc[-61] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5) and math.isfinite(r60)):
                continue
            if mkt_r60 is not None and not r60 - mkt_r60 > 0:
                continue
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "r60": r60, "sigma": sigma})
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
            mkt_txt = f"，跑赢60日大盘{mkt_r60:.1%}" if mkt_r60 is not None else ""
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=hold,
                reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，60日涨幅{r.r60:.1%}{mkt_txt}，σ={sigma:.1%}，自适应止损{stop_px:.2f}/止盈{target_px:.2f}，固定持有{hold}日"))
        return picks


STRATEGY = RelStr60Scan()
