"""11:35 午盘简报（仅交易日）。python -m jobs.midday

只读任务：不修改今日推荐、不撮合、不写 playbook。错过窗口（13:00 后才启动）直接跳过，
不补发——午盘数据只对当天下午有意义。
"""
from __future__ import annotations

from datetime import datetime, time

from agents import midday as midday_agent
from jobs import common

READY_AT = time(11, 35)   # 上午收盘后 5 分钟：行情源已落定，下午开盘前还有时间看
STALE_AFTER = time(13, 0)  # 下午开盘后才启动视为过时


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("midday %s: not a trading day", d)
        return False
    if now.time() > STALE_AFTER:
        common.log.info("midday %s: stale at %s, skipped", d, now.time())
        return False
    llm = common.make_llm(settings, store)
    return common.run_job("midday", d,
                          lambda: midday_agent.run_midday(d, store, client, llm, notifier),
                          store, notifier)


if __name__ == "__main__":
    main()
