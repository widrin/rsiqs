"""09:25 竞价定稿并推送（仅交易日）。python -m jobs.premarket_final

计划任务 09:25 触发；入口先等到 09:25:15，让竞价快照落定后再定稿；09:35 之后视为过时跳过。"""
from __future__ import annotations

import time as _time
from datetime import datetime, time

from agents import premarket
from jobs import common, premarket_scan

READY_AT = time(9, 25, 15)  # 集合竞价 09:25 结束，留 15 秒给行情源刷新竞价价
STALE_AFTER = time(9, 35)


def main(now: datetime | None = None, sleep=_time.sleep) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_final %s: not a trading day", d)
        return False
    if now.time() > STALE_AFTER:
        common.log.info("premarket_final %s: stale at %s, skipped", d, now.time())
        return False
    wait = (datetime.combine(d, READY_AT) - now).total_seconds()
    if wait > 0:
        common.log.info("premarket_final %s: waiting %.0fs for auction data", d, wait)
        sleep(wait)
    if store.load_candidates(d) is None:
        # 开机晚或 08:30 扫描失败：只补跑扫描（不做深度分析），run_final 会退回量化推荐。
        # 看候选是否已保存而不是看 premarket_scan 任务是否 ok：扫描完成后深度分析可能还在跑，任务尚未记 ok
        scanned = common.run_job("premarket_scan", d, lambda: premarket_scan.scan(
            d, settings, store, client, cache, calendar), store, notifier)
        if not scanned:
            return False  # 扫描失败已告警；按设计当天不出推荐
    llm = common.make_llm(settings, store)
    return common.run_job("premarket_final", d, lambda: premarket.run_final(
        d, settings, store, llm, client, notifier, calendar), store, notifier)


if __name__ == "__main__":
    main()
