"""假设：ind_momo_leader_2611 的行业层 alpha 真实（val超额15.3%），回撤−73.8%主要来自熊市满仓暴露。第1轮证明软减仓（缩减候选数）与紧止损（1.0σ）都无效甚至恶化——软减仓让持仓更集中、紧止损被T+1噪声扫出。本稿改用硬市场闸：用全池横截面统计代替昂贵的指数拼接——广度=收盘价>自身120日均线的股票占比，市场动量=全池r20中位数；仅当广度>0.5且市场r20中位数>0时才给出候选，否则返回空列表完全空仓，已持仓在5日持有期内自然到期。这样熊市日无新开仓、无新增暴露，系统性回撤被直接切除。单行业≤2只防止强行业黑天鹅，出场沿用现任1.5σ/3σ（已被现任验证的最优出场）。与 ind_momo_divsoft_2611 的差异：那是软减仓（仍满仓持有集中头寸），本稿是硬空仓（熊市零新暴露）。预期失效：硬闸在V型反转初期踏空、均线附近反复切换导致换手上升；若train DD仍超标则说明5日持有期内的尾部风险无法靠入场闸控制。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class IndMomoHardGate:
    name = "ind_momo_hardgate_2612"
    top_n = 15
    min_rows = 125
    min_amount = 1e8
    top_ind = 5
    per_ind = 2

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        ind_ret = {}
        above_ma = 0
        total = 0
        r20_list = []
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
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 10:
                continue
            sigma = float(daily_ret.std())
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            sigma = min(max(sigma, 0.01), 0.06)
            ma120 = float(c.iloc[-120:].mean())
            total += 1
            if close > ma120:
                above_ma += 1
            r20_list.append(r20)
            ind = view.industry(code) or "未知"
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma, "ind": ind})
            ind_ret.setdefault(ind, []).append(r20)
        if not rows or total < 50:
            return []
        breadth = above_ma / total
        mkt_r20 = float(pd.Series(r20_list).median())
        if not (breadth > 0.5 and mkt_r20 > 0):
            return []
        ind_score = {k: float(pd.Series(v).mean()) for k, v in ind_ret.items() if len(v) >= 3}
        if not ind_score:
            return []
        strong = sorted(ind_score, key=lambda k: ind_score[k], reverse=True)[:self.top_ind]
        df = pd.DataFrame(rows)
        df = df[df["ind"].isin(strong)].copy()
        if df.empty:
            return []
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True])
        picked = []
        ind_cnt = {}
        for r in df.itertuples(index=False):
            if len(picked) >= self.top_n:
                break
            if ind_cnt.get(r.ind, 0) >= self.per_ind:
                continue
            ind_cnt[r.ind] = ind_cnt.get(r.ind, 0) + 1
            picked.append(r)
        picks = []
        for r in picked:
            sigma = r.sigma
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < r.close * 1.03 < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=round(r.close * 1.03, 2),
                stop=stop_px, target=target_px, hold_days=5,
                reason=f"市场闸通过(广度{breadth:.0%}>50%,市场中位r20={mkt_r20:.1%})，{r.ind}行业动量前{self.top_ind}，单行业≤{self.per_ind}只，个股20日{r.r20:.1%}/5日{r.r5:.1%}，σ={sigma:.1%}止损{stop_px:.2f}/止盈{target_px:.2f}"))
        return picks


STRATEGY = IndMomoHardGate()
