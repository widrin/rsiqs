# engine/run_eval.py
"""评估 CLI：python -m engine.run_eval <strategy_name>

在独立子进程中运行，研究 agent 只能看到这里打印的单行 JSON（不含留出集数值）。
"""
import json
import logging
import math
import statistics
import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from config import get_settings
from data.cache import BarCache
from engine import backtest, broker, evaluator, metrics, rules
from engine.calendar import TradingCalendar
from engine.evaluator import evaluate
from engine.store import Store
from strategies.base import load_strategy

BENCHMARK = "sh000300"
HISTORY_START = date(2015, 6, 1)
INCUMBENT_KV = "eval_incumbent"  # 现任在 train+val 上的组合日收益统计，换现任后重算一次


def _incumbent_dsr(store: Store, panel, names, calendar: TradingCalendar, benchmark_nav: pd.Series,
                   n_trials: int, skip: str) -> float | None:
    """现任策略的 DSR：DSR 门槛是相对现任的，所以先算现任这一把。

    统计量缓存在 kv，同一只现任只回测一次（换现任后自动重算）。任何失败都返回 None，门槛退回
    DSR_FLOOR — 算不出基准不该让候选的评估失败。评估现任自身时没有基准可比，同样返回 None。
    """
    incs = store.strategies_with_status("incumbent")
    name = incs[0] if incs else None
    if not name or name == skip:
        return None
    cached = store.kv_get(INCUMBENT_KV)
    splits_key = repr(evaluator.SPLITS)
    stats = (cached if isinstance(cached, dict) and cached.get("name") == name
             and cached.get("splits") == splits_key else None)
    if stats is None:
        try:
            inc = load_strategy(name)
            rets = [evaluator.run_backtest(inc, panel, names, calendar, start, end or calendar.last(),
                                           benchmark_nav).nav.pct_change().dropna()
                    for start, end in (evaluator.SPLITS["train"], evaluator.SPLITS["val"])]
            r = pd.concat(rets)
            if len(r) < 4:
                return None
            stats = {"name": name, "splits": splits_key, "sr": evaluator.sharpe(r, periods=1),
                     "n_obs": int(len(r)), "skew": float(r.skew()), "kurt": float(r.kurt()) + 3.0}
            store.kv_set(INCUMBENT_KV, stats)
        except Exception:  # noqa: BLE001 - 现任算不出来就当没有基准
            logging.exception("计算现任 %s 的 DSR 基准失败，门槛退回 DSR_FLOOR", name)
            return None
    return evaluator.deflated_sharpe(stats["sr"], n_trials, int(stats["n_obs"]),
                                     float(stats["skew"]), float(stats["kurt"]))


def _namespace(obj) -> tuple:
    """模块或类（含基类）命名空间里全部条目的身份快照：替换、新增、删除任何属性都会改变它。值为模块的条目
    不计入：numpy、pandas 的子模块（numpy.polynomial 等）在第一次访问时才导入并写进命名空间，这不是改动。"""
    if isinstance(obj, type):
        items = [(f"{k.__qualname__}.{n}", v) for k in obj.__mro__ for n, v in vars(k).items()]
    else:
        items = list(vars(obj).items())
    return tuple(sorted((n, id(v)) for n, v in items if not isinstance(v, types.ModuleType)))


def _rules_state() -> tuple:
    """评估规则的快照，在加载策略之前取。策略代码与评估器在同一进程里（源码检查已禁止给 self 以外的属性赋值，
    这里是纵深防御）：评估结束后核对，被改过就作废这次试验。"""
    return (repr(evaluator.GATE), repr(evaluator.HOLDOUT_GATE), repr(evaluator.SPLITS), evaluator.run_backtest,
            evaluator.deflated_sharpe, evaluator.sharpe, evaluator._passes_gate, evaluator._dsr_bar,
            evaluator.DSR_FLOOR, backtest.SimBroker,
            broker.SimBroker.execute_day, metrics.summarize, metrics.deflated_sharpe,
            repr({k: v for k, v in vars(rules).items() if k.isupper()}),
            *(_namespace(m) for m in (evaluator, backtest, broker, metrics, rules, math, statistics, np)),
            *(_namespace(c) for c in (pd.Series, pd.DataFrame, pd.Index, statistics.NormalDist)),
            pd.concat, pd.DataFrame.__init__)


SETUP_EXIT = 3  # 评估环境本身出错（配置、缓存、数据库），与策略无关：调用方不计入试验


class SetupError(RuntimeError):
    """加载策略之前的准备步骤失败。"""


def _setup():
    settings = get_settings()
    db = Path(settings.root) / settings.db_path
    store = Store(db)
    cache = BarCache(db, client=None)
    bench = cache.load_index(BENCHMARK)
    if bench.empty:
        raise RuntimeError(f"缓存中没有基准指数 {BENCHMARK}，请先运行 python -m jobs.bootstrap")
    calendar = TradingCalendar(ts.date() for ts in bench.index)
    panel, names = cache.load_panel(cache.codes(), HISTORY_START, calendar.last())
    return store, calendar, panel, names, bench / bench.iloc[0]


def run(name: str) -> dict:
    try:
        store, calendar, panel, names, benchmark_nav = _setup()
    except Exception as e:  # noqa: BLE001 — 统一标成环境错误
        raise SetupError(f"{type(e).__name__}: {e}") from e
    before = _rules_state()  # 先取快照再加载策略：策略模块的顶层代码也可能改动评估规则
    strategy = load_strategy(name)
    dsr_incumbent = _incumbent_dsr(store, panel, names, calendar, benchmark_nav,
                                   store.trial_count() + 1, skip=name)  # 与 evaluate 用同一个 n_trials
    report = evaluate(strategy, name, panel, names, calendar, benchmark_nav, store,
                      dsr_incumbent=dsr_incumbent)
    if _rules_state() != before:
        trial = store.last_trial(name)
        if trial is not None:
            store.void_trial(trial["id"])
        raise RuntimeError("评估期间评估规则被改动，本次试验作废（策略代码不能修改 engine 模块）")
    return report.to_agent_dict()


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({"error": "usage: python -m engine.run_eval <strategy_name>"}), file=out)
        return 2
    try:
        result = run(argv[0])
    except KeyboardInterrupt:
        raise
    except SetupError as e:
        logging.exception("评估 %s 的准备步骤失败", argv[0])
        print(json.dumps({"error": f"评估环境出错：{e}", "stage": "setup"}, ensure_ascii=False), file=out)
        return SETUP_EXIT
    except BaseException as e:  # noqa: BLE001 - 含策略抛出的 SystemExit：任何失败都要以 JSON 回传给 agent
        logging.exception("评估 %s 失败", argv[0])
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), file=out)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str), file=out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    raise SystemExit(main())
