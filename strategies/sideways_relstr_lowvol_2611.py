"""假设：震荡市的绝对收益需要同时有横截面资金承接与风险约束，单纯最低波动只能降低组合波动，不能提供足够收益。本策略以全池20日收益中位数作为市场代理，选择20日相对强度为正的股票，并用20日波动率后半区过滤剔除高波票；同时要求5日涨幅不超过5%、20日涨幅不超过20%，避免把短期加速和突破末端误认为可持续强度。经济学依据是震荡市缺乏指数趋势，资金更倾向留在有相对承接的少数股票，而机构调仓与信息扩散使这种相对强度可短期延续；短期未过热与低波约束则降低彩票偏好、隔夜缺口和T+1止损摩擦。与现任『20日动量−5日反转』不同，本策略按相对全池强度排序，不奖励5日下跌；与上一轮纯低波策略不同，低波只是风险约束而非收益信号，不做内部大盘择时。出场沿用20日波动率自适应止损止盈和5日持有，控制交易结构差异。预期在相对强度属于抱团末端、市场发生快速风格切换，或防御行业集中补跌时失效。"""
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class SidewaysRelStrLowVol:
    name = "sideways_relstr_lowvol_2611"
    target_regime = "sideways"
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
            daily_ret = c.pct_change().dropna().iloc[-20:]
            if len(daily_ret) < 20:
                continue
            sigma = float(daily_ret.std())
            if not all(math.isfinite(x) for x in (r20, r5, sigma)) or sigma <= 0:
                continue
            if r5 > 0.05 or r20 > 0.20:
                continue
            rows.append({"code": code, "name": name, "close": close,
                         "r20": r20, "r5": r5, "sigma": sigma})
        if len(rows) < 20:
            return []
        df = pd.DataFrame(rows)
        market_r20 = float(df["r20"].median())
        sigma_cut = float(df["sigma"].quantile(0.50))
        df = df[(df["r20"] > market_r20) & (df["sigma"] <= sigma_cut)].copy()
        if df.empty:
            return []
        df["rel20"] = df["r20"] - market_r20
        df["score"] = df["rel20"].rank() - 0.5 * df["sigma"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        picks = []
        for r in df.itertuples(index=False):
            sigma = min(max(float(r.sigma), 0.01), 0.06)
            entry_max = round(r.close * 1.02, 2)
            stop_px = round(r.close * (1 - 1.5 * sigma), 2)
            target_px = round(r.close * (1 + 3.0 * sigma), 2)
            if not stop_px < entry_max < target_px:
                continue
            picks.append(Pick(
                code=r.code, name=r.name, score=float(r.score),
                entry_max=entry_max, stop=stop_px, target=target_px,
                hold_days=5,
                reason=f"20日收益{r.r20:.1%}，高于全池中位数{market_r20:.1%}；5日涨幅{r.r5:.1%}未过热，20日波动率{sigma:.1%}处于较低半区，持有5日"))
        return picks


STRATEGY = SidewaysRelStrLowVol()
