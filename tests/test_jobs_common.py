import logging
from datetime import date, datetime, time

import pytest

from agents.llm import LLMError
from agents.premarket import AuctionCheck, PremarketPlan
from agents.research import ResearchRound
from agents.review import ReviewOutput
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick
import jobs.common as common
from jobs.common import (incumbent_strategy, last_closed_day, load_calendar, run_job,
                         setup_logging)
from notify.wecom import NullNotifier
from tests.jobfakes import FakeLLM

D = date(2026, 10, 16)


def test_run_job_success_marks_ok_and_skips_rerun():
    store, notifier, calls = Store(":memory:"), NullNotifier(), []
    assert run_job("settle", D, lambda: calls.append(1), store, notifier) is True
    assert store.job_done("settle", D)
    assert run_job("settle", D, lambda: calls.append(2), store, notifier) is True
    assert calls == [1]  # 已完成 -> 跳过


def test_run_job_force_reruns():
    store, notifier, calls = Store(":memory:"), NullNotifier(), []
    run_job("review", D, lambda: calls.append(1), store, notifier)
    assert run_job("review", D, lambda: calls.append(2), store, notifier, force=True)
    assert calls == [1, 2]


def test_run_job_error_marks_error_and_alerts():
    store, notifier = Store(":memory:"), NullNotifier()

    def boom():
        raise RuntimeError("数据源挂了")

    assert run_job("premarket_scan", D, boom, store, notifier) is False
    assert not store.job_done("premarket_scan", D)
    assert len(notifier.sent) == 1
    assert "premarket_scan" in str(notifier.sent[0])
    assert "数据源挂了" in str(notifier.sent[0])
    # 失败后可重试
    assert run_job("premarket_scan", D, lambda: None, store, notifier) is True
    assert store.job_done("premarket_scan", D)


def test_run_job_skips_while_another_process_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "LOCK_DIR", tmp_path / "locks")
    store, notifier, ran = Store(":memory:"), NullNotifier(), []
    with common.job_lock("settle") as got:
        assert got
        assert run_job("settle", D, lambda: ran.append(1), store, notifier) is False
        assert ran == [] and not store.job_done("settle", D) and notifier.sent == []
        assert run_job("settle", D, lambda: ran.append(2), store, notifier, lock=False) is True
    assert run_job("other", D, lambda: ran.append(3), store, notifier) is True
    assert ran == [2, 3]
    with common.job_lock("settle") as got:  # 释放后可以再拿到
        assert got


def test_job_lock_waits_for_release(tmp_path, monkeypatch):
    import threading
    import time as _t

    monkeypatch.setattr(common, "LOCK_DIR", tmp_path / "locks")
    released = threading.Event()

    def holder():
        with common.job_lock("settle_main"):
            released.wait(5)

    th = threading.Thread(target=holder)
    th.start()
    _t.sleep(0.2)
    with common.job_lock("settle_main", wait=0) as got:
        assert got is False
    threading.Timer(0.5, released.set).start()
    with common.job_lock("settle_main", wait=10) as got:  # 等到别的进程结算完
        assert got is True
    th.join()


def test_run_job_alerts_once_per_job_and_day(monkeypatch):
    store, notifier = Store(":memory:"), NullNotifier()
    monkeypatch.setattr(common, "today", lambda: D)
    for _ in range(3):  # 计划任务当天定时重试
        assert run_job("settle", D, lambda: 1 / 0, store, notifier) is False
    assert len(notifier.sent) == 1 and "定时重试" in notifier.sent[0]
    run_job("settle", date(2026, 10, 19), lambda: 1 / 0, store, notifier)
    assert len(notifier.sent) == 2
    monkeypatch.setattr(common, "today", lambda: date(2026, 10, 19))  # 第二天同一个结算日仍失败：再提醒
    run_job("settle", D, lambda: 1 / 0, store, notifier)
    assert len(notifier.sent) == 3


def test_run_job_alert_not_recorded_when_push_fails():
    class Flaky(NullNotifier):
        def __init__(self):
            super().__init__()
            self.ok = False

        def send_text(self, content):
            super().send_text(content)
            return self.ok

    store, notifier = Store(":memory:"), Flaky()
    run_job("settle", D, lambda: 1 / 0, store, notifier)
    notifier.ok = True
    run_job("settle", D, lambda: 1 / 0, store, notifier)  # 上次没推出去：这次再发
    run_job("settle", D, lambda: 1 / 0, store, notifier)
    assert len(notifier.sent) == 2


def test_run_job_survives_failure_while_recording_failure(caplog):
    class BrokenStore(Store):
        def mark_job(self, job, d, status):
            if status == "error":
                raise OSError("database is locked")
            super().mark_job(job, d, status)

    with caplog.at_level(logging.ERROR):
        assert run_job("x", D, lambda: 1 / 0, BrokenStore(":memory:"), NullNotifier()) is False
    assert "ZeroDivisionError" in caplog.text  # 原始异常已写进日志


def test_run_job_alert_failure_does_not_raise():
    store = Store(":memory:")

    class Broken:
        def send_text(self, content):
            raise OSError("网络断开")

    assert run_job("x", D, lambda: 1 / 0, store, Broken()) is False


class CalClient:
    def __init__(self):
        self.n = 0

    def trade_calendar(self):
        self.n += 1
        if self.n > 1:
            raise RuntimeError("down")
        return [date(2026, 10, 15), date(2026, 10, 16)]


def test_load_calendar_cached_per_day_and_falls_back():
    store, c = Store(":memory:"), CalClient()
    cal1 = load_calendar(store, c, now=D)
    cal2 = load_calendar(store, c, now=D)
    assert c.n == 1 and cal2.is_trading_day(D)
    cal3 = load_calendar(store, c, now=date(2026, 10, 17))  # 刷新失败 -> 旧缓存
    assert c.n == 2 and cal3.last() == D and cal1.last() == D


def test_incumbent_strategy_default_and_store():
    store = Store(":memory:")
    assert incumbent_strategy(store) == "baseline_momo"
    store.set_status("momo_v2", "incumbent", D)
    assert incumbent_strategy(store) == "momo_v2"


def test_last_closed_day():
    c = TradingCalendar([date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19)])
    assert last_closed_day(datetime.combine(D, time(15, 9)), c) == date(2026, 10, 15)
    assert last_closed_day(datetime.combine(D, time(15, 10)), c) == D
    assert last_closed_day(datetime(2026, 10, 18, 9, 0), c) == D  # 周日


def test_setup_logging_captures_agent_logs_once(tmp_path):
    base = logging.getLogger()
    before, level = list(base.handlers), base.level
    try:
        setup_logging(tmp_path)
        setup_logging(tmp_path)  # 重复调用不重复挂 handler
        added = [h for h in base.handlers if h not in before]
        assert len(added) == 1
        logging.getLogger("agents.premarket").warning("analyze:active 失败 LLMError")
        added[0].flush()
        text = (tmp_path / "logs" / "jobs.log").read_text(encoding="utf-8")
        assert "agents.premarket" in text and "LLMError" in text
    finally:
        for h in [h for h in base.handlers if h not in before]:
            base.removeHandler(h)
            h.close()
        base.setLevel(level)


def test_fake_llm_dispatch_and_scripted_failure():
    store = Store(":memory:")
    store.save_candidates(D, [Pick(code="600001", name="测试01", score=1.0, entry_max=10.3,
                                   stop=9.3, target=11.0, hold_days=5, reason="量化")], {})
    llm = FakeLLM(store, fail={"auction": LLMError("中转不可用")})
    llm.day = D
    plan = llm.parse("analyze:active", "s", "u", PremarketPlan, effort="high",
                     max_budget_usd=1.5, essential=True)
    assert [(p.code, p.action, p.entry_max) for p in plan.picks] == [("600001", "keep", 10.3)]
    assert llm.parse("review", "s", "u", ReviewOutput).lessons[0].evidence
    assert llm.parse("research", "s", "u", ResearchRound).done is True
    with pytest.raises(LLMError):
        llm.parse("auction:active", "s", "u", AuctionCheck)
    assert llm.purposes == ["analyze:active", "review", "research", "auction:active"]
    assert llm.calls[0]["max_budget_usd"] == 1.5 and llm.calls[0]["essential"] is True
