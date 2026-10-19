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
            evaluator.deflated_sharpe, evaluator.sharpe, evaluator._passes_gate, backtest.SimBroker,
            broker.SimBroker.execute_day, metrics.summarize, metrics.deflated_sharpe,
            repr({k: v for k, v in vars(rules).items() if k.isupper()}),
            *(_namespace(m) for m in (evaluator, backtest, broker, metrics, rules, math, statistics, np)),
            *(_namespace(c) for c in (pd.Series, pd.DataFrame, pd.Index, statistics.NormalDist)),
            pd.concat, pd.DataFrame.__init__)


def run(name: str) -> dict:
    settings = get_settings()
    db = Path(settings.root) / settings.db_path
    store = Store(db)
    cache = BarCache(db, client=None)
    bench = cache.load_index(BENCHMARK)
    if bench.empty:
        raise RuntimeError(f"缓存中没有基准指数 {BENCHMARK}，请先运行 python -m jobs.bootstrap")
    calendar = TradingCalendar(ts.date() for ts in bench.index)
    panel, names = cache.load_panel(cache.codes(), HISTORY_START, calendar.last())
    benchmark_nav = bench / bench.iloc[0]
    before = _rules_state()  # 先取快照再加载策略：策略模块的顶层代码也可能改动评估规则
    strategy = load_strategy(name)
    report = evaluate(strategy, name, panel, names, calendar, benchmark_nav, store)
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
    except BaseException as e:  # noqa: BLE001 - 含策略抛出的 SystemExit：任何失败都要以 JSON 回传给 agent
        logging.exception("评估 %s 失败", argv[0])
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), file=out)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str), file=out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    raise SystemExit(main())
