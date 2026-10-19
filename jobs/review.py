"""16:05 复盘 + 日报：针对最近一个已收盘且已结算的交易日，并回补之前已结算、还没复盘的交易日。
python -m jobs.review"""
from __future__ import annotations

import time as _time
from datetime import datetime

from agents import review as review_agent
from jobs import common

SETTLE_WAIT_SEC = 1800  # 结算正在别的进程里跑（唤醒或开机后与 Settle 同时补跑）时最多等这么久
START_GRACE_SEC = 120   # 两个任务同时启动时，Settle 可能还没拿到锁：这段时间内结算没完成就继续等
POLL_SEC = 5
MAX_BACKFILL = 3        # 最多复盘这么多个交易日（含当天）：结算暂停或开机晚时回补漏掉的日子


def pending_reviews(store, calendar, last) -> list:
    """last 及之前共 MAX_BACKFILL 个交易日里已结算、还没复盘的日子（遇到未结算的日子就停），按日期升序。
    更早漏掉的日子不再补（日报只是记录，LLM 复盘只做最近一天）。"""
    days, d = [], last
    for _ in range(MAX_BACKFILL):
        if not store.job_done("settle", d):
            break
        if not store.job_done("review", d):
            days.append(d)
        d = calendar.prev_day(d)
    return days[::-1]


def _wait_for_settle(store, last, sleep) -> None:
    """等正在跑的结算结束。只在有跨进程锁（真实运行）时等：测试里 LOCK_DIR 为 None，直接返回。"""
    if common.LOCK_DIR is None:
        return
    grace_end = _time.monotonic() + START_GRACE_SEC
    while not store.job_done("settle", last):
        with common.job_lock("settle_main", wait=SETTLE_WAIT_SEC):
            pass
        if store.job_done("settle", last) or _time.monotonic() >= grace_end:
            return
        sleep(POLL_SEC)


def main(now: datetime | None = None, sleep=_time.sleep) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    last = common.last_closed_day(now, calendar)
    _wait_for_settle(store, last, sleep)
    if not store.job_done("settle", last):
        common.log.info("review %s: settle not done yet, skipped", last)
        return False
    days = pending_reviews(store, calendar, last)
    if not days:
        common.log.info("skip review %s (already ok)", last)
        return True
    llm = common.make_llm(settings, store)
    ok = True
    for d in days:  # 较早的日子只补发日报（不调用 LLM），最近一天照常复盘；某天失败不挡住其余日子
        ok = common.run_job("review", d, lambda d=d: review_agent.run_review(
            d, settings, store, llm, cache, notifier, calendar, backfill=(d != last)), store, notifier) and ok
    return ok


if __name__ == "__main__":
    main()
