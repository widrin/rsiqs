"""任务入口公共设施：幂等运行、告警、日志、依赖装配。"""
from __future__ import annotations

import logging
import os
import traceback
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

from config import get_settings
from data.akshare_client import AkClient
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.store import Store
from notify.format import format_alert
from notify.wecom import NullNotifier, WeCom

log = logging.getLogger("rsiqs.jobs")

LOCK_DIR: Path | None = None  # context() 设为 <root>/logs/locks；为 None（测试）时不加锁

if os.name == "nt":
    import msvcrt

    def _lock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def job_lock(job: str):
    """跨进程互斥：同名任务同一时刻只有一个进程在跑（例如开机补跑与定时任务重叠）。拿到锁时 yield True，
    别的进程正持有时 yield False。锁由操作系统随进程退出释放，进程被杀也不会留下残锁。"""
    if LOCK_DIR is None:
        yield True
        return
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_DIR / f"{job}.lock", "a+b") as f:
        try:
            _lock(f)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            _unlock(f)


def setup_logging(root: Path) -> None:
    """所有模块的日志写入 <root>/logs/jobs.log。handler 挂在根 logger 上：jobs、agents
    （LLMError 的 subtype 与输出摘要）、notify（推送失败）都进同一文件；重复调用不重复挂。"""
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = os.path.abspath(log_dir / "jobs.log")
    base = logging.getLogger()
    if not any(getattr(h, "baseFilename", None) == target for h in base.handlers):
        h = logging.FileHandler(target, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        base.addHandler(h)
    base.setLevel(logging.INFO)


INDEX_SYMBOL = "sh000300"
BENCH_ACCOUNT = "bench:csi300"
LLM_ACCOUNTS = ("h:active", "h:candidate")
DEFAULT_INCUMBENT = "baseline_momo"
CLOSE_READY = time(15, 10)  # 当日日线视为可用的最早时刻


def today() -> date:
    return datetime.now().date()


def last_closed_day(now: datetime, calendar: TradingCalendar) -> date:
    """不晚于 now、且已收盘（>=15:10）的最近交易日。"""
    d = now.date()
    if calendar.is_trading_day(d) and now.time() >= CLOSE_READY:
        return d
    return calendar.prev_day(d)


def incumbent_strategy(store: Store) -> str:
    names = store.strategies_with_status("incumbent")
    return names[0] if names else DEFAULT_INCUMBENT


def make_llm(settings, store: Store):
    """LLM 工厂：agents.llm.LLM（经本机 claude.exe 无头模式调用 Claude）；测试中打桩为 FakeLLM。"""
    from agents.llm import LLM

    return LLM(settings, store)


def run_job(job: str, d: date, fn, store: Store, notifier, force: bool = False, lock: bool = True) -> bool:
    """幂等执行：已成功则跳过（返回 True）；成功标记 ok；异常标记 error 并告警（返回 False）。

    lock=True 时持有跨进程锁：同名任务正在别的进程里跑时直接返回 False（不标记、不告警）。
    同一任务同一天只告警一次：计划任务当天会定时重试，重复失败只记日志。"""
    if not force and store.job_done(job, d):
        log.info("skip %s %s (already ok)", job, d)
        return True
    with job_lock(job) if lock else _no_lock() as got:
        if not got:
            log.info("skip %s %s (running in another process)", job, d)
            return False
        if not force and store.job_done(job, d):  # 等锁期间别的进程已经完成
            log.info("skip %s %s (already ok)", job, d)
            return True
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — 任务边界统一兜底
            log.error("job %s %s failed: %s\n%s", job, d, e, traceback.format_exc())
            try:
                store.mark_job(job, d, "error")
                key = f"job_alerted:{job}:{d.isoformat()}"
                if not store.kv_get(key):
                    notifier.send_text(format_alert(f"任务失败 {job} {d.isoformat()}",
                                                    f"{type(e).__name__}: {e}\n（当天会定时重试；再次失败只记日志）"))
                    store.kv_set(key, True)
            except Exception as ne:  # noqa: BLE001 — 记录或告警本身失败：原始异常已写进日志
                log.error("recording failure of %s %s failed: %s", job, d, ne)
            return False
        store.mark_job(job, d, "ok")
    log.info("job %s %s ok", job, d)
    return True


@contextmanager
def _no_lock():
    yield True


def load_calendar(store: Store, client: AkClient, now: date | None = None) -> TradingCalendar:
    """交易日历缓存在 kv "calendar"（ISO 字符串列表），kv "calendar_fetched" 记录刷新日；
    每天最多拉取一次，拉取失败时用旧缓存。"""
    now = now or today()
    dates = store.kv_get("calendar")
    if not dates or store.kv_get("calendar_fetched") != now.isoformat():
        try:
            dates = [x.isoformat()[:10] for x in client.trade_calendar()]
            store.kv_set("calendar", dates)
            store.kv_set("calendar_fetched", now.isoformat())
        except Exception:
            if not dates:
                raise
            log.warning("trade_calendar refresh failed, using cached calendar")
    return TradingCalendar(date.fromisoformat(x) for x in dates)


def context():
    """装配依赖：(settings, store, client, cache, calendar, notifier)。"""
    global LOCK_DIR
    settings = get_settings()
    setup_logging(settings.root)
    LOCK_DIR = settings.root / "logs" / "locks"
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = settings.root / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    client = AkClient()
    cache = BarCache(db_path, client)
    notifier = (WeCom(settings.wecom_webhook_url, log_dir=settings.root / "logs")
                if settings.wecom_webhook_url else NullNotifier())
    calendar = load_calendar(store, client)
    return settings, store, client, cache, calendar, notifier
