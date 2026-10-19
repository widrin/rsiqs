"""锁定的评估器：训练/验证/留出三段回测 + 门槛 + DSR。留出期数值永不外传。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.backtest import run_backtest
from engine.calendar import TradingCalendar
from engine.metrics import deflated_sharpe, sharpe
from engine.store import Store

SPLITS = {"train": (date(2016, 1, 1), date(2024, 12, 31)),
          "val": (date(2025, 1, 1), date(2026, 6, 30)),
          "holdout": (date(2026, 7, 1), None)}  # None = calendar.last()
GATE = {"train_sharpe": 0.5, "val_sharpe": 0.8, "val_excess_ann": 0.03, "val_max_dd": -0.25, "dsr": 0.90}
HOLDOUT_GATE = {"excess_ann": 0.0, "sharpe": 0.5}


@dataclass
class EvalReport:
    strategy: str
    train: dict
    val: dict
    dsr: float
    gate_pass: bool
    holdout_pass: bool | None
    n_trials: int

    def to_agent_dict(self) -> dict:
        # 只含训练/验证指标与留出期的通过与否；本类本身也不保存任何留出期数值
        return {"strategy": self.strategy, "train": dict(self.train), "val": dict(self.val),
                "dsr": self.dsr, "gate_pass": self.gate_pass, "holdout_pass": self.holdout_pass,
                "n_trials": self.n_trials}


def _passes_gate(train: dict, val: dict, dsr: float) -> bool:
    return (train["sharpe"] >= GATE["train_sharpe"] and val["sharpe"] >= GATE["val_sharpe"]
            and val["excess_ann"] >= GATE["val_excess_ann"] and val["max_dd"] >= GATE["val_max_dd"]
            and dsr >= GATE["dsr"])


def _data_end(panel, calendar: TradingCalendar) -> date:
    # 新浪交易日历含未来日期；留出期只回放到面板最后一个有数据的交易日
    last = max((df.index.max() for df in panel.values() if len(df)), default=None)
    return calendar.last() if last is None else min(calendar.last(), last.date())


def evaluate(strategy, name: str, panel, names, calendar: TradingCalendar, benchmark_nav: pd.Series,
             store: Store, params: dict | None = None, splits: dict | None = None) -> EvalReport:
    splits = splits or SPLITS

    def bt(key: str):
        start, end = splits[key]
        return run_backtest(strategy, panel, names, calendar, start, end or _data_end(panel, calendar), benchmark_nav)

    n_trials = store.trial_count() + 1  # 含本次
    try:
        tr, va = bt("train"), bt("val")
    except BaseException:
        # 跑不完的试验同样计入 n_trials（DSR 的多重检验校正）；训练、验证段的报错照常抛出
        store.add_trial(name, params or {}, None, None, False, None)
        raise
    r = pd.concat([tr.nav.pct_change().dropna(), va.nav.pct_change().dropna()])
    dsr = deflated_sharpe(sharpe(r, periods=1), n_trials, len(r), float(r.skew()) if len(r) > 2 else 0.0,
                          float(r.kurt()) + 3.0 if len(r) > 3 else 3.0)
    gate_pass = _passes_gate(tr.metrics, va.metrics, dsr)
    # 先入库再回放留出期：留出期中途失败或进程被杀，这次试验也已计数
    trial_id = store.add_trial(name, params or {}, tr.metrics["sharpe"], va.metrics["sharpe"], gate_pass, None)
    holdout_pass = None
    if gate_pass:
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
    return EvalReport(strategy=name, train=tr.metrics, val=va.metrics, dsr=dsr, gate_pass=gate_pass,
                      holdout_pass=holdout_pass, n_trials=store.trial_count())
