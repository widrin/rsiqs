import datetime as dt
import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from agents import playbook
from engine.calendar import TradingCalendar
from engine.store import Store

TODAY = dt.date(2026, 10, 16)


def L(action, text="", evidence="2026-10-15 600000 高开 6% 后回落 -4%", rule_id=""):
    return SimpleNamespace(action=action, rule_id=rule_id, text=text, evidence=evidence)


@pytest.fixture
def pbdir(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def cal():
    return TradingCalendar(pd.bdate_range("2026-08-03", "2026-12-31").date)


def test_load_missing_returns_empty_and_roundtrip(pbdir):
    pb = playbook.load("active")
    assert pb["version"] == 1 and pb["rules"] == []
    pb["rules"].append({"id": "r1", "text": "规则", "evidence": "证据", "created": "2026-10-01"})
    playbook.save("candidate", pb)
    assert json.loads((pbdir / "candidate.json").read_text(encoding="utf-8")) == pb
    assert playbook.load("candidate") == pb
    with pytest.raises(ValueError):
        playbook.load("other")


def test_render():
    assert "暂无" in playbook.render({"version": 1, "rules": []})
    text = playbook.render({"version": 3, "rules": [
        {"id": "r1", "text": "高开超过 5% 否决", "evidence": "e1", "created": "x"},
        {"id": "r2", "text": "竞价量能不足否决", "evidence": "e2", "created": "x"}]})
    assert text.splitlines()[0].startswith("1. [r1] 高开超过 5% 否决")
    assert text.splitlines()[1].startswith("2. [r2]")


def test_apply_add_remove_merge_and_evidence_required():
    pb = {"version": 1, "created": "2026-10-01", "rules": []}
    pb = playbook.apply_lessons(pb, [L("add", "规则A"), L("add", "规则B"), L("add", "无证据", evidence="  ")], TODAY)
    assert [r["id"] for r in pb["rules"]] == ["r1", "r2"]
    assert pb["rules"][0]["created"] == TODAY.isoformat()
    pb = playbook.apply_lessons(pb, [L("merge", "规则A+B 合并", rule_id="r1"), L("remove", rule_id="r2"),
                                     L("remove", rule_id="r99"), L("remove", rule_id="r1", evidence="")], TODAY)
    assert [(r["id"], r["text"]) for r in pb["rules"]] == [("r1", "规则A+B 合并")]
    pb = playbook.apply_lessons(pb, [L("add", "规则C")], TODAY)
    assert pb["rules"][-1]["id"] == "r2"  # 新 id = 现存最大编号 + 1


def test_apply_does_not_mutate_input():
    pb = {"version": 1, "created": "2026-10-01", "rules": []}
    playbook.apply_lessons(pb, [L("add", "规则A")], TODAY)
    assert pb["rules"] == []


def test_caps_drop_oldest():
    old = [{"id": f"r{i}", "text": f"旧规则{i}", "evidence": "e", "created": f"2026-09-{i:02d}"}
           for i in range(1, 31)]
    pb = playbook.apply_lessons({"version": 1, "created": "x", "rules": old}, [L("add", "新规则")], TODAY)
    assert len(pb["rules"]) == playbook.MAX_RULES
    assert "r1" not in [r["id"] for r in pb["rules"]]
    assert pb["rules"][-1]["text"] == "新规则"

    big = [L("add", "长" * playbook.MAX_RULE_CHARS, evidence="e") for _ in range(20)]  # 20 × 301 字 > 6000
    pb = playbook.apply_lessons({"version": 1, "created": "x", "rules": []}, big, TODAY)
    assert sum(len(r["text"]) + len(r["evidence"]) for r in pb["rules"]) <= playbook.MAX_CHARS
    assert [r["id"] for r in pb["rules"]] == [f"r{i}" for i in range(2, 21)]


def _rules(*texts):
    return [{"id": f"r{i}", "text": t, "evidence": "e", "created": "2026-09-01"} for i, t in enumerate(texts, 1)]


def _setup_release(pbdir, cal, cand_end, act_end, start=dt.date(2026, 9, 1)):
    store = Store(":memory:")
    playbook.save("active", {"version": 2, "created": "2026-09-01", "rules": _rules("旧")})
    playbook.save("candidate", {"version": 2, "created": "2026-09-01", "rules": _rules("旧", "新")})
    store.kv_set("playbook_released", start.isoformat())
    days = cal.between(start, TODAY)
    for i, d in enumerate(days):
        frac = i / (len(days) - 1)
        store.record_nav("h:candidate", d, 500_000 * (1 + (cand_end - 1) * frac))
        store.record_nav("h:active", d, 500_000 * (1 + (act_end - 1) * frac))
    return store


def test_maybe_release_first_call_starts_window(pbdir, cal):
    store = Store(":memory:")
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert store.kv_get("playbook_released") == TODAY.isoformat()


@pytest.mark.parametrize("gap,released", [(playbook.MIN_DAYS_BETWEEN_RELEASES - 1, False),
                                          (playbook.MIN_DAYS_BETWEEN_RELEASES, True)])
def test_maybe_release_window_boundary(pbdir, cal, gap, released):
    start = cal.between(dt.date(2026, 8, 3), TODAY)[-(gap + 1)]  # 从 start 到 TODAY 相隔 gap 个交易日
    store = _setup_release(pbdir, cal, 1.05, 1.01, start=start)
    assert bool(playbook.maybe_release(store, TODAY, cal)) is released


def test_save_is_atomic_and_load_survives_corruption(pbdir):
    playbook.save("candidate", {"version": 3, "created": "x", "rules": _rules("a")})
    assert not list(pbdir.glob("*.tmp"))
    (pbdir / "candidate.json").write_text('{"version": 1, "rules": [', encoding="utf-8")
    pb = playbook.load("candidate")  # 损坏时退回空 playbook，不让盘前和复盘整体失败
    assert pb["rules"] == []


@pytest.mark.parametrize("text", [
    '{"version": "v3", "rules": []}',
    '{"version": 2, "rules": [{"id": "r1", "text": 5, "evidence": "e"}]}',
    '{"version": 2, "rules": ["规则"]}',
    '{"version": true, "rules": []}',
    '[]',
])
def test_load_checked_flags_malformed_playbook(pbdir, text):
    (pbdir / "active.json").write_text(text, encoding="utf-8")
    pb, ok = playbook.load_checked("active")
    assert not ok and pb["rules"] == []
    assert playbook.corrupt_kinds() == ["active"]


def test_load_checked_missing_file_is_not_corrupt(pbdir):
    assert playbook.load_checked("candidate")[1] is True and playbook.corrupt_kinds() == []


@pytest.mark.parametrize("broken", ["active", "candidate"])
def test_maybe_release_skips_when_a_playbook_is_corrupt(pbdir, cal, broken):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    other = "candidate" if broken == "active" else "active"
    before = (pbdir / f"{other}.json").read_text(encoding="utf-8")
    (pbdir / f"{broken}.json").write_text('{"version": 2, "rules": [', encoding="utf-8")
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert (pbdir / f"{other}.json").read_text(encoding="utf-8") == before  # 空规则没有写进另一份
    # 损坏期间盘前按空规则交易：窗口从今天重新开始，修好后不拿这段净值比较两版规则
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def test_maybe_release_after_repair_ignores_corrupt_days(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    good = (pbdir / "candidate.json").read_text(encoding="utf-8")
    (pbdir / "candidate.json").write_text('{"version": 2, "rules": [', encoding="utf-8")
    playbook.maybe_release(store, TODAY - dt.timedelta(days=1), cal)  # 昨天的复盘发现损坏
    (pbdir / "candidate.json").write_text(good, encoding="utf-8")       # 今天修好
    assert playbook.maybe_release(store, TODAY, cal) is None             # 新窗口还不满 10 个交易日
    assert playbook.load("active")["version"] == 2


def test_maybe_release_resets_when_premarket_ran_without_playbook(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    # 盘前 playbook 读不出来（按空规则运行），复盘前已经修好：文件完好，但这一天的净值不能用来比较
    store.save_recommendation(TODAY, "h:candidate", [], {"llm_reviewed": True, "playbook_version": None})
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert store.kv_get("playbook_released") == TODAY.isoformat()
    assert playbook.load("active")["version"] == 2


def test_load_checked_accepts_bom(pbdir):
    (pbdir / "active.json").write_text('\ufeff{"version": 3, "rules": [{"id": "r1", "text": "t", "evidence": "e"}]}',
                                       encoding="utf-8")
    pb, ok = playbook.load_checked("active")
    assert ok and pb["version"] == 3


def test_long_lesson_is_truncated_not_wiping_rules():
    pb = {"version": 1, "rules": [{"id": f"r{i}", "text": "旧" * 10, "evidence": "e", "created": "x"} for i in range(10)]}
    out = playbook.apply_lessons(pb, [L("add", text="长" * 6001)], TODAY)
    assert len(out["rules"]) == 11 and len(out["rules"][-1]["text"]) == playbook.MAX_RULE_CHARS


def test_maybe_release_too_early(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.10, 1.00, start=dt.date(2026, 10, 12))
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert playbook.load("active")["version"] == 2


def test_maybe_release_promotes(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    msg = playbook.maybe_release(store, TODAY, cal)
    assert msg and "v3" in msg
    act, cand = playbook.load("active"), playbook.load("candidate")
    assert act["version"] == 3 and [r["text"] for r in act["rules"]] == ["旧", "新"]
    assert cand == act
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def test_maybe_release_resets_when_worse(pbdir, cal):
    store = _setup_release(pbdir, cal, 0.98, 1.02)
    msg = playbook.maybe_release(store, TODAY, cal)
    assert msg and "重置" in msg
    act, cand = playbook.load("active"), playbook.load("candidate")
    assert act["version"] == 2 and cand == act
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def test_maybe_release_identical_rules_only_restarts_window(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    playbook.save("candidate", playbook.load("active"))
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert playbook.load("active")["version"] == 2
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def test_git_commit_in_tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "ROOT", tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "tester")
    (tmp_path / "playbook").mkdir()
    (tmp_path / "playbook" / "candidate.json").write_text("{}", encoding="utf-8")
    assert playbook.git_commit(["playbook/candidate.json"], "playbook: 复盘更新") is True
    log = _git(tmp_path, "log", "-1", "--format=%B")
    assert log.startswith("playbook: 复盘更新")
    assert "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" in log
    assert playbook.git_commit(["playbook/candidate.json"], "nothing changed") is False


def test_git_commit_timeout_kills_process_tree_and_skips_hooks(monkeypatch):
    started, killed = [], []

    class HangingGit:
        pid = 4242

        def __init__(self, args, **kw):
            started.append((args, kw))

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("git", timeout)
            return -9

    monkeypatch.setattr(playbook.subprocess, "Popen", HangingGit)
    monkeypatch.setattr(playbook, "_kill_tree", lambda proc: killed.append(proc.pid))
    assert playbook.git_commit(["playbook/active.json"], "m") is False  # 超时不抛异常
    args, kw = started[0]
    assert "commit.gpgsign=false" in args and any(a.startswith("core.hooksPath=") for a in args)
    assert kw["stdout"] is not subprocess.PIPE and killed == [4242]  # 输出不走管道；超时后结束整个进程树


def test_git_commit_skips_hooks_in_real_repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")  # 会让提交失败的钩子
    monkeypatch.setattr(playbook, "ROOT", tmp_path)
    monkeypatch.setattr(playbook, "NO_HOOKS", tmp_path / ".no-hooks")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    assert playbook.git_commit(["a.json"], "playbook: 测试") is True


def test_git_commit_outside_repo_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "ROOT", tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    assert playbook.git_commit(["x.txt"], "msg") is False


def test_prompt_files_exist():
    for name in ("premarket", "review"):
        text = playbook.load_prompt(name)
        assert len(text) > 500 and "证据" in text
