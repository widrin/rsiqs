# tests/test_doctor.py
import logging
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import jobs.doctor as doctor
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.integrity import write_manifest
from engine.store import Store
from jobs.doctor import Diagnosis, failed_jobs, health_check, integrity_issues, main, research_idle, source_health

NOW = datetime(2026, 1, 8, 7, 30)  # 1-08 盘前：锚定日应为 1-07
DAYS = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]
ANCHOR = date(2026, 1, 7)


class FakeClient:
    def __init__(self, data=None):
        self.data = data or {}

    def daily_bars(self, code, start, end):
        df = self.data.get(code)
        if df is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
        m = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
        return df[m].reset_index(drop=True)

    def index_daily(self, symbol="sh000300"):
        return pd.Series([4000.0, 4010.0], index=pd.DatetimeIndex(["2026-01-06", ANCHOR]), name=symbol)


class FakeLLM:
    def __init__(self):
        self.calls = []

    def parse(self, purpose, system, user, schema, timeout=120.0):
        self.calls.append(purpose)
        return schema(summary="s", root_cause="r", suggested_fix="f", severity="warn")


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(text)
        return True

    def replay_failed(self):
        return 0


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    client = FakeClient()
    cache = BarCache(tmp_path / "t.sqlite", client)
    calendar = TradingCalendar(DAYS)
    notifier = FakeNotifier()
    settings = SimpleNamespace(root=tmp_path)
    return SimpleNamespace(store=store, client=client, cache=cache, calendar=calendar,
                           notifier=notifier, settings=settings, root=tmp_path)


def add_llm_usage(store, purpose="research", d="2026-01-07"):
    store.conn.execute("INSERT INTO llm_usage (ts, purpose, input_tokens, output_tokens, cost_cny) "
                       "VALUES (?, ?, 1, 1, 0.0)", (f"{d} 08:00:00", purpose))
    store.conn.commit()


def test_failed_jobs_groups_and_counts(env):
    for job, d in (("settle", date(2026, 1, 7)), ("settle", date(2026, 1, 8)), ("review", date(2026, 1, 8))):
        env.store.mark_job(job, d, "error")
    assert failed_jobs(env.store, today=NOW.date()) == ["review × 1（2026-01-08）", "settle × 2（2026-01-07, 2026-01-08）"]
    # days=1：since = 今天-1，含 01-07 的失败
    assert failed_jobs(env.store, days=1, today=NOW.date()) == ["review × 1（2026-01-08）", "settle × 2（2026-01-07, 2026-01-08）"]


def test_missing_bars_and_repair(env):
    env.store.kv_set("universe", ["600000", "000001"])
    env.client.data["600000"] = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-06", ANCHOR.isoformat()]),
        "open": [10.0, 10.2], "high": [10.5, 10.6], "low": [9.9, 10.0],
        "close": [10.2, 10.4], "volume": [1000, 1100], "amount": [1e6, 1.1e6]})
    missing = doctor.missing_bars(env.cache, ["600000", "000001"], ANCHOR)
    assert missing == ["600000", "000001"]  # 600000 尚未回填、000001 数据源无数据
    repaired, still = doctor.repair_data(env.cache, env.client, missing, ANCHOR)
    assert repaired == ["600000"] and still == ["000001"]  # 000001 回填成功但源里没数据：留在"仍缺"


def test_repair_index_only_when_gap(env):
    assert doctor.repair_index(env.cache, env.client, ANCHOR) is True
    assert doctor.repair_index(env.cache, env.client, ANCHOR) is False  # 已补齐


def test_research_idle_variants(env):
    assert "从未成功运行" in research_idle(env.store, today=NOW.date())
    env.store.mark_job("research", ANCHOR, "ok")
    assert "空转" in research_idle(env.store, today=NOW.date())  # ok 但没有 LLM 记录
    add_llm_usage(env.store)
    assert research_idle(env.store, today=NOW.date()) is None


def test_source_health_scans_log_tail(tmp_path):
    log = tmp_path / "jobs.log"
    log.write_text(
        "2026-01-06 09:00:00 WARNING data.akshare_client 东财日线失败(600000)，切换新浪: x\n"
        "2026-01-07 09:00:00 WARNING data.akshare_client stock_zh_a_hist 第 1/3 次失败: 被临时限流。\n"
        "2026-01-07 09:00:01 WARNING data.akshare_client 东财日线失败(000001)，切换新浪: x\n", encoding="utf-8")
    assert source_health(log, today=date(2026, 1, 7)) == "近 24h 东财限流告警 1 次，切换新浪 2 次"
    assert source_health(log, today=date(2026, 1, 9)) is None  # 两天前的告警不算
    assert source_health(tmp_path / "none.log") is None


def test_integrity_issues(env):
    assert "缺失" in integrity_issues(env.root)[0]
    (env.root / "engine").mkdir()
    (env.root / "engine" / "store.py").write_text("x = 1\n", encoding="utf-8")
    write_manifest(env.root)
    assert integrity_issues(env.root) == []
    (env.root / "engine" / "store.py").write_text("x = 2\n", encoding="utf-8")
    assert "不符" in integrity_issues(env.root)[0]


def test_health_check_repairs_and_reports(env):
    env.store.kv_set("universe", ["600000"])
    env.client.data["600000"] = pd.DataFrame({
        "date": pd.to_datetime([ANCHOR.isoformat()]),
        "open": [10.2], "high": [10.6], "low": [10.0], "close": [10.4],
        "volume": [1100], "amount": [1.1e6]})
    env.store.mark_job("settle", ANCHOR, "error")
    report = health_check(env.settings, env.store, env.client, env.cache, env.calendar, NOW)
    assert report["anchor"] == ANCHOR
    assert report["repaired"] == [f"回补 1 只股票 {ANCHOR} 的 K 线", f"补齐基准指数 sh000300 至 {ANCHOR}"]
    assert any("任务失败" in i for i in report["issues"])
    assert any("完整性清单缺失" in i for i in report["issues"])  # 未生成清单 → 完整性问题


def test_main_pushes_brief_or_alert(env, monkeypatch):
    monkeypatch.setattr(doctor.common, "make_llm", lambda settings, store: FakeLLM())
    monkeypatch.setattr(doctor.common, "context", lambda: (env.settings, env.store, env.client,
                                                           env.cache, env.calendar, env.notifier))
    # run_job 会用 common.log 记 "job ok"：换成无 handler 的 logger，避免测试日志进生产 jobs.log
    monkeypatch.setattr(doctor.common, "log", logging.getLogger("test_doctor"))
    report_ok = {"anchor": ANCHOR, "issues": [], "repaired": ["补齐基准指数"], "notes": []}
    monkeypatch.setattr(doctor, "health_check", lambda *a, **k: report_ok)
    assert main(NOW) is True
    assert env.notifier.sent and env.notifier.sent[0].startswith("【RSIQS 体检】正常")

    env.notifier.sent.clear()
    report_bad = {"anchor": ANCHOR, "issues": ["任务失败：settle × 2（2026-01-07, 2026-01-08）"],
                  "repaired": [], "notes": []}
    monkeypatch.setattr(doctor, "health_check", lambda *a, **k: report_bad)
    assert main(datetime(2026, 1, 9, 7, 30)) is True  # 换一天：job_done 按 (job, d) 幂等
    alert = env.notifier.sent[0]
    assert "告警" in alert and "诊断（warn）" in alert and "根因假设" in alert
