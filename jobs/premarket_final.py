"""09:25 竞价定稿并推送（仅交易日）。python -m jobs.premarket_final

计划任务 09:25 触发；入口先等到 09:25:15，让竞价快照落定后再定稿；09:35 之后视为过时跳过。"""
from __future__ import annotations

import threading
import time as _time
from datetime import datetime, time, timedelta

from agents import premarket
from jobs import common, premarket_scan
from notify.format import format_alert

READY_AT = time(9, 25, 15)  # 集合竞价 09:25 结束，留 15 秒给行情源刷新竞价价
STALE_AFTER = time(9, 35)
SCAN_DEADLINE = time(9, 27)  # 补跑扫描最晚到此刻：之后还要留时间给定稿（run_final 自己最多约 110 秒）
SCAN_MIN_SECONDS = 90        # 开机晚（09:27 之后才补跑）时至少给扫描这么多秒，但不晚于 SCAN_HARD_DEADLINE
SCAN_HARD_DEADLINE = time(9, 33)


def _within(seconds: float, fn):
    """在守护线程里跑 fn，最多等 seconds 秒；超时抛 TimeoutError（线程随进程退出）。"""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — 原样转交给调用方
            box["error"] = e

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(max(seconds, 0.0))
    if th.is_alive():
        raise TimeoutError(f"补跑扫描 {max(seconds, 0.0):.0f} 秒内没有完成，今日不出推荐")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def main(now: datetime | None = None, sleep=_time.sleep) -> bool:
    now = now or datetime.now()
    t0 = _time.monotonic()  # 在 context() 之前取：装配依赖花的时间要从等待里扣掉
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_final %s: not a trading day", d)
        return False
    if now.time() > STALE_AFTER:
        common.log.info("premarket_final %s: stale at %s, skipped", d, now.time())
        key = f"premarket_stale_alerted:{d.isoformat()}"
        if not store.job_done("premarket_final", d) and not store.kv_get(key) and notifier.send_text(format_alert(
                "盘前推荐未推送", f"09:25–09:35 期间电脑没有运行（{now:%H:%M} 才启动），今日没有推送推荐")):
            store.kv_set(key, True)
        return False
    ready = datetime.combine(d, READY_AT)
    wait = (ready - now).total_seconds() - (_time.monotonic() - t0)
    if wait > 0:
        common.log.info("premarket_final %s: waiting %.0fs for auction data", d, wait)
        sleep(wait)
    t1 = _time.monotonic()
    base = ready if wait > 0 else now + timedelta(seconds=t1 - t0)

    def clock() -> datetime:
        return base + timedelta(seconds=_time.monotonic() - t1)

    if store.load_candidates(d) is None:
        # 开机晚或 08:30 扫描失败：只补跑扫描（不做深度分析），run_final 会退回量化推荐。
        # 看候选是否已保存而不是看 premarket_scan 任务是否 ok：扫描完成后深度分析可能还在跑，任务尚未记 ok。
        # 不拿任务锁：08:30 的进程可能还在做深度分析；扫描最晚到 SCAN_DEADLINE
        start = clock()
        until = min(max(datetime.combine(d, SCAN_DEADLINE), start + timedelta(seconds=SCAN_MIN_SECONDS)),
                    datetime.combine(d, SCAN_HARD_DEADLINE))
        budget = (until - start).total_seconds()
        scanned = common.run_job("premarket_scan", d, lambda: _within(budget, lambda: premarket_scan.scan(
            d, settings, store, client, cache, calendar)), store, notifier, lock=False)
        if not scanned:  # 按设计当天不出推荐。08:30 已告警过扫描失败时 run_job 不再告警，这里单独说明结果
            notifier.send_text(format_alert("盘前推荐未推送", "09:25 补跑扫描失败或超时，今日不出推荐（原因见日志）"))
            return False
        if clock().time() > STALE_AFTER:
            notifier.send_text(format_alert("盘前推荐未推送", f"补跑扫描到 {clock():%H:%M} 才完成，已过时"))
            return False
    llm = common.make_llm(settings, store)
    return common.run_job("premarket_final", d, lambda: premarket.run_final(
        d, settings, store, llm, client, notifier, calendar), store, notifier)


if __name__ == "__main__":
    main()
