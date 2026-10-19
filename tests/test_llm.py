import datetime as dt
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from agents import llm as llm_mod
from agents.llm import LLM, BudgetExceeded, LLMError, build_args, child_env, find_claude_exe, strict_schema
from config import Settings
from engine.store import Store


class Inner(BaseModel):
    code: str = Field(description="股票代码")
    score: float


class Outer(BaseModel):
    view: str
    items: list[Inner]
    note: str = ""


GOOD = {"view": "震荡", "items": [{"code": "600000", "score": 0.7}], "note": ""}
SYSTEM = "你是测试助手。\n只输出 JSON。"
USER = "请分析 600000 浦发银行的竞价情况"
NPM_EXE = Path("node_modules/@anthropic-ai/claude-code/bin/claude.exe")


def cli_output(structured=GOOD, *, is_error=False, subtype="success", result="", cost=0.0215):
    """claude -p --output-format json 的 stdout 结构（字段与实测一致）。"""
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": result,
            "structured_output": structured, "total_cost_usd": cost, "duration_ms": 15000, "num_turns": 2,
            "usage": {"input_tokens": 1200, "cache_creation_input_tokens": 3000,
                      "cache_read_input_tokens": 500, "output_tokens": 350}}


class FakeRunner:
    """subprocess.run 替身：记录参数，在调用期间读出系统提示词文件，返回预设 stdout。"""

    def __init__(self, payload=None, returncode=0, stdout=None, exc=None):
        self.stdout = stdout if stdout is not None else json.dumps(payload or cli_output()).encode("utf-8")
        self.returncode = returncode
        self.exc = exc
        self.calls: list[tuple[list[str], dict]] = []
        self.system_seen: list[bytes] = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        self.system_seen.append(Path(args[args.index("--system-prompt-file") + 1]).read_bytes())
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(args=args, returncode=self.returncode, stdout=self.stdout, stderr=b"")

    def system_file(self, i=0) -> Path:
        args = self.calls[i][0]
        return Path(args[args.index("--system-prompt-file") + 1])


def make_settings(root, **kw) -> Settings:
    base = dict(anthropic_api_key="", wecom_webhook_url="", root=root, usd_cny=7.2)
    base.update(kw)
    return Settings(**base)


def touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, claude_exe=str(touch(tmp_path / "bin" / "claude.exe")))


@pytest.fixture
def no_claude(monkeypatch, tmp_path):
    """隔离本机安装：shutil.which 只认测试登记的路径，家目录指向 tmp_path/home。"""
    found: dict[str, str] = {}
    monkeypatch.setattr(llm_mod.shutil, "which", lambda name: found.get(name))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return found


def usage_rows(store):
    return store.conn.execute("SELECT purpose, input_tokens, output_tokens, cost_cny FROM llm_usage").fetchall()


def this_month_cost(store):
    t = dt.date.today()
    return store.month_llm_cost(t.year, t.month)


def test_strict_schema_nested():
    s = strict_schema(Outer)
    assert s["additionalProperties"] is False
    assert sorted(s["required"]) == ["items", "note", "view"]
    inner = s["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert sorted(inner["required"]) == ["code", "score"]


def test_build_args_is_the_verified_command(tmp_path):
    s = make_settings(tmp_path)
    args = build_args(s, r"C:\cc\claude.exe", tmp_path / "sys.md", '{"type": "object"}', "high", 1.5)
    assert args == [r"C:\cc\claude.exe", "-p", "--bare", "--output-format", "json",
                    "--model", "claude-opus-5-5", "--effort", "high", "--tools", "",
                    "--no-session-persistence", "--max-budget-usd", "1.50",
                    "--system-prompt-file", str(tmp_path / "sys.md"), "--json-schema", '{"type": "object"}']
    with pytest.raises(ValueError):
        build_args(s, "claude.exe", "sys.md", "{}", "extreme", 1.0)


def test_child_env_strips_claudecode_and_inherited_anthropic_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-other")
    monkeypatch.setenv("RSIQS_KEEP_ME", "yes")
    relay = make_settings(tmp_path, anthropic_base_url="https://relay.example.invalid", anthropic_auth_token="tok")
    env = child_env(relay)
    assert "CLAUDECODE" not in env and env["RSIQS_KEEP_ME"] == "yes"
    assert {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")} == {
        "ANTHROPIC_BASE_URL": "https://relay.example.invalid", "ANTHROPIC_AUTH_TOKEN": "tok"}
    official = child_env(make_settings(tmp_path, anthropic_api_key="sk-test"))
    assert {k: v for k, v in official.items() if k.startswith("ANTHROPIC_")} == {"ANTHROPIC_API_KEY": "sk-test"}


def test_parse_runs_cli_and_returns_model(store, settings, tmp_path):
    runner = FakeRunner()
    out = LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, effort="low", timeout=30.0)
    assert isinstance(out, Outer) and out.view == "震荡" and out.items[0].code == "600000"
    assert len(runner.calls) == 1
    args, kw = runner.calls[0]
    assert args[0] == settings.claude_exe and args[1:3] == ["-p", "--bare"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--effort") + 1] == "low"
    assert args[args.index("--max-budget-usd") + 1] == "1.00"
    schema_json = args[args.index("--json-schema") + 1]
    assert schema_json == json.dumps(strict_schema(Outer), ensure_ascii=True)
    assert schema_json.isascii() and "股票代码" in json.loads(schema_json)["$defs"]["Inner"]["properties"]["code"][
        "description"]
    assert args == build_args(settings, settings.claude_exe, runner.system_file(), schema_json, "low", 1.0)
    assert kw["input"] == USER.encode("utf-8")
    assert kw["capture_output"] is True and kw["timeout"] == 30.0 and kw["cwd"] == str(tmp_path)
    assert runner.system_seen == [SYSTEM.encode("utf-8")]  # 调用期间文件内容 == 系统提示词
    assert runner.system_file().suffix == ".md" and not runner.system_file().exists()  # 调用后删除


def test_parse_passes_child_env(monkeypatch, store, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://inherited.invalid")
    s = make_settings(tmp_path, claude_exe=str(touch(tmp_path / "claude.exe")), anthropic_api_key="sk-test")
    runner = FakeRunner()
    LLM(s, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    env = runner.calls[0][1]["env"]
    assert "CLAUDECODE" not in env
    assert {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")} == {"ANTHROPIC_API_KEY": "sk-test"}


def test_usage_tokens_and_cost_recorded(store, settings):
    LLM(settings, store, runner=FakeRunner()).parse("unit", SYSTEM, USER, Outer, max_budget_usd=0.3)
    [(purpose, tokens_in, tokens_out, cost)] = usage_rows(store)
    assert (purpose, tokens_in, tokens_out) == ("unit", 1200 + 3000 + 500, 350)
    assert cost == pytest.approx(0.0215 * 7.2)
    assert this_month_cost(store) == pytest.approx(0.0215 * 7.2)


def test_cli_error_raises_but_usage_recorded(store, settings):
    runner = FakeRunner(cli_output(structured=None, is_error=True, subtype="error_max_budget_usd",
                                   result="Reached maximum budget", cost=0.5), returncode=1)
    with pytest.raises(LLMError, match="error_max_budget_usd"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert this_month_cost(store) == pytest.approx(0.5 * 7.2)
    assert not runner.system_file().exists()


NO_STRUCTURED = {k: v for k, v in cli_output().items() if k != "structured_output"}


@pytest.mark.parametrize("payload, returncode", [
    (cli_output(is_error=True, result="Claude 拒绝回答"), 0),  # is_error
    (cli_output(), 1),  # 退出码非 0
    (cli_output(structured=None, result="纯文本回答"), 0),  # structured_output 为 null
    (NO_STRUCTURED, 0),  # 没有 structured_output 字段
])
def test_cli_failures_raise_llm_error(store, settings, payload, returncode):
    with pytest.raises(LLMError):
        LLM(settings, store, runner=FakeRunner(payload, returncode=returncode)).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1


@pytest.mark.parametrize("stdout", [b"", b"Error: something went wrong", b"\xff\xfe\x00", b"[1, 2]"])
def test_non_json_stdout_raises_llm_error(store, settings, stdout):
    runner = FakeRunner(stdout=stdout, returncode=1)
    with pytest.raises(LLMError):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert usage_rows(store) == []
    assert not runner.system_file().exists()


def test_structured_output_failing_validation_raises_llm_error(store, settings):
    runner = FakeRunner(cli_output(structured={"view": 1}))
    with pytest.raises(LLMError, match="Outer"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1


def test_timeout_propagates_and_cleans_up(store, settings):
    runner = FakeRunner(exc=subprocess.TimeoutExpired(["claude.exe"], 5.0))
    with pytest.raises(subprocess.TimeoutExpired):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, timeout=5.0)
    assert runner.system_seen == [SYSTEM.encode("utf-8")] and not runner.system_file().exists()
    assert usage_rows(store) == []


def test_launch_failure_raises_llm_error(store, settings):
    runner = FakeRunner(exc=FileNotFoundError(2, "系统找不到指定的文件。"))
    with pytest.raises(LLMError, match="claude.exe"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert not runner.system_file().exists() and usage_rows(store) == []


def test_budget_exceeded_non_essential_skips_runner(no_claude, store, tmp_path):
    store.add_llm_usage("old", 0, 0, 300.0)
    runner = FakeRunner()
    llm = LLM(make_settings(tmp_path, monthly_budget_cny=300.0), store, runner=runner)
    with pytest.raises(BudgetExceeded):  # 先查预算：连 claude.exe 都不用找
        llm.parse("unit", SYSTEM, USER, Outer)
    assert runner.calls == []


def test_budget_exceeded_essential_passes(store, settings):
    store.add_llm_usage("old", 0, 0, 300.0)
    runner = FakeRunner()
    assert LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, essential=True).view == "震荡"
    assert len(runner.calls) == 1


def test_exe_resolved_lazily_and_cached(monkeypatch, store, settings):
    lookups = []
    monkeypatch.setattr(llm_mod, "find_claude_exe", lambda s: lookups.append(s) or s.claude_exe)
    runner = FakeRunner()
    llm = LLM(settings, store, runner=runner)
    assert lookups == []  # 构造时不查找
    llm.parse("a", SYSTEM, USER, Outer)
    llm.parse("b", SYSTEM, USER, Outer)
    assert len(lookups) == 1 and len(runner.calls) == 2


def test_missing_exe_raises_llm_error_at_parse(no_claude, store, tmp_path):
    runner = FakeRunner()
    llm = LLM(make_settings(tmp_path), store, runner=runner)  # 找不到 claude.exe 也能构造
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        llm.parse("unit", SYSTEM, USER, Outer)
    assert runner.calls == [] and usage_rows(store) == []


def test_find_settings_value_wins(no_claude, tmp_path):
    mine = touch(tmp_path / "custom" / "claude.exe")
    no_claude["claude.exe"] = str(touch(tmp_path / "other" / "claude.exe"))
    assert find_claude_exe(make_settings(tmp_path, claude_exe=str(mine))) == str(mine)
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        find_claude_exe(make_settings(tmp_path, claude_exe=str(tmp_path / "nope" / "claude.exe")))


def test_find_settings_wrapper_maps_to_npm_exe(no_claude, tmp_path):
    exe = touch(tmp_path / "npm" / NPM_EXE)
    cmd = touch(tmp_path / "npm" / "claude.cmd")
    assert find_claude_exe(make_settings(tmp_path, claude_exe=str(cmd))) == str(exe)
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        find_claude_exe(make_settings(tmp_path, claude_exe=str(touch(tmp_path / "x" / "claude.ps1"))))


def test_find_npm_wrapper_on_path_resolves_to_sibling_exe(no_claude, tmp_path):
    no_claude["claude"] = str(touch(tmp_path / "nodejs" / "claude.CMD"))
    exe = touch(tmp_path / "nodejs" / NPM_EXE)
    no_claude["claude.exe"] = str(touch(tmp_path / "other" / "claude.exe"))
    assert find_claude_exe(make_settings(tmp_path)) == str(exe)  # npm 旁的 exe 优先


def test_find_never_returns_wrapper(no_claude, tmp_path):
    wrapper = str(touch(tmp_path / "nodejs" / "claude.cmd"))  # npm 目录残缺：没有 bin/claude.exe
    no_claude["claude"] = wrapper
    no_claude["claude.exe"] = wrapper
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        find_claude_exe(make_settings(tmp_path))


def test_find_path_exe_then_native_install_then_error(no_claude, tmp_path):
    on_path = str(touch(tmp_path / "bin" / "claude.exe"))
    no_claude.update({"claude": on_path, "claude.exe": on_path})  # 原生安装在 PATH 上
    assert find_claude_exe(make_settings(tmp_path)) == on_path
    no_claude.clear()
    native = touch(tmp_path / "home" / ".local" / "bin" / "claude.exe")
    assert find_claude_exe(make_settings(tmp_path)) == str(native)
    native.unlink()
    with pytest.raises(LLMError, match="找不到 claude.exe"):
        find_claude_exe(make_settings(tmp_path))
