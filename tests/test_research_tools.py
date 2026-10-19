import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from agents import research
from agents.research import Draft, build_context, evaluate_strategy, submit_draft
from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE
from engine.store import Store
from strategies.sandbox import ALLOWED_IMPORTS

PROJECT = Path(__file__).resolve().parents[1]
SOURCES = ["strategies/base.py", "strategies/baseline_momo.py",
           "engine/types.py", "engine/dataview.py", "engine/rules.py"]

GOOD_SRC = '''"""假设：测试用空策略。"""
from engine.dataview import DataView
from engine.types import Pick


class Probe:
    name = "probe_one"

    def select(self, view: DataView) -> list[Pick]:
        return []


STRATEGY = Probe()
'''


@pytest.fixture
def env(tmp_path):
    for rel in SOURCES:  # 用真实源码搭一个临时项目根目录
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / rel, tmp_path / rel)
    settings = Settings(anthropic_api_key="", wecom_webhook_url="", root=tmp_path)
    store = Store(":memory:")
    store.set_status("baseline_momo", "incumbent", date(2026, 10, 1))
    return settings, store


def draft(name="probe_one", code=GOOD_SRC, hypothesis="测试假设：空策略"):
    return Draft(name=name, hypothesis=hypothesis, code=code)


# ---------- submit_draft ----------

@pytest.mark.parametrize("name", ["Bad", "ab", "1abc", "foo-bar", "../evil", "x" * 42,
                                  "base", "sandbox", "__init__"])
def test_submit_bad_name_rejected(env, name):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(name=name))
    assert not ok and "不合规" in msg
    assert store.get_status(name) is None
    assert sorted(p.name for p in (settings.root / "strategies").glob("*.py")) == ["base.py", "baseline_momo.py"]


def test_submit_incumbent_rejected_and_file_untouched(env):
    settings, store = env
    path = settings.root / "strategies" / "baseline_momo.py"
    before = path.read_text(encoding="utf-8")
    ok, msg = submit_draft(settings, store, draft(name="baseline_momo"))
    assert not ok and "当前现任策略" in msg
    assert path.read_text(encoding="utf-8") == before
    assert store.get_status("baseline_momo") == "incumbent"


@pytest.mark.parametrize("status", ["shadow", "candidate", "retired"])
def test_submit_existing_name_rejected(env, status):
    settings, store = env
    store.set_status("old_idea", status, date(2026, 10, 1))
    ok, msg = submit_draft(settings, store, draft(name="old_idea"))
    assert not ok and "新名字" in msg
    assert store.get_status("old_idea") == status
    assert not (settings.root / "strategies" / "old_idea.py").exists()


def test_submit_existing_file_without_status_rejected(env):
    settings, store = env
    path = settings.root / "strategies" / "manual_one.py"
    path.write_text("# 手写策略\n", encoding="utf-8")
    ok, msg = submit_draft(settings, store, draft(name="manual_one"))
    assert not ok and "新名字" in msg
    assert path.read_text(encoding="utf-8") == "# 手写策略\n"
    assert store.get_status("manual_one") is None


def test_submit_disallowed_import_rejected_with_clear_reason(env):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(code="import os\n" + GOOD_SRC))
    assert not ok
    assert "代码规范检查未通过" in msg and "禁止导入 os" in msg  # 原样反馈给模型
    assert not (settings.root / "strategies" / "probe_one.py").exists()
    assert store.get_status("probe_one") is None


def test_submit_syntax_error_rejected(env):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(code="def broken(:\n    return []\n"))
    assert not ok and "语法错误" in msg and "第 1 行" in msg
    assert not (settings.root / "strategies" / "probe_one.py").exists()


def test_submit_compile_only_error_rejected(env):
    # ast.parse 能过、compile() 才报错的代码（模块顶层 return）
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(code=GOOD_SRC + "return STRATEGY\n"))
    assert not ok and "语法错误" in msg
    assert store.get_status("probe_one") is None


def test_submit_success_writes_file_and_sets_candidate(env):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(name=" probe_one "))  # 首尾空白会被去掉
    assert ok and "strategies/probe_one.py" in msg
    path = settings.root / "strategies" / "probe_one.py"
    assert path.read_text(encoding="utf-8") == GOOD_SRC
    assert b"\r\n" not in path.read_bytes()  # Windows 上也写 LF
    assert store.get_status("probe_one") == "candidate"
    assert store.kv_get("research:hypothesis:probe_one") == "测试假设：空策略"
    ok2, msg2 = submit_draft(settings, store, draft())  # 同名再交一次：拒绝
    assert not ok2 and "新名字" in msg2


# ---------- evaluate_strategy ----------

def report(gate=True, holdout=True):
    return {"strategy": "probe_one", "train": {"sharpe": 1.0}, "val": {"sharpe": 1.2},
            "dsr": 0.95, "gate_pass": gate, "holdout_pass": holdout, "n_trials": 3}


def fake_runner(stdout, returncode=0, stderr="", calls=None, trial=None):
    """trial=(store, gate, holdout)：像真实子进程里的 evaluate 一样先写一条试验记录。"""
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        if trial is not None:
            store, gate, holdout = trial
            store.add_trial(cmd[-1], {}, 1.0, 1.2, gate, holdout)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return run


@pytest.fixture
def written(env):
    settings, store = env
    assert submit_draft(settings, store, draft())[0]
    return settings, store


def test_evaluate_pass_parses_last_json_line_and_sets_shadow(written):
    settings, store = written
    calls = []
    stdout = "策略日志一行\n" + json.dumps(report()) + "\n\n"
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(stdout, calls=calls, trial=(store, True, True)))
    assert out == {"ok": True, "report": report(), "error": ""}
    assert store.get_status("probe_one") == "shadow"
    cmd, kw = calls[0]
    assert cmd == [sys.executable, "-m", "engine.run_eval", "probe_one"]
    assert kw["cwd"] == str(settings.root) and kw["timeout"] == research.EVAL_TIMEOUT == 1800
    assert kw["env"]["PYTHONIOENCODING"] == "utf-8"


@pytest.mark.parametrize("gate,holdout", [(True, False), (False, None)])
def test_evaluate_without_full_pass_keeps_candidate(written, gate, holdout):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(json.dumps(report(gate, holdout)), trial=(store, gate, holdout)))
    assert out["ok"] is True and out["report"]["gate_pass"] is gate and out["error"] == ""
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_forged_report_without_trial_rejected(written):
    settings, store = written  # 策略打印伪造的报告后提前结束进程：数据库里没有本次的试验记录
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(json.dumps(report())))
    assert out["ok"] is False and out["report"] is None and "试验记录" in out["error"]
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_status_follows_trial_record_not_stdout(written):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(json.dumps(report()), trial=(store, False, None)))
    assert out["ok"] is True and out["report"]["gate_pass"] is False and out["report"]["holdout_pass"] is None
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_timeout(written):
    settings, store = written

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    out = evaluate_strategy(settings, store, "probe_one", runner=slow)
    assert out["ok"] is False and out["report"] is None and "超时" in out["error"]
    assert store.get_status("probe_one") == "candidate"
    trial = store.last_trial("probe_one")  # 跑不完的评估同样计入 n_trials
    assert store.trial_count() == 1 and trial["gate_pass"] is False and trial["holdout_pass"] is None


def test_evaluate_cannot_start_is_not_counted(written):
    settings, store = written

    def missing(cmd, **kw):
        raise FileNotFoundError("python 不存在")

    evaluate_strategy(settings, store, "probe_one", runner=missing)
    assert store.trial_count() == 0


def test_evaluate_cannot_start(written):
    settings, store = written

    def missing(cmd, **kw):
        raise FileNotFoundError("python 不存在")

    out = evaluate_strategy(settings, store, "probe_one", runner=missing)
    assert out["ok"] is False and "无法启动" in out["error"]


@pytest.mark.parametrize("stdout,code", [
    ('{"error": "KeyError: \'close\'"}\n', 1),  # run_eval 的错误 JSON
    (json.dumps(report()) + "\n", 3),           # 退出码非 0 时，报告也不采信
])
def test_evaluate_nonzero_exit(written, stdout, code):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(stdout, returncode=code))
    assert out["ok"] is False and out["report"] is None and f"退出码 {code}" in out["error"]
    assert store.get_status("probe_one") == "candidate"
    if code == 1:
        assert "KeyError" in out["error"]


@pytest.mark.parametrize("stdout", ["", "not json\n", "[1, 2]\n", '{"strategy": "probe_one"}\n',
                                    json.dumps(report()) + "\n多出来的一行\n"])
def test_evaluate_garbage_output(written, stdout):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(stdout, stderr="Traceback: 出错了"))
    assert out["ok"] is False and out["report"] is None and "评估失败" in out["error"]
    assert store.get_status("probe_one") == "candidate"


# ---------- build_context ----------

def test_build_context_contents(env):
    settings, store = env
    store.add_trial("old_idea", {}, 0.4, 0.2, False, None)
    store.kv_set("research:hypothesis:old_idea", "小盘股短期反转更强")
    store.set_status("old_idea", "candidate", date(2026, 10, 2))
    store.set_status("shadow_one", "shadow", date(2026, 10, 3))
    ctx = build_context(settings, store)
    assert "class Strategy(Protocol)" in ctx           # strategies/base.py 接口
    assert "class BaselineMomo" in ctx                 # 基线策略源码
    assert "class DataView" in ctx and "class Pick" in ctx and "def limit_prices" in ctx
    for mod in ALLOWED_IMPORTS:
        assert mod in ctx
    assert json.dumps(GATE) in ctx and json.dumps(HOLDOUT_GATE) in ctx
    assert "2016-01-01" in ctx and "2026-07-01" in ctx  # SPLITS
    assert "n_trials：1" in ctx
    assert "old_idea" in ctx and "小盘股短期反转更强" in ctx  # 最近试验及其假设
    assert "shadow_one" in ctx and "baseline_momo" in ctx       # 策略状态
    assert "沙箱" not in ctx                                    # 源码里的 # 注释已去掉
    assert len(ctx) <= research.CONTEXT_MAX_CHARS


def test_build_context_incumbent_source_and_length_cap(env, monkeypatch):
    settings, store = env
    store.set_status("baseline_momo", "retired", date(2026, 10, 5))
    store.set_status("momo_v2", "incumbent", date(2026, 10, 5))
    body = '"""现任策略。"""\n' + "\n".join(f"X_{i} = {i}" for i in range(20_000))
    (settings.root / "strategies" / "momo_v2.py").write_text(body, encoding="utf-8")
    ctx = build_context(settings, store)
    assert "strategies/momo_v2.py（当前现任策略）" in ctx and "X_1 = 1" in ctx
    assert "已截断" in ctx and "X_19999" not in ctx           # 单文件上限
    assert "class BaselineMomo" in ctx
    assert len(ctx) <= research.CONTEXT_MAX_CHARS
    monkeypatch.setattr(research, "CONTEXT_MAX_CHARS", 3000)  # 总长度上限
    assert len(build_context(settings, store)) <= 3000


# 提示词里不能出现的措辞（会触发分类器，导致调用失败）；背景里的源码也算
CONTEXT_BANNED = ["安全", "沙箱", "绕过", "权限", "漏洞", "攻击", "注入", "逃逸", "恶意",
                  "sandbox", "bypass", "security", "permission", "exploit", "jailbreak", "escape", "malicious",
                  "attack", "inject", "vulnerab"]


def test_build_context_wording_including_embedded_source(env):
    settings, store = env
    ctx = build_context(settings, store)
    assert not [w for w in CONTEXT_BANNED if w in ctx.lower()]
    assert "RESERVED: set[str]" in ctx                 # 保留模块名那一行换成了中性说明


def test_build_context_status_list_cap(env, monkeypatch):
    settings, store = env
    names = [f"cand_{i:03d}" for i in range(60)]
    for n in names:
        store.set_status(n, "candidate", date(2026, 10, 2))
    ctx = build_context(settings, store)
    assert all(n in ctx for n in names)                # 60 个都列出
    monkeypatch.setattr(research, "STATUS_LIST_MAX", 5)
    ctx = build_context(settings, store)
    assert "cand_004" in ctx and "cand_005" not in ctx and "等 60 个" in ctx and "年月后缀" in ctx


def test_evaluate_uses_utf8_and_reports_stderr(written):
    settings, store = written
    calls = []
    evaluate_strategy(settings, store, "probe_one", runner=fake_runner("", calls=calls, stderr="出错了"))
    assert calls[0][1]["encoding"] == "utf-8"
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner("", stderr="Traceback: 出错了"))
    assert "出错了" in out["error"]


def test_evaluate_error_includes_strategy_frames(written):
    settings, store = written
    stderr = ("ERROR:root:评估 probe_one 失败\nTraceback (most recent call last):\n"
              '  File "C:\\proj\\engine\\backtest.py", line 88, in run\n    picks = strategy.select(view)\n'
              '  File "C:\\proj\\strategies\\probe_one.py", line 12, in select\n'
              '    x = h["close"].iloc[-21]\n        ^^^^^^^^^^^^^^^^^^^^^^\n'
              "IndexError: single positional indexer is out-of-bounds\n")
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(
        '{"error": "IndexError: single positional indexer is out-of-bounds"}\n', returncode=1, stderr=stderr))
    assert 'strategies/probe_one.py 第 12 行（select）：x = h["close"].iloc[-21]' in out["error"]
    assert "backtest.py" not in out["error"]           # 只给策略文件里的帧


def test_evaluate_error_length_is_capped(written):
    settings, store = written
    frame = '  File "/p/strategies/probe_one.py", line 7, in select\n    y = 1\n'
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(
        '{"error": "' + "x" * 10_000 + '"}\n', returncode=1, stderr=frame * 2000))
    assert len(out["error"]) <= research.EVAL_ERROR_CHARS + 1_600
    assert len(research._strategy_frames(frame * 2000, "probe_one")) <= 1500


def test_strategy_frames_without_source_line():
    stderr = ('  File "/p/strategies/probe_one.py", line 7, in select\n'
              '  File "/p/strategies/probe_one.py", line 3, in helper\n')
    assert research._strategy_frames(stderr, "probe_one") == (
        "strategies/probe_one.py 第 7 行（select）：\nstrategies/probe_one.py 第 3 行（helper）：")
