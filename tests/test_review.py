import datetime as dt
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agents import playbook, review
from agents.llm import LLM, BudgetExceeded, LLMError
from agents.review import Lesson, ReviewOutput
from config import Settings
from engine.calendar import TradingCalendar
from engine.ledger import Account
from engine.store import Store
from engine.types import Fill, Pick

D = dt.date(2026, 10, 16)
PREV = dt.date(2026, 10, 15)
CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)
ACCOUNTS = ["q:baseline_momo", "h:active", "h:candidate", "bench:csi300"]


def settings() -> Settings:
    return Settings(anthropic_api_key="k", wecom_webhook_url="", root=Path("."))


def pick(code):
    return Pick(code=code, name=f"股{code}", score=0.8, entry_max=10.3, stop=9.6, target=11.2, hold_days=5,
                reason=f"推荐理由{code}")


class FakeCache:
    def __init__(self):
        self.requested = None

    def load_panel(self, codes, start, end):
        self.requested = (sorted(codes), start, end)
        idx = pd.DatetimeIndex([pd.Timestamp(PREV), pd.Timestamp(D)])
        df = pd.DataFrame({"open": [10.0, 10.62], "high": [10.2, 10.8], "low": [9.9, 10.1], "close": [10.0, 10.15],
                           "volume": [1e6, 2e6], "amount": [1e7, 2e7], "prev_close": [9.9, 10.0]}, index=idx)
        return {c: df.copy() for c in codes}, {c: f"股{c}" for c in codes}


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0, max_budget_usd=1.0,
              essential=False):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, max_budget_usd=max_budget_usd, essential=essential))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Recorder:
    def __init__(self):
        self.sent = []

    def send_markdown(self, content):
        self.sent.append(content)
        return True

    def send_text(self, content):
        self.sent.append(content)
        return True


REAL_RELEASE = playbook.maybe_release


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", tmp_path)
    (tmp_path / "review_system.md").write_text("复盘系统提示", encoding="utf-8")
    playbook.save("active", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "旧规则", "evidence": "e", "created": "2026-09-01"}]})
    playbook.save("candidate", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "旧规则", "evidence": "e", "created": "2026-09-01"}]})
    commits, releases = [], []
    monkeypatch.setattr(playbook, "git_commit", lambda paths, message: commits.append((paths, message)) or True)

    def fake_release(store, today, calendar):
        releases.append(today)
        return None

    monkeypatch.setattr(playbook, "maybe_release", fake_release)

    store = Store(":memory:")
    for i, name in enumerate(ACCOUNTS):
        acc = Account(name=name, cash=500_000.0)
        if name == "h:active":
            acc.trades = [Fill(date=PREV, code="600009", side="BUY", qty=100, price=9.0, fee=5.0, reason=""),
                          Fill(date=D, code="600001", side="BUY", qty=1000, price=10.63, fee=5.0, reason=""),
                          Fill(date=D, code="600009", side="SELL", qty=100, price=9.5, fee=5.5, reason="止盈")]
        store.save_account(acc)
        store.record_nav(name, PREV, 500_000.0)
        store.record_nav(name, D, 500_000.0 * (1 + 0.01 * (i + 1)))
    store.save_recommendation(D, "h:active", [pick("600001"), pick("600002")], {"llm_reviewed": True})
    store.save_recommendation(D, "h:candidate", [pick("600003")], {"llm_reviewed": False})
    return dict(store=store, cache=FakeCache(), notifier=Recorder(), commits=commits, releases=releases)


OUTPUT = ReviewOutput(summary="今日 600001 高开 6% 买入后回落。", lessons=[
    Lesson(action="add", rule_id="", text="主板竞价高开超过 5% 的候选否决",
           evidence="2026-10-16 600001 高开 6.2% 买入，收盘 -4.4%"),
    Lesson(action="add", rule_id="", text="无证据规则", evidence=""),
])


def run(env, llm):
    review.run_review(D, settings(), env["store"], llm, env["cache"], env["notifier"], CAL)


def test_review_applies_lessons_to_candidate_only(env):
    llm = FakeLLM(OUTPUT)
    run(env, llm)
    call = llm.calls[0]
    assert call["purpose"] == "review" and call["essential"] is False and call["effort"] == "medium"
    assert call["timeout"] == review.REVIEW_TIMEOUT and call["max_budget_usd"] == review.REVIEW_BUDGET_USD
    assert call["schema"] is ReviewOutput and call["system"] == "复盘系统提示"
    user = call["user"]
    for s in ("600001", "600002", "600003", "10.62", "BUY", "止盈", "旧规则", "未经 LLM 审核"):
        assert s in user, s
    assert "@ 9.00" not in user.split("### 当日成交")[1].split("###")[0]  # 前一日的买入不算当日成交
    cand, act = playbook.load("candidate"), playbook.load("active")
    assert [r["text"] for r in cand["rules"]] == ["旧规则", "主板竞价高开超过 5% 的候选否决"]
    assert [r["text"] for r in act["rules"]] == ["旧规则"]
    assert env["releases"] == [D]
    assert env["commits"] == [(["playbook/active.json", "playbook/candidate.json"],
                               "playbook: 2026-10-16 复盘更新 1 条")]
    assert env["cache"].requested[0] == ["600001", "600002", "600003", "600009"]
    assert env["cache"].requested[2] == D


def test_review_pushes_report_for_all_accounts(env):
    run(env, FakeLLM(OUTPUT))
    assert len(env["notifier"].sent) == 1
    report = env["notifier"].sent[0]
    for name in ACCOUNTS:
        assert name in report
    assert "今日 600001 高开 6% 买入后回落。" in report
    assert "+4.00%" in report  # bench:csi300 当日 +4%


def test_review_caps_lessons(env):
    many = ReviewOutput(summary="s", lessons=[
        Lesson(action="add", rule_id="", text=f"规则{i}", evidence=f"证据{i}") for i in range(5)])
    run(env, FakeLLM(many))
    assert len(playbook.load("candidate")["rules"]) == 1 + review.MAX_LESSONS


def test_review_budget_exceeded_still_pushes_report(env):
    run(env, FakeLLM(BudgetExceeded("over")))
    assert len(env["notifier"].sent) == 1
    assert "预算" in env["notifier"].sent[0] and "h:active" in env["notifier"].sent[0]
    assert playbook.load("candidate")["rules"] == playbook.load("active")["rules"]
    assert env["commits"] == []
    assert env["releases"] == [D]  # 发布判断不依赖 LLM


@pytest.mark.parametrize("exc", [LLMError("review: error_during_execution"),
                                 subprocess.TimeoutExpired(["claude.exe"], 300.0), ValueError("bad")])
def test_review_llm_failure_still_pushes_report(env, exc):
    run(env, FakeLLM(exc))
    assert len(env["notifier"].sent) == 1 and "复盘失败" in env["notifier"].sent[0]


def test_review_release_message_is_pushed_and_committed(env, monkeypatch):
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: "playbook v2 上线：测试")
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "playbook v2 上线：测试" in env["notifier"].sent[0]
    assert env["commits"] == [(["playbook/active.json", "playbook/candidate.json"],
                               "playbook: 2026-10-16 playbook v2 上线：测试")]


def test_release_message_comes_first(env, monkeypatch):
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: "playbook v2 上线：测试")
    run(env, FakeLLM(OUTPUT))
    assert env["commits"][-1][1] == "playbook: 2026-10-16 playbook v2 上线：测试；复盘更新 1 条"


def test_lessons_apply_to_released_candidate(env, monkeypatch, tmp_path):
    """用真的 maybe_release：发布后的候选版（v2）再写当天的 lesson，今天新写的规则不上线。"""
    monkeypatch.setattr(playbook, "maybe_release", REAL_RELEASE)
    store = env["store"]
    rules = [{"id": "r1", "text": "旧规则", "evidence": "e", "created": "2026-09-01"},
             {"id": "r2", "text": "候选规则", "evidence": "e", "created": "2026-09-20"}]
    playbook.save("candidate", {"version": 1, "created": "x", "rules": rules})
    start = CAL.between(dt.date(2026, 9, 1), D)[-(playbook.MIN_DAYS_BETWEEN_RELEASES + 1)]
    store.kv_set(playbook.RELEASE_KEY, start.isoformat())
    for name, end in (("h:candidate", 1.05), ("h:active", 1.01)):
        store.record_nav(name, start, 500_000.0)
        store.record_nav(name, PREV, 500_000.0 * end)
        store.record_nav(name, D, 500_000.0 * end)
    llm = FakeLLM(OUTPUT)
    run(env, llm)
    act, cand = playbook.load("active"), playbook.load("candidate")
    assert act["version"] == 2 and [r["text"] for r in act["rules"]] == ["旧规则", "候选规则"]
    assert cand["version"] == 2 and [r["text"] for r in cand["rules"]] == ["旧规则", "候选规则",
                                                                          "主板竞价高开超过 5% 的候选否决"]
    assert "playbook v2" in llm.calls[0]["user"] and "候选规则" in llm.calls[0]["user"]
    assert env["commits"][-1][1].startswith("playbook: 2026-10-16 playbook v2 上线")


@pytest.mark.parametrize("broken", ["active", "candidate"])
def test_corrupt_playbook_is_not_released_or_overwritten(env, monkeypatch, tmp_path, broken):
    monkeypatch.setattr(playbook, "maybe_release", REAL_RELEASE)
    (tmp_path / f"{broken}.json").write_text('{"version": 1, "rules": [', encoding="utf-8")
    other = "candidate" if broken == "active" else "active"
    before = (tmp_path / f"{other}.json").read_text(encoding="utf-8")
    env["store"].kv_set(playbook.RELEASE_KEY, "2026-09-01")
    run(env, FakeLLM(OUTPUT))
    assert (tmp_path / f"{broken}.json").read_text(encoding="utf-8") == '{"version": 1, "rules": ['
    assert (tmp_path / f"{other}.json").read_text(encoding="utf-8") == before
    assert env["commits"] == []
    assert f"playbook/{broken}.json 损坏" in env["notifier"].sent[0]
    assert env["store"].kv_get(playbook.RELEASE_KEY) == D.isoformat()  # 发布窗口从今天重新开始


def test_evidence_only_merge_is_committed(env):
    out = ReviewOutput(summary="s", lessons=[Lesson(action="merge", rule_id="r1", text="旧规则",
                                                    evidence="2026-10-16 600001 再次验证")])
    run(env, FakeLLM(out))
    assert playbook.load("candidate")["rules"][0]["evidence"] != "e"
    assert env["commits"][-1][1] == "playbook: 2026-10-16 补充证据 1 条"


def test_release_happens_before_todays_lessons(env, monkeypatch):
    order = []
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: order.append("release"))
    real = playbook.apply_lessons
    monkeypatch.setattr(playbook, "apply_lessons", lambda *a, **k: order.append("lessons") or real(*a, **k))
    run(env, FakeLLM(OUTPUT))
    assert order == ["release", "lessons"]  # 当天新写的规则没有前向模拟过，不能随这次发布上线


def test_commit_counts_only_effective_changes(env):
    from agents.review import Lesson
    out = ReviewOutput(summary="s", lessons=[Lesson(action="add", rule_id="", text="新", evidence="2026-10-16 600001"),
                                             Lesson(action="remove", rule_id="r99", text="", evidence="e")])
    run(env, FakeLLM(out))
    assert env["commits"][-1][1] == "playbook: 2026-10-16 复盘更新 1 条"


def test_backfilled_day_only_sends_the_report(env, monkeypatch):
    released = []
    monkeypatch.setattr(playbook, "maybe_release", lambda *a: released.append(a) or "不该发布")
    llm = FakeLLM(OUTPUT)
    before = playbook.load("candidate")
    review.run_review(PREV, settings(), env["store"], llm, env["cache"], env["notifier"], CAL, backfill=True)
    assert llm.calls == [] and released == [] and env["commits"] == []  # 不调用 LLM、不发布、不写 lesson
    assert playbook.load("candidate") == before
    [report] = env["notifier"].sent
    assert "回补的日报" in report and "2026-10-15" in report


def test_backfilled_day_resets_window_when_premarket_ran_without_playbook(env):
    env["store"].save_recommendation(PREV, "h:candidate", [], {"llm_reviewed": True, "playbook_version": None})
    env["store"].kv_set(playbook.RELEASE_KEY, "2026-09-01")
    review.run_review(PREV, settings(), env["store"], FakeLLM(OUTPUT), env["cache"], env["notifier"], CAL,
                      backfill=True)
    assert env["store"].kv_get(playbook.RELEASE_KEY) == PREV.isoformat()


def test_no_change_commit_is_not_reported_as_failure(env, monkeypatch):
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: "playbook v2 上线：测试")
    monkeypatch.setattr(playbook, "git_commit", lambda paths, message: None)  # 没有变化
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "git 提交失败" not in env["notifier"].sent[0]


def test_git_failure_is_noted_in_report(env, monkeypatch):
    monkeypatch.setattr(playbook, "git_commit", lambda paths, message: False)
    run(env, FakeLLM(OUTPUT))
    assert "git 提交失败" in env["notifier"].sent[0]


def test_retired_quant_account_not_in_report(env):
    env["store"].set_status("baseline_momo", "retired", D)
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "q:baseline_momo" not in env["notifier"].sent[0]


def test_report_marks_accounts_without_todays_nav(env):
    env["store"].record_nav("q:late", PREV, 500_000.0)
    env["store"].save_account(Account(name="q:late", cash=500_000.0))
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "q:late（今日未结算）" in env["notifier"].sent[0]


def test_review_without_recommendations_skips_llm(env):
    store = Store(":memory:")
    for name in ACCOUNTS:
        store.save_account(Account(name=name, cash=500_000.0))
        store.record_nav(name, D, 500_000.0)
    store.save_account(Account(name="q:no_nav", cash=1.0))  # 无净值记录 -> 不进日报
    llm = FakeLLM(OUTPUT)
    review.run_review(D, settings(), store, llm, env["cache"], env["notifier"], CAL)
    assert llm.calls == [] and env["cache"].requested is None
    report = env["notifier"].sent[0]
    assert "无推荐" in report and "q:no_nav" not in report


def test_review_through_real_llm_wrapper(env, tmp_path):
    """真实 LLM 封装 + 假 claude.exe 子进程：run_review 传给 parse 的参数必须被真实签名接受。"""
    exe = tmp_path / "bin" / "claude.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    calls = []

    def runner(args, **kw):
        calls.append((args, kw))
        out = {"type": "result", "subtype": "success", "is_error": False, "result": "",
               "structured_output": OUTPUT.model_dump(), "total_cost_usd": 0.05,
               "usage": {"input_tokens": 900, "output_tokens": 300}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out).encode("utf-8"), stderr=b"")

    s = Settings(anthropic_api_key="k", wecom_webhook_url="", root=tmp_path, claude_exe=str(exe))
    review.run_review(D, s, env["store"], LLM(s, env["store"], runner=runner), env["cache"], env["notifier"], CAL)
    args, kw = calls[0]
    assert args[args.index("--effort") + 1] == "medium"
    assert args[args.index("--max-budget-usd") + 1] == f"{review.REVIEW_BUDGET_USD:.2f}"
    assert kw["timeout"] == review.REVIEW_TIMEOUT and "600001" in kw["input"].decode("utf-8")
    assert [r["text"] for r in playbook.load("candidate")["rules"]] == ["旧规则", "主板竞价高开超过 5% 的候选否决"]
    assert "今日 600001 高开 6% 买入后回落。" in env["notifier"].sent[0]
