"""08:30 盘前扫描 + 深度分析（仅交易日）。python -m jobs.premarket_scan

扫描失败由 run_job 告警（09:25 定稿时会再补跑扫描）。深度分析失败只降级：任务仍记 ok，
推一条"盘前分析未完成"告警，09:25 时那一路按量化/规则推荐。"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta

from agents import premarket
from jobs import common
from notify.format import format_alert
from strategies.base import load_strategy

KINDS = ("active", "candidate")
ANALYZE_CUTOFF = time(9, 25)  # 晚于此刻启动（开机补跑）只扫描：定稿已开始，计划用不上
ANALYZE_DEADLINE = time(9, 22)  # 深度分析必须在此前结束，给 09:25 定稿留出余量


def scan(d: date, settings, store, client, cache, calendar) -> list:
    """只做量化扫描并保存候选；premarket_final 补跑扫描时也调用它。"""
    strategy = load_strategy(common.incumbent_strategy(store))
    return premarket.scan(d, settings, store, cache, client, strategy, calendar)


def analyze_all(d: date, settings, store, now: datetime) -> dict[str, bool]:
    """两路深度分析，最晚到 ANALYZE_DEADLINE；run_analyze 本身抛异常时按两路都未完成处理。"""
    try:
        return premarket.run_analyze(d, settings, store, common.make_llm(settings, store),
                                     deadline=datetime.combine(d, ANALYZE_DEADLINE), now=now)
    except Exception:  # noqa: BLE001 — 分析只影响推荐质量，不能让扫描任务失败
        common.log.exception("premarket analyze %s crashed", d)
        return {k: False for k in KINDS}


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    t0 = _time.monotonic()  # 在 context() 之前取：开机补跑时初始化依赖也要花时间
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_scan %s: not a trading day", d)
        return False
    status: dict[str, bool] = {}

    def job() -> None:
        scan(d, settings, store, client, cache, calendar)
        if now.time() >= ANALYZE_CUTOFF:
            common.log.info("premarket_scan %s: started at %s, analysis skipped", d, now.time())
            return
        # 扫描本身可能要几分钟：按实际经过的时间算出分析开始的时刻
        status.update(analyze_all(d, settings, store, now + timedelta(seconds=_time.monotonic() - t0)))

    ok = common.run_job("premarket_scan", d, job, store, notifier)
    failed = sorted(k for k, done in status.items() if not done)
    if failed:
        notifier.send_text(format_alert(
            "盘前分析未完成", f"9:25 将按量化/规则推荐（未完成：{'、'.join(failed)}）"))
    return ok


if __name__ == "__main__":
    main()
