"""锁定的评估器：训练/验证/留出三段回测 + 门槛 + DSR。留出期数值永不外传。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.backtest import run_backtest
from engine.calendar import TradingCalendar
from engine.metrics import annualized_return, deflated_sharpe, max_drawdown, sharpe, summarize
from engine import regime as regime_mod
from engine.store import Store

SPLITS = {"train": (date(2016, 1, 1), date(2024, 12, 31)),
          "val": (date(2025, 1, 1), date(2026, 6, 30)),
          "holdout": (date(2026, 7, 1), None)}  # None = calendar.last()
# val 段只有 18 个月（约 378 个日收益），夏普的年化标准误约 0.8：真实夏普 1.0 的策略也有四成概率被
# 测到 0.8 以下。所以 val_sharpe 只作为"样本外没有崩掉"的兜底，与 train 同档；显著性交给 DSR——它用
# train+val 拼接的约 2,600 个日收益，检验力足够。
# val_excess_ann 的口径是相对沪深300价格指数，而策略净值来自前复权价（分红已隐含在价格里）、基准不含
# 分红。沪深300 股息率约 2%/年，门槛因此从 3% 抬到 5%，抵消这部分结构性偏差。
# train_max_dd 是九年的回撤上限：A 股长多动量必然经历熊市，放宽到 -45%，但挡住 -66%、-74% 这种
# "九年亏掉三分之二"的策略——它们能靠 val 段（2025-01~2026-06 是单边牛市）蒙过其余门槛。
GATE = {"train_sharpe": 0.5, "train_max_dd": -0.45, "val_sharpe": 0.5,
        "val_excess_ann": 0.05, "val_max_dd": -0.25}
# DSR 不再写死一个绝对门槛：策略够不够好，是相对现任判断的（目标是把钱赚得比现在这一套多）。
# 挑战者的 DSR 至少要达到现任的水平——同一条公式、同一个 n_trials，比较是公平的，而且试验次数
# 增加对双方的影响相同，不会像固定的 0.9 那样越研究越不可能达标。DSR_FLOOR 只是防噪声的底线：
# 现任本身很差时，不能让门槛一路降到随机水平。
DSR_FLOOR = 0.5
# 行情专精策略（target_regime）必须在目标行情里有真实持仓：日收益标准差低于此值视为空仓套利
# （空仓时收益恒为0，std=0；哪怕轻仓也会轻易超过此线）。
MIN_DAILY_VOL = 0.001
HOLDOUT_GATE = {"excess_ann": 0.0, "sharpe": 0.5}


@dataclass
class EvalReport:
    strategy: str
    train: dict
    val: dict
    dsr: float
    dsr_bar: float
    gate_pass: bool
    holdout_pass: bool | None
    holdout_reason: str | None
    n_trials: int
    target_regime: str | None = None

    def to_agent_dict(self) -> dict:
        # 只含训练/验证指标与留出期的通过与否；本类本身也不保存任何留出期数值
        return {"strategy": self.strategy, "train": dict(self.train), "val": dict(self.val),
                "dsr": self.dsr, "dsr_bar": self.dsr_bar, "gate_pass": self.gate_pass,
                "holdout_pass": self.holdout_pass, "holdout_reason": self.holdout_reason,
                "n_trials": self.n_trials, "target_regime": self.target_regime}


def _dsr_bar(dsr_incumbent: float | None) -> float:
    """DSR 门槛：不低于现任，且不低于噪声底线。没有现任基准（首次评估、库为空）时只留底线。"""
    return DSR_FLOOR if dsr_incumbent is None else max(DSR_FLOOR, float(dsr_incumbent))


def _passes_gate(train: dict, val: dict, dsr: float, dsr_incumbent: float | None = None,
                 target_regime: str | None = None) -> bool:
    base = (train["sharpe"] >= GATE["train_sharpe"] and train["max_dd"] >= GATE["train_max_dd"]
            and val["sharpe"] >= GATE["val_sharpe"]
            and val["excess_ann"] >= GATE["val_excess_ann"] and val["max_dd"] >= GATE["val_max_dd"]
            and dsr >= _dsr_bar(dsr_incumbent))
    if not base:
        return False
    # 行情专精策略用超额收益口径：必须在目标行情里真实持仓，否则空仓就能"跑赢"下跌的基准
    if target_regime is not None:
        if train.get("daily_vol", 0.0) < MIN_DAILY_VOL or val.get("daily_vol", 0.0) < MIN_DAILY_VOL:
            return False
    return True


def _data_end(panel, calendar: TradingCalendar) -> date:
    # 新浪交易日历含未来日期；留出期只回放到面板最后一个有数据的交易日
    last = max((df.index.max() for df in panel.values() if len(df)), default=None)
    return calendar.last() if last is None else min(calendar.last(), last.date())


def _period_regime(benchmark_nav: pd.Series, end_date: date) -> str | None:
    """截至 end_date 的行情状态（牛/熊/震荡）。数据不足时返回 None。"""
    ts = pd.Timestamp(end_date)
    series = benchmark_nav[benchmark_nav.index <= ts].dropna()
    if len(series) < regime_mod.MA_WINDOW + 1:
        return None
    return regime_mod.classify(series).state


def _regime_subset(nav: pd.Series, bench_nav: pd.Series, target_regime: str,
                   regime_map: dict) -> tuple[pd.Series, pd.Series]:
    """只保留行情状态 == target_regime 的交易日，返回 (过滤后日收益, 过滤后基准日收益)。"""
    r = nav.astype(float).pct_change().dropna()
    br = bench_nav.astype(float).pct_change().dropna()
    r, br = r.align(br, join="inner")
    mask = r.index.map(lambda ts: regime_map.get(ts.date()) == target_regime)
    return r[mask], br[mask]


def _metrics_from_rets(r: pd.Series, br: pd.Series) -> dict:
    """从日收益重建净值并算指标（与 metrics.summarize 同口径）。"""
    if len(r) == 0:
        return {"total_return": 0.0, "ann_return": 0.0, "sharpe": 0.0,
                "max_dd": 0.0, "excess_ann": 0.0, "n_days": 0}
    return summarize((1.0 + r).cumprod(), (1.0 + br).cumprod())


def _excess_metrics(ex: pd.Series) -> dict:
    """从超额日收益算指标（行情专精策略用超额口径：衡量相对基准的 alpha）。"""
    if len(ex) == 0:
        return {"total_return": 0.0, "ann_return": 0.0, "sharpe": 0.0,
                "max_dd": 0.0, "excess_ann": 0.0, "n_days": 0}
    nav_ex = (1.0 + ex).cumprod()
    return {"total_return": float(nav_ex.iloc[-1] - 1.0),
            "ann_return": annualized_return(ex),
            "sharpe": sharpe(ex),
            "max_dd": max_drawdown(nav_ex),
            "excess_ann": float(ex.mean() * 252),
            "n_days": int(len(ex))}


def evaluate(strategy, name: str, panel, names, calendar: TradingCalendar, benchmark_nav: pd.Series,
             store: Store, params: dict | None = None, splits: dict | None = None,
             dsr_incumbent: float | None = None, industries: dict[str, str] | None = None) -> EvalReport:
    splits = splits or SPLITS
    target_regime = getattr(strategy, "target_regime", None)

    def bt(key: str):
        start, end = splits[key]
        return run_backtest(strategy, panel, names, calendar, start, end or _data_end(panel, calendar),
                            benchmark_nav, industries=industries)

    n_trials = store.trial_count() + 1  # 含本次
    try:
        tr, va = bt("train"), bt("val")
    except BaseException:
        # 跑不完的试验同样计入 n_trials（DSR 的多重检验校正）；训练、验证段的报错照常抛出
        store.add_trial(name, params or {}, None, None, False, None)
        raise

    # 按目标行情过滤：策略声明 target_regime 时，只在该行情的交易日上算指标与 DSR。
    # 解决"牛市策略被九年熊市拖垮、熊市策略被牛市拉高"的口径错位——不同行情用不同策略，
    # 各自在自己擅长的行情内比较。
    if target_regime is not None:
        tr_start = splits["train"][0]
        va_end = splits["val"][1] or _data_end(panel, calendar)
        regime_map = regime_mod.daily_regime(benchmark_nav, tr_start, va_end)
        tr_r, tr_br = _regime_subset(tr.nav, benchmark_nav, target_regime, regime_map)
        va_r, va_br = _regime_subset(va.nav, benchmark_nav, target_regime, regime_map)
        # 行情专精策略用超额收益口径：策略价值是"在该行情里跑赢基准"，而非靠 beta 或空仓
        tr_ex, va_ex = tr_r - tr_br, va_r - va_br
        tr_metrics = _excess_metrics(tr_ex)
        va_metrics = _excess_metrics(va_ex)
        tr_metrics["daily_vol"] = float(tr_r.std()) if len(tr_r) > 1 else 0.0
        va_metrics["daily_vol"] = float(va_r.std()) if len(va_r) > 1 else 0.0
        r = pd.concat([tr_ex, va_ex])
    else:
        tr_metrics, va_metrics = tr.metrics, va.metrics
        tr_r = tr.nav.pct_change().dropna()
        va_r = va.nav.pct_change().dropna()
        tr_metrics["daily_vol"] = float(tr_r.std()) if len(tr_r) > 1 else 0.0
        va_metrics["daily_vol"] = float(va_r.std()) if len(va_r) > 1 else 0.0
        r = pd.concat([tr_r, va_r])

    dsr = deflated_sharpe(sharpe(r, periods=1), n_trials, len(r),
                          float(r.skew()) if len(r) > 2 else 0.0,
                          float(r.kurt()) + 3.0 if len(r) > 3 else 3.0)
    gate_pass = _passes_gate(tr_metrics, va_metrics, dsr, dsr_incumbent, target_regime)
    # val 与 holdout 行情状态可能不同：动量策略在熊市 holdout 失败是预期，不算过拟合否决
    val_regime = _period_regime(benchmark_nav, splits["val"][1])
    ho_regime = _period_regime(benchmark_nav, _data_end(panel, calendar))
    regime_diff = (val_regime is not None and ho_regime is not None and val_regime != ho_regime)
    # 目标行情与 holdout 行情不符时，holdout 不适用（该策略不该在这段行情被考核）
    if target_regime is not None and ho_regime is not None and ho_regime != target_regime:
        regime_diff = True
    # 先入库再回放留出期：留出期中途失败或进程被杀，这次试验也已计数
    trial_id = store.add_trial(name, params or {}, tr_metrics["sharpe"], va_metrics["sharpe"], gate_pass, None)
    holdout_pass = None
    holdout_reason = f"regime_diff:{val_regime}->{ho_regime}" if regime_diff else None
    if gate_pass:
        if regime_diff:
            store.set_trial_holdout(trial_id, None)  # 留出门槛不适用，保持 holdout_pass=None
        else:
            interrupted = False
            try:
                ho = bt("holdout").metrics
                holdout_pass = bool(ho["n_days"] > 0 and ho["excess_ann"] >= HOLDOUT_GATE["excess_ann"]
                                    and ho["sharpe"] >= HOLDOUT_GATE["sharpe"])
            except BaseException as e:  # noqa: BLE001 - 任何异常只记未通过：异常文本可能带出留出期数值，不外传
                holdout_pass, interrupted = False, isinstance(e, KeyboardInterrupt)
            store.set_trial_holdout(trial_id, holdout_pass)
            if interrupted:  # Ctrl+C 照样中断，但抛出不带消息、不带原异常的新异常（策略也可能抛 KeyboardInterrupt）
                raise KeyboardInterrupt from None
    return EvalReport(strategy=name, train=tr_metrics, val=va_metrics, dsr=dsr,
                      dsr_bar=_dsr_bar(dsr_incumbent), gate_pass=gate_pass,
                      holdout_pass=holdout_pass, holdout_reason=holdout_reason,
                      n_trials=store.trial_count(), target_regime=target_regime)
