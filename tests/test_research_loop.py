import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import research
from agents.llm import BudgetExceeded, LLMError
from agents.research import RESEARCH_SYSTEM, Draft, ResearchRound, run_research
from config import Settings
from engine import integrity
from engine.store import Store
from notify.wecom import NullNotifier
from strategies.sandbox import ALLOWED_IMPORTS

PROJECT = Path(__file__).resolve().parents[1]
SOURCES = ["strategies/base.py", "strategies/baseline_momo.py",
           "engine/types.py", "engine/dataview.py", "engine/rules.py"]
# 提示词里不能出现的措辞（会触发分类器，导致调用失败）
BANNED_WORDS = ["安全", "沙箱", "绕过", "权限", "漏洞", "攻击", "注入", "逃逸", "恶意",
                "sandbox", "bypass", "security", "permission", "exploit", "jailbreak", "escape", "malicious",
                "attack", "inject", "vulnerab"]


def src(name: str, extra: str = "") -> str:
    return f'''"""假设：{name} 的测试假设。"""
{extra}from engine.dataview import DataView
from engine.types import Pick


class S:
    name = "{name}"

    def select(self, view: DataView) -> list[Pick]:
        return []


STRATEGY = S()
'''


def d(name: str, extra: str = "") -> Draft:
    return Draft(name=name, hypothesis=f"{name} 的假设：动量在高换手股票上更强", code=src(name, extra))


def rnd(*drafts, done=False, analysis="分析") -> ResearchRound:
    return ResearchRound(analysis=analysis, drafts=list(drafts), done=done)


def report(name, gate=False, holdout=None):
    return {"strategy": name, "train": {"sharpe": 0.61, "excess_ann": 0.021},
            "val": {"sharpe": 0.95, "excess_ann": 0.043, "max_dd": -0.18},
            "dsr": 0.912345678, "gate_pass": gate, "holdout_pass": holdout, "n_trials": 7}


class FakeLLM:
    """按顺序返回预设结果（ResearchRound 或异常）；像真 LLM 一样先查预算，成功后记账。"""

    def __init__(self, settings, store, script, round_cost=0.5):
        self.settings, self.store = settings, store
        self.script = list(script)
        self.round_cost = round_cost
        self.calls: list[dict] = []

    def check_budget(self, essential):
        t = date.today()
        if not essential and self.store.month_llm_cost(t.year, t.month) >= self.settings.monthly_budget_cny:
            raise BudgetExceeded("本月 LLM 预算已用完")

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0,
              max_budget_usd=1.0, essential=False):
        assert max_budget_usd > 0, "单次预算必须为正"
        self.check_budget(essential)
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, max_budget_usd=max_budget_usd, essential=essential))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        self.store.add_llm_usage(purpose, 1000, 200, self.round_cost)
        return item


class FakeEval:
    """替代 subprocess.run 的评估 runner：按策略名返回预设报告，并像真实子进程一样写入试验记录。"""

    def __init__(self, store):
        self.store = store
        self.outcomes: dict[str, dict] = {}
        self.side_effect = None
        self.names: list[str] = []

    def __call__(self, cmd, **kw):
        name = cmd[-1]
        self.names.append(name)
        if self.side_effect:
            self.side_effect(name)
        rep = self.outcomes.get(name, report(name))
        if "gate_pass" in rep:
            self.store.add_trial(name, {}, rep["train"]["sharpe"], rep["val"]["sharpe"],
                                 rep["gate_pass"], rep["holdout_pass"])
        return subprocess.CompletedProcess(cmd, 0, stdout="评估日志\n" + json.dumps(rep) + "\n", stderr="")


@pytest.fixture
def env(tmp_path, monkeypatch):
    for rel in SOURCES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / rel, tmp_path / rel)
    integrity.write_manifest(tmp_path)  # 真实清单：之后 verify(tmp_path) == []
    settings = Settings(anthropic_api_key="", wecom_webhook_url="", monthly_budget_cny=50.0,
                        usd_cny=7.2, root=tmp_path)
    store = Store(":memory:")
    store.set_status("baseline_momo", "incumbent", date(2026, 10, 1))
    commits = []
    monkeypatch.setattr(research, "git_commit",
                        lambda paths, message: commits.append((paths, message)) or True)
    fake_eval = FakeEval(store)
    real_evaluate = research.evaluate_strategy
    monkeypatch.setattr(research, "evaluate_strategy",
                        lambda s, st, name, runner=None: real_evaluate(s, st, name, runner=fake_eval))
    return SimpleNamespace(settings=settings, store=store, notifier=NullNotifier(), commits=commits,
                           evals=fake_eval, root=tmp_path)


def make_llm(e, *script, cost=0.5) -> FakeLLM:
    return FakeLLM(e.settings, e.store, script, round_cost=cost)


def run(e, llm):
    return run_research(e.settings, e.store, e.notifier, llm=llm)


def test_system_prompt_key_points_and_neutral_wording():
    for kw in ["≤5", "沪深 300", "开盘价", "T+1", "train", "val", "holdout", "通过/不通过", "DSR",
               "n_trials", "过拟合", "经济学", "docstring", "STRATEGY", "Strategy", "不联网", "可复现",
               "done=true", "limit_prices(code, name, prev_close, view.asof)", "at_limit_up", "at_limit_down",
               "开盘价不高于 stop"]:
        assert kw in RESEARCH_SYSTEM, kw
    for mod in ALLOWED_IMPORTS:
        assert mod in RESEARCH_SYSTEM, mod
    assert "<<" not in RESEARCH_SYSTEM
    assert not [w for w in BANNED_WORDS if w in RESEARCH_SYSTEM.lower()]


def test_multi_round_feedback_contains_previous_results(env):
    e = env
    e.evals.outcomes["good_one"] = report("good_one", gate=True, holdout=False)
    llm = make_llm(e, rnd(d("good_one"), d("bad_one", extra="import os\n"), analysis="第一轮：换手动量"),
                   rnd(done=True, analysis="结论：good_one 门槛达标但 holdout 未通过"))
    out = run(e, llm)
    assert out["status"] == "ok" and out["rounds"] == 2
    first, second = llm.calls
    for c in llm.calls:
        assert c["purpose"] == "research" and c["system"] == RESEARCH_SYSTEM
        assert c["schema"] is ResearchRound and c["effort"] == "high"
        assert c["timeout"] == 1800.0 and c["essential"] is False
        assert "class Strategy(Protocol)" in c["user"]  # 每轮都带完整背景
    assert first["max_budget_usd"] == 3.0                 # 预算充足时取上限 3 美元
    assert "前几轮" not in first["user"]
    fb = second["user"]
    assert "第一轮：换手动量" in fb
    assert '"gate_pass": true' in fb and '"holdout_pass": false' in fb
    assert '"dsr": 0.9123' in fb                          # 报告数值保留 4 位小数
    assert "bad_one" in fb and "禁止导入 os" in fb         # 被拒原因原样反馈
    assert 'name = "good_one"' in fb                      # 附上草稿代码
    assert "还可评估 4 次" in fb
    prose = re.sub(r"```python.*?```", "", fb, flags=re.S).lower()
    assert not [w for w in BANNED_WORDS if w in prose]    # 代码块以外的文字措辞中性
    assert out["analysis"] == "结论：good_one 门槛达标但 holdout 未通过"
    assert out["written"] == ["good_one"]
    assert [r["name"] for r in out["results"]] == ["good_one", "bad_one"]


def test_eval_cap_enforced_across_rounds(env, monkeypatch):
    e = env
    monkeypatch.setattr(research, "MAX_EVALS_PER_SESSION", 3)
    llm = make_llm(e, rnd(d("a_one"), d("b_two")), rnd(d("c_three"), d("d_four")), rnd(d("e_five")))
    out = run(e, llm)
    assert e.evals.names == ["a_one", "b_two", "c_three"]
    assert len(llm.calls) == 2                            # 额度用完即停，不再请求第 3 轮
    assert "还可评估 1 次" in llm.calls[1]["user"]
    assert out["written"] == ["a_one", "b_two", "c_three"]
    assert not (e.root / "strategies" / "d_four.py").exists()
    skipped = out["results"][-1]
    assert skipped["name"] == "d_four" and not skipped["saved"] and "评估次数已用完" in skipped["message"]


def test_draft_cap_per_round_and_rejections_cost_no_eval(env):
    e = env
    llm = make_llm(e, rnd(d("a_one", extra="import os\n"), d("b_two"), d("c_three"), d("d_four")),
                   rnd(done=True))
    out = run(e, llm)
    assert e.evals.names == ["b_two", "c_three"]          # 被拒的 a_one 不占评估次数
    assert [r["name"] for r in out["results"]] == ["a_one", "b_two", "c_three"]
    assert not (e.root / "strategies" / "d_four.py").exists()
    assert "另有 1 个草稿超出每轮 3 个的上限" in llm.calls[1]["user"]
    assert "还可评估 3 次" in llm.calls[1]["user"]


def test_stops_on_done_after_processing_drafts(env):
    e = env
    llm = make_llm(e, rnd(d("a_one"), done=True), rnd(d("b_two")))
    out = run(e, llm)
    assert len(llm.calls) == 1 and out["rounds"] == 1 and out["status"] == "ok"
    assert e.evals.names == ["a_one"]


def test_stops_on_empty_drafts_without_commit(env):
    e = env
    llm = make_llm(e, rnd(analysis="暂无值得检验的想法"), rnd(d("b_two")))
    out = run(e, llm)
    assert len(llm.calls) == 1 and out["status"] == "ok" and out["written"] == []
    assert e.commits == []
    assert "本次没有提交策略草稿" in e.notifier.sent[-1]


def test_stops_after_max_rounds(env):
    e = env
    llm = make_llm(e, rnd(d("a_one")), rnd(d("b_two")), rnd(d("c_three")), rnd(d("d_four")))
    out = run(e, llm)
    assert len(llm.calls) == research.MAX_ROUNDS == 3 and out["rounds"] == 3
    assert "这是最后一轮" in llm.calls[2]["user"] and "这是最后一轮" not in llm.calls[1]["user"]
    assert e.evals.names == ["a_one", "b_two", "c_three"]


def test_budget_exceeded_mid_session_stops_and_keeps_earlier_work(env):
    e = env
    llm = make_llm(e, rnd(d("a_one")), rnd(d("b_two")), cost=60.0)  # 第 1 轮就花掉 60 元 > 预算 50 元
    out = run(e, llm)
    assert out["status"] == "budget_exceeded" and out["rounds"] == 1 and len(llm.calls) == 1
    assert out["cost_cny"] == pytest.approx(60.0)
    assert e.commits[0][0] == ["strategies/a_one.py"]
    assert "预算" in e.notifier.sent[-1]


def test_budget_exhausted_before_start_skips_session(env):
    e = env
    e.store.add_llm_usage("premarket:active", 0, 0, 50.0)
    llm = make_llm(e, rnd(d("a_one")))
    out = run(e, llm)
    assert out == {"status": "budget_exceeded", "rounds": 0, "written": [], "results": [],
                   "cost_cny": 0.0, "analysis": ""}
    assert llm.calls == [] and e.evals.names == [] and e.commits == []
    assert len(e.notifier.sent) == 1 and "跳过" in e.notifier.sent[0]


def test_max_budget_usd_and_cost_follow_month_budget(env):
    e = env
    e.store.add_llm_usage("review", 0, 0, 40.0)  # 本月已花 40 元，剩 10 元
    llm = make_llm(e, rnd(d("a_one")), rnd(done=True), cost=0.5)
    out = run(e, llm)
    assert llm.calls[0]["max_budget_usd"] == pytest.approx(10.0 / 7.2)
    assert llm.calls[1]["max_budget_usd"] == pytest.approx(9.5 / 7.2)
    assert out["cost_cny"] == pytest.approx(1.0)  # 本次会话的月度花费增量


@pytest.mark.parametrize("exc", [LLMError("research: error_during_execution 中转返回 502"),
                                 subprocess.TimeoutExpired("claude.exe", 1800)])
def test_llm_failure_aborts_cleanly(env, exc):
    e = env
    out = run(e, make_llm(e, exc))
    assert out["status"] == "llm_error" and out["rounds"] == 0 and out["written"] == []
    assert e.evals.names == [] and e.commits == []
    msg = e.notifier.sent[-1]
    assert "告警" in msg and type(exc).__name__ in msg


def test_llm_failure_in_later_round_still_commits_earlier_drafts(env):
    e = env
    out = run(e, make_llm(e, rnd(d("a_one")), LLMError("research: error_max_budget_usd")))
    assert out["status"] == "llm_error" and out["rounds"] == 1 and out["written"] == ["a_one"]
    assert e.commits[0][0] == ["strategies/a_one.py"]
    assert "error_max_budget_usd" in e.notifier.sent[-1]


def _touch_engine(root):
    with open(root / "engine" / "rules.py", "a", encoding="utf-8") as f:
        f.write("\nEXTRA = 1\n")


def test_integrity_failure_before_alerts_without_llm_call(env):
    e = env
    _touch_engine(e.root)
    llm = make_llm(e, rnd(d("a_one")))
    out = run(e, llm)
    assert out["status"] == "integrity_failed" and llm.calls == []
    assert e.evals.names == [] and e.commits == []
    assert "告警" in e.notifier.sent[0] and "engine/rules.py" in e.notifier.sent[0]


def test_integrity_change_after_alerts_and_skips_commit(env):
    e = env
    e.evals.side_effect = lambda name: _touch_engine(e.root)  # 评估过程中 engine/ 被改动
    out = run(e, make_llm(e, rnd(d("a_one"), done=True)))
    assert out["status"] == "integrity_failed" and out["written"] == ["a_one"]
    assert e.commits == []
    assert (e.root / "strategies" / "a_one.py").exists()       # 留在工作区供人工检查
    alert = e.notifier.sent[-1]
    assert "告警" in alert and "engine/rules.py" in alert and "a_one" in alert


def test_commit_summary_and_return_shape(env):
    e = env
    e.evals.outcomes["a_one"] = report("a_one", gate=True, holdout=True)
    llm = make_llm(e, rnd(d("a_one"), d("b_two"), done=True, analysis="结论：a_one 值得进影子盘观察"))
    out = run(e, llm)
    assert set(out) == {"status", "rounds", "written", "results", "cost_cny", "analysis"}
    assert out["written"] == ["a_one", "b_two"] and out["cost_cny"] == pytest.approx(0.5)
    assert e.store.get_status("a_one") == "shadow" and e.store.get_status("b_two") == "candidate"
    paths, message = e.commits[0]
    assert paths == ["strategies/a_one.py", "strategies/b_two.py"]
    assert "a_one 的假设：动量在高换手股票上更强" in message and "b_two 的假设" in message
    assert "进入影子盘" in message
    summary = e.notifier.sent[-1]
    assert "研究会话" in summary and "a_one" in summary and "进入影子盘" in summary
    assert "0.50 元" in summary and "结论：a_one 值得进影子盘观察" in summary


def test_default_llm_is_built_from_settings(env, monkeypatch):
    e = env
    fake = make_llm(e, rnd(done=True))
    seen = []
    monkeypatch.setattr(research, "LLM", lambda settings, store: seen.append((settings, store)) or fake)
    out = run_research(e.settings, e.store, e.notifier)
    assert seen == [(e.settings, e.store)] and len(fake.calls) == 1 and out["status"] == "ok"


def test_failed_eval_still_counts_toward_cap(env):
    e = env
    e.evals.outcomes["a_one"] = {"error": "KeyError: 'close'"}  # run_eval 报错，没有写入试验记录
    llm = make_llm(e, rnd(d("a_one")), rnd(done=True))
    out = run(e, llm)
    assert out["results"][0]["ok"] is False and "KeyError" in out["results"][0]["error"]
    assert "还可评估 4 次" in llm.calls[1]["user"]       # 失败同样消耗一次额度


def test_local_error_stops_and_commits_earlier_drafts(env, monkeypatch):
    e = env
    real = research.submit_draft

    def flaky(settings, store, draft):
        if draft.name == "b_two":
            raise OSError("磁盘已满")
        return real(settings, store, draft)

    monkeypatch.setattr(research, "submit_draft", flaky)
    out = run(e, make_llm(e, rnd(d("a_one"), d("b_two"), d("c_three"))))
    assert out["status"] == "error" and e.evals.names == ["a_one"]
    assert e.commits[0][0] == ["strategies/a_one.py"]
    msg = e.notifier.sent[-1]
    assert "告警" in msg and "OSError" in msg and "磁盘已满" in msg


def test_feedback_truncates_long_code_and_hypothesis(env):
    e = env
    long = Draft(name="long_one", hypothesis="长" * 3000, code=src("long_one", extra="X = 1\n" * 3000))
    llm = make_llm(e, rnd(long), rnd(done=True))
    run(e, llm)
    fb = llm.calls[1]["user"]
    assert "已截断" in fb and "长" * (research.FEEDBACK_HYPOTHESIS_CHARS + 1) not in fb
    assert len(fb) < len(llm.calls[0]["user"]) + research.FEEDBACK_CODE_CHARS + 3000


def test_draft_block_caps_message_and_error():
    res = {"name": "x_one", "hypothesis": "h", "message": "m" * 10_000, "saved": True, "ok": False,
           "report": None, "error": "e" * 10_000}
    block = research._draft_block(res, Draft(name="x_one", hypothesis="h", code="X = 1\n"))
    assert "m" * (research.FEEDBACK_MESSAGE_CHARS + 1) not in block
    assert "e" * (research.EVAL_ERROR_CHARS + 1_601) not in block and "e" * research.EVAL_ERROR_CHARS in block


def test_context_built_once_and_round_task_has_current_n_trials(env, monkeypatch):
    e = env
    built = []
    real = research.build_context
    monkeypatch.setattr(research, "build_context", lambda s, st: built.append(1) or real(s, st))
    llm = make_llm(e, rnd(d("a_one")), rnd(done=True))
    run(e, llm)
    assert built == [1]
    assert "n_trials：0（下一次评估计为第 1 次）" in llm.calls[0]["user"]
    assert "n_trials：1（下一次评估计为第 2 次）" in llm.calls[1]["user"]


def test_engine_change_mid_session_stops_and_reverts_shadow(env):
    e = env
    for n in ("a_one", "b_two"):
        e.evals.outcomes[n] = report(n, gate=True, holdout=True)
    e.evals.side_effect = lambda name: _touch_engine(e.root) if name == "a_one" else None
    llm = make_llm(e, rnd(d("a_one"), d("b_two")), rnd(d("c_three")))
    out = run(e, llm)
    assert out["status"] == "integrity_failed" and set(out) == {"status", "rounds", "written", "results",
                                                               "cost_cny", "analysis"}
    assert e.evals.names == ["a_one"] and len(llm.calls) == 1   # 不再评估、不再请求 LLM
    assert e.store.get_status("a_one") == "candidate"           # 不参与前向模拟
    assert e.store.strategies_with_status("shadow") == [] and e.commits == []
    assert "已从影子盘改回 candidate：a_one" in e.notifier.sent[-1]
    trial = e.store.last_trial("a_one")                         # 试验记录作废，仍计入 n_trials
    assert trial["gate_pass"] is False and trial["holdout_pass"] is False and e.store.trial_count() == 1


def test_mid_session_mismatch_is_reported_even_if_restored(env, monkeypatch):
    e = env
    e.evals.outcomes["a_one"] = report("a_one", gate=True, holdout=True)
    calls = []
    real = research.verify

    def flaky_verify(root):
        calls.append(1)
        return [".integrity/manifest.json"] if len(calls) == 2 else real(root)  # 只有第一次评估后不符

    monkeypatch.setattr(research, "verify", flaky_verify)
    llm = make_llm(e, rnd(d("a_one"), d("b_two")), rnd(d("c_three")))
    out = run(e, llm)
    assert out["status"] == "integrity_failed" and e.evals.names == ["a_one"] and len(llm.calls) == 1
    assert e.store.get_status("a_one") == "candidate" and e.commits == []
    alert = e.notifier.sent[-1]
    assert "会话中 engine/ 校验不符" in alert and ".integrity/manifest.json" in alert
    assert e.store.last_trial("a_one")["gate_pass"] is False             # 试验记录已作废
    monkeypatch.setattr(research, "verify", real)
    ctx = research.build_context(e.settings, e.store)
    assert "a_one：已作废" in ctx and "a_one：train" not in ctx            # 下次背景里不当作真实失败


def test_commit_failure_is_reported(env, monkeypatch):
    e = env
    monkeypatch.setattr(research, "git_commit", lambda paths, message: False)
    out = run(e, make_llm(e, rnd(d("a_one"), done=True)))
    assert out["status"] == "ok"
    assert "git 提交失败，请人工提交：strategies/a_one.py" in e.notifier.sent[-1]


def test_system_prompt_explains_reserved_names_and_suffix():
    assert "open_px" in RESEARCH_SYSTEM and "连变量名" in RESEARCH_SYSTEM
    assert "to_string" in RESEARCH_SYSTEM and ".format" in RESEARCH_SYSTEM
    assert "年月后缀" in RESEARCH_SYSTEM
