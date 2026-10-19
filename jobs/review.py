"""16:05 复盘 + 日报：针对最近一个已收盘且已结算的交易日，并回补之前已结算、还没复盘的交易日。
python -m jobs.review"""
from __future__ import annotations

from datetime import datetime

from agents import review as review_agent
from jobs import common

SETTLE_WAIT_SEC = 1800  # 结算正在别的进程里跑（唤醒或开机后与 Settle 同时补跑）时最多等这么久
MAX_BACKFILL = 3        # 最多复盘这么多个交易日（含当天）：结算暂停或开机晚时回补漏掉的日子


def pending_reviews(store, calendar, last) -> list:
    """从 last 往前：已结算、还没复盘的连续交易日（遇到已复盘或未结算的日子就停），按日期升序。"""
    days, d = [], last
    while len(days) < MAX_BACKFILL and store.job_done("settle", d) and not store.job_done("review", d):
        days.append(d)
        d = calendar.prev_day(d)
    return days[::-1]


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    last = common.last_closed_day(now, calendar)
    with common.job_lock("settle_main", wait=SETTLE_WAIT_SEC):
        pass  # 等正在跑的结算结束，再看最近一天是否已结算
    if not store.job_done("settle", last):
        common.log.info("review %s: settle not done yet, skipped", last)
        return False
    if store.job_done("review", last):
        common.log.info("skip review %s (already ok)", last)
        return True
    llm = common.make_llm(settings, store)
    ok = True
    for d in pending_reviews(store, calendar, last):
        ok = common.run_job("review", d, lambda d=d: review_agent.run_review(
            d, settings, store, llm, cache, notifier, calendar), store, notifier) and ok
    return ok


if __name__ == "__main__":
    main()
