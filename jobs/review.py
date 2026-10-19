"""16:05 复盘 + 日报：针对最近一个已收盘且已结算的交易日。python -m jobs.review"""
from __future__ import annotations

from datetime import datetime

from agents import review as review_agent
from jobs import common


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = common.last_closed_day(now, calendar)
    if not store.job_done("settle", d):
        common.log.info("review %s: settle not done yet, skipped", d)
        return False
    llm = common.make_llm(settings, store)
    return common.run_job("review", d, lambda: review_agent.run_review(
        d, settings, store, llm, cache, notifier, calendar), store, notifier)


if __name__ == "__main__":
    main()
