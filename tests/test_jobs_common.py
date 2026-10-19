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
