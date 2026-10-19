import threading
from datetime import datetime, time

import pytest

import agents.premarket as premarket
import agents.research as research_agent
import agents.review as review_agent
from jobs import common, premarket_final, premarket_scan, research, review
from engine.types import Pick
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, cal, install_context,
                            make_dates, make_index, make_panel, make_settings, new_store,
                            next_saturday)


@pytest.fixture
def env(tmp_path, monkeypatch):
    dates = make_dates(20)
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    cache, store, notifier = FakeCache(panel, NAMES, index), new_store(), NullNotifier()
    install_context(monkeypatch, make_settings(tmp_path), store, client, cache, cal(dates),
                    notifier)
    calls, analysis, analysis_kw = [], {"active": True, "candidate": True}, {}
    review_kw, review_fail = [], set()
    scan_picks = [Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=10.5, stop=9.5,
                       target=11.5, hold_days=5, reason="候选")]

    def fake_scan(d, settings, store, cache, client, strategy, calendar):
        calls.append(("scan", d, strategy.name))
        store.save_candidates(d, list(scan_picks), {})
        return list(scan_picks)

    def fake_analyze(d, settings, store, llm, deadline=None, now=None):
        calls.append(("analyze", d, llm))
        analysis_kw.update(deadline=deadline, now=now)
        return dict(analysis)

    def fake_final(d, settings, store, llm, client, notifier, calendar):
        calls.append(("final", d, llm))

    def fake_review(d, settings, store, llm, cache, notifier, calendar, latest=True, release=True):
        if d in review_fail:
            review_fail.discard(d)
            raise RuntimeError("复盘失败")
        calls.append(("review", d, llm))
        review_kw.append((d, latest, release))

    def fake_research(settings, store, notifier, llm=None):
        calls.append(("research", llm))
        return {"status": "ok"}

    monkeypatch.setattr(premarket, "scan", fake_scan)
    monkeypatch.setattr(premarket, "run_analyze", fake_analyze)
    monkeypatch.setattr(premarket, "run_final", fake_final)
    monkeypatch.setattr(review_agent, "run_review", fake_review)
    monkeypatch.setattr(research_agent, "run_research", fake_research)
    monkeypatch.setattr(common, "make_llm", lambda settings, store: "LLM")
    return dict(dates=dates, store=store, notifier=notifier, calls=calls, analysis=analysis,
                slept=[], analysis_kw=analysis_kw, scan_picks=scan_picks, review_kw=review_kw,
                review_fail=review_fail)


def at(d, hh, mm=0, ss=0):
    return datetime.combine(d, time(hh, mm, ss))


def test_scan_runs_scan_then_analyze_once(env):
    d = env["dates"][10]
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert premarket_scan.main(now=at(d, 8, 40)) is True  # 已完成 -> 跳过
    assert env["calls"] == [("scan", d, "baseline_momo"), ("analyze", d, "LLM")]
    assert env["notifier"].sent == []


def test_scan_alerts_when_analysis_incomplete_but_job_ok(env):
    d = env["dates"][10]
    env["analysis"]["candidate"] = False
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert env["store"].job_done("premarket_scan", d)
    [msg] = env["notifier"].sent
    assert "盘前分析未完成" in msg and "9:25 将按量化/规则推荐" in msg
    assert "candidate" in msg and "active" not in msg


def test_scan_without_candidates_alerts_once_about_the_strategy(env):
    d = env["dates"][10]
    env["scan_picks"].clear()
    env["analysis"].update(active=False, candidate=False)  # 没有候选时 run_analyze 不做分析
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    [msg] = env["notifier"].sent  # 只说没有候选，不说"盘前分析未完成"
    assert "盘前没有候选" in msg and "盘前分析未完成" not in msg


def test_scan_analyze_crash_counts_as_incomplete(env, monkeypatch):
    d = env["dates"][10]

    def crash(*a, **k):
        raise RuntimeError("找不到 claude.exe")

    monkeypatch.setattr(premarket, "run_analyze", crash)
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert env["store"].job_done("premarket_scan", d)
    [msg] = env["notifier"].sent
    assert "盘前分析未完成" in msg and "active、candidate" in msg


def test_scan_started_after_0925_skips_analyze(env):
    d = env["dates"][10]
    assert premarket_scan.main(now=at(d, 9, 28)) is True  # 开机补跑
    assert env["calls"] == [("scan", d, "baseline_momo")]
    assert env["notifier"].sent == []


def test_scan_passes_analysis_deadline(env):
    d = env["dates"][10]
    premarket_scan.main(now=at(d, 9, 15))
    kw = env["analysis_kw"]
    assert kw["deadline"] == at(d, 9, 22) and at(d, 9, 15) <= kw["now"] < at(d, 9, 16)


def test_scan_counts_context_time_toward_analysis_start(env, monkeypatch):
    d = env["dates"][10]
    clock = [1000.0]
    real = common.context

    def slow_context():
        clock[0] += 90  # 初始化依赖（连库、取交易日历）花了 90 秒
        return real()

    monkeypatch.setattr(common, "context", slow_context)
    monkeypatch.setattr(premarket_scan._time, "monotonic", lambda: clock[0])
    premarket_scan.main(now=at(d, 9, 15))
    assert env["analysis_kw"]["now"] == at(d, 9, 16, 30)


def test_final_does_not_rescan_while_analysis_runs(env):
    d = env["dates"][10]
    env["store"].save_candidates(d, [], {})  # 扫描已完成，深度分析还在跑：premarket_scan 尚未记 ok
    assert not env["store"].job_done("premarket_scan", d)
    assert premarket_final.main(now=at(d, 9, 25, 15), sleep=env["slept"].append) is True
    assert [c[0] for c in env["calls"]] == ["final"]


def test_scan_skips_non_trading_day(env):
    assert premarket_scan.main(now=at(next_saturday(env["dates"][5]), 8, 30)) is False
    assert env["calls"] == []


@pytest.mark.parametrize("hh, mm, ss, waited", [
    (9, 20, 0, [315.0]), (9, 25, 0, [15.0]), (9, 25, 15, []), (9, 30, 0, [])])
def test_final_waits_until_092515_then_runs(env, hh, mm, ss, waited):
    d = env["dates"][10]
    premarket_scan.main(now=at(d, 8, 30))
    assert premarket_final.main(now=at(d, hh, mm, ss), sleep=env["slept"].append) is True
    assert env["slept"] == pytest.approx(waited, abs=1.0)
    assert env["calls"] == [("scan", d, "baseline_momo"), ("analyze", d, "LLM"),
                            ("final", d, "LLM")]
    assert env["store"].job_done("premarket_final", d)


def test_final_runs_missing_scan_inline_without_analyze(env):
    d = env["dates"][10]
    assert premarket_final.main(now=at(d, 9, 30), sleep=env["slept"].append) is True
    assert [c[0] for c in env["calls"]] == ["scan", "final"]
    assert env["store"].job_done("premarket_scan", d)
    assert premarket_scan.main(now=at(d, 9, 31)) is True  # 已补跑 -> 跳过，不再分析
    assert [c[0] for c in env["calls"]] == ["scan", "final"]


def test_final_wait_subtracts_context_time(env, monkeypatch):
    d = env["dates"][10]
    env["store"].save_candidates(d, [], {})
    clock = [1000.0]
    real = common.context

    def slow_context():
        clock[0] += 10  # 装配依赖花了 10 秒
        return real()

    monkeypatch.setattr(common, "context", slow_context)
    monkeypatch.setattr(premarket_final._time, "monotonic", lambda: clock[0])
    assert premarket_final.main(now=at(d, 9, 25), sleep=env["slept"].append) is True
    assert env["slept"] == [5.0]


def test_final_inline_scan_has_a_deadline(env, monkeypatch):
    d = env["dates"][10]
    release = threading.Event()

    def slow_scan(*a, **k):
        release.wait(5)
        return []

    monkeypatch.setattr(premarket, "scan", slow_scan)
    monkeypatch.setattr(premarket_final, "SCAN_MIN_SECONDS", 0.2)
    try:
        assert premarket_final.main(now=at(d, 9, 27), sleep=env["slept"].append) is False
    finally:
        release.set()
    assert env["calls"] == [] and not env["store"].job_done("premarket_scan", d)
    assert any("没有完成" in str(m) for m in env["notifier"].sent)


def test_final_stale_after_slow_inline_scan(env, monkeypatch):
    d = env["dates"][10]
    clock = [1000.0]
    monkeypatch.setattr(premarket_final._time, "monotonic", lambda: clock[0])

    def slow_scan(d, settings, store, cache, client, strategy, calendar):
        clock[0] += 600  # 扫描用了 10 分钟
        store.save_candidates(d, [], {})
        return []

    monkeypatch.setattr(premarket, "scan", slow_scan)
    monkeypatch.setattr(premarket_final, "SCAN_HARD_DEADLINE", time(9, 59))
    assert premarket_final.main(now=at(d, 9, 26), sleep=env["slept"].append) is False
    assert env["calls"] == [] and any("已过时" in str(m) for m in env["notifier"].sent)


def test_final_stale_after_0935(env):
    d = env["dates"][10]
    assert premarket_final.main(now=at(d, 9, 36), sleep=env["slept"].append) is False
    assert env["calls"] == [] and env["slept"] == []
    assert not env["store"].job_done("premarket_final", d)
    assert premarket_final.main(now=at(d, 10, 0), sleep=env["slept"].append) is False  # 再次补跑：只提醒一次
    [msg] = env["notifier"].sent
    assert "盘前推荐未推送" in msg and "09:36" in msg


def test_final_skips_non_trading_day_without_waiting(env):
    sat = next_saturday(env["dates"][5])
    assert premarket_final.main(now=at(sat, 9, 25), sleep=env["slept"].append) is False
    assert env["calls"] == [] and env["slept"] == []


def test_final_not_run_when_scan_fails(env, monkeypatch):
    d = env["dates"][10]

    def boom(*a, **k):
        raise RuntimeError("数据源失败")

    monkeypatch.setattr(premarket, "scan", boom)
    assert premarket_final.main(now=at(d, 9, 26), sleep=env["slept"].append) is False
    assert env["calls"] == []
    assert any("数据源失败" in str(m) for m in env["notifier"].sent)


def test_final_reports_failed_inline_scan_even_after_0830_alert(env, monkeypatch):
    d = env["dates"][10]

    def boom(*a, **k):
        raise RuntimeError("数据源失败")

    monkeypatch.setattr(premarket, "scan", boom)
    assert premarket_scan.main(now=at(d, 8, 30)) is False
    assert len(env["notifier"].sent) == 1
    assert premarket_final.main(now=at(d, 9, 26), sleep=env["slept"].append) is False
    assert len(env["notifier"].sent) == 2 and "今日不出推荐" in env["notifier"].sent[-1]


def test_review_requires_settle_and_runs_once(env):
    d = env["dates"][10]
    assert review.main(now=at(d, 16, 5)) is False  # 未结算
    env["store"].mark_job("settle", d, "ok")
    assert review.main(now=at(d, 16, 5)) is True
    assert review.main(now=at(d, 16, 30)) is True  # 已复盘 -> 跳过
    assert env["calls"] == [("review", d, "LLM")]


def test_review_backfills_settled_days_without_review(env):
    dates = env["dates"]
    env["store"].mark_job("review", dates[6], "ok")
    for d in dates[7:11]:  # 结算暂停后一次补完 4 天
        env["store"].mark_job("settle", d, "ok")
    assert review.main(now=at(dates[10], 16, 5)) is True
    assert [c[1] for c in env["calls"]] == dates[8:11]  # 最多 3 天，按日期顺序


def test_review_backfill_decides_release_before_any_lesson(env, monkeypatch):
    import agents.playbook as pb

    dates, order = env["dates"], []
    for d in dates[8:11]:
        env["store"].mark_job("settle", d, "ok")
    monkeypatch.setattr(pb, "reset_window_if_degraded", lambda store, d: order.append(("check", d)) or False)
    monkeypatch.setattr(pb, "maybe_release", lambda store, d, cal: order.append(("release", d)) or "发布消息")
    assert review.main(now=at(dates[10], 16, 5)) is True
    assert order == [("check", dates[8]), ("check", dates[9]), ("release", dates[10])]
    assert env["review_kw"] == [(dates[8], False, None), (dates[9], False, None), (dates[10], True, "发布消息")]


def test_review_backfill_stops_at_failure_and_resumes(env):
    dates = env["dates"]
    for d in dates[8:11]:
        env["store"].mark_job("settle", d, "ok")
    env["review_fail"].add(dates[9])
    assert review.main(now=at(dates[10], 16, 5)) is False
    assert [c[1] for c in env["calls"]] == [dates[8]]  # 按日期顺序：失败就停
    assert review.main(now=at(dates[10], 16, 25)) is True  # 下次重试接着补
    assert [c[1] for c in env["calls"]] == dates[8:11]


def test_review_waits_when_started_together_with_settle(env, monkeypatch, tmp_path):
    d = env["dates"][10]
    monkeypatch.setattr(common, "LOCK_DIR", tmp_path / "locks")
    naps = []

    def nap(sec):  # 等待期间 Settle 进程完成了结算
        naps.append(sec)
        env["store"].mark_job("settle", d, "ok")

    assert review.main(now=at(d, 16, 5), sleep=nap) is True
    assert naps and [c[1] for c in env["calls"]] == [d]


def test_review_before_close_targets_previous_day(env):
    d = env["dates"][10]
    env["store"].mark_job("settle", env["dates"][9], "ok")
    assert review.main(now=at(d, 9, 0)) is True
    assert env["calls"] == [("review", env["dates"][9], "LLM")]


def test_review_on_weekend_reviews_friday(env):
    fri = env["dates"][9]  # 2026-01-05 起的第 10 个工作日是周五
    env["store"].mark_job("settle", fri, "ok")
    assert review.main(now=at(next_saturday(fri), 10)) is True
    assert env["calls"] == [("review", fri, "LLM")]


def test_research_runs_once_per_day_with_factory_llm(env):
    sat = next_saturday(env["dates"][5])
    assert research.main(now=at(sat, 10)) == {"status": "ok"}
    assert research.main(now=at(sat, 11)) is None  # 当天已跑过
    assert env["calls"] == [("research", "LLM")]


def test_review_retries_earlier_day_even_if_latest_done(env):
    dates = env["dates"]
    for d in dates[8:11]:
        env["store"].mark_job("settle", d, "ok")
    env["store"].mark_job("review", dates[8], "ok")
    env["store"].mark_job("review", dates[10], "ok")  # 最近一天已复盘，dates[9] 之前失败过
    assert review.main(now=at(dates[10], 16, 45)) is True
    assert [c[1] for c in env["calls"]] == [dates[9]]
