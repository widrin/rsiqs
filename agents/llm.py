"""Claude 调用封装：经 Claude Code CLI 无头模式（claude.exe -p --bare）调用，结构化输出、记账、预算。

中转只接受 Claude Code 客户端，Anthropic SDK 直连会被拒绝，所以这里不用 SDK：每次调用起一个 claude.exe 子进程，
系统提示词写进临时 .md 文件，用户提示词以 UTF-8 字节走 stdin，JSON Schema 走 --json-schema。
每次都传 --tools ""：模型不使用任何工具，所需上下文全部由 Python 放进提示词。
费用取 CLI 输出的 total_cost_usd（美元）× usd_cny 记入 llm_usage。
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from config import Settings
from engine.store import Store

EFFORTS = ("low", "medium", "high", "xhigh", "max")
NPM_EXE = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
WRAPPER_SUFFIXES = (".cmd", ".bat", ".ps1")
NOT_FOUND = "找不到 claude.exe，请在 .env 设置 CLAUDE_EXE"


class BudgetExceeded(Exception):
    pass


class LLMError(Exception):
    """找不到或无法运行 claude.exe、CLI 报错或拒答、没有 structured_output、stdout 不是 JSON、结构校验失败。"""


def _strictify(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for v in node.values():
            _strictify(v)
    elif isinstance(node, list):
        for v in node:
            _strictify(v)


def strict_schema(model: type[BaseModel]) -> dict:
    """model_json_schema()，并对每个 object（含 $defs）设 additionalProperties=False、required=全部字段。"""
    schema = copy.deepcopy(model.model_json_schema())
    _strictify(schema)
    return schema


def _is_wrapper(path: str) -> bool:
    return path.lower().endswith(WRAPPER_SUFFIXES)


def find_claude_exe(settings: Settings) -> str:
    """claude.exe 的路径，从不返回 .cmd/.ps1 包装脚本（cmd.exe 的引号规则会破坏 JSON 参数）。

    顺序：settings.claude_exe → PATH 上 claude（npm 包装脚本）旁的 node_modules/.../bin/claude.exe
    → PATH 上的 claude.exe → ~/.local/bin/claude.exe（原生安装）。
    """
    configured = settings.claude_exe.strip().strip('"')
    if configured:
        if _is_wrapper(configured):
            exe = Path(configured).parent / NPM_EXE
            if exe.is_file():
                return str(exe)
            raise LLMError(f"CLAUDE_EXE 指向包装脚本 {configured}，请改为 claude.exe 的完整路径")
        if not Path(configured).is_file():
            raise LLMError(f"CLAUDE_EXE 指向的文件不存在：{configured!r}（请填完整路径，.env 里不要加双引号）")
        return configured
    wrapper = shutil.which("claude")
    if wrapper:
        exe = Path(wrapper).parent / NPM_EXE
        if exe.is_file():
            return str(exe)
    found = shutil.which("claude.exe")
    if found and not _is_wrapper(found):
        return found
    native = Path.home() / ".local" / "bin" / "claude.exe"
    if native.is_file():
        return str(native)
    raise LLMError(NOT_FOUND)


def child_env(settings: Settings) -> dict[str, str]:
    """子进程环境：去掉 CLAUDECODE 与所有继承来的 ANTHROPIC_*，只放入 settings 中非空的鉴权变量。"""
    env = {k: v for k, v in os.environ.items()
           if k.upper() != "CLAUDECODE" and not k.upper().startswith("ANTHROPIC_")}
    for key, value in (("ANTHROPIC_API_KEY", settings.anthropic_api_key),
                       ("ANTHROPIC_BASE_URL", settings.anthropic_base_url),
                       ("ANTHROPIC_AUTH_TOKEN", settings.anthropic_auth_token)):
        if value:
            env[key] = value
    return env


def build_args(settings: Settings, exe: str, system_file: str | Path, schema_json: str, effort: str,
               max_budget_usd: float) -> list[str]:
    """已实测可用的无头调用命令行；用户提示词不在命令行里，走 stdin。"""
    if effort not in EFFORTS:
        raise ValueError(f"unknown effort: {effort!r}")
    return [exe, "-p", "--bare", "--output-format", "json", "--model", settings.llm_model,
            "--effort", effort, "--tools", "", "--no-session-persistence",
            "--max-budget-usd", f"{max_budget_usd:.2f}", "--system-prompt-file", str(system_file),
            "--json-schema", schema_json]


def _int(value: Any) -> int:
    return int(value or 0)


def _tail(raw: Any, limit: int = 300) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return str(raw or "").strip()[-limit:]


class LLM:
    def __init__(self, settings: Settings, store: Store, runner: Callable[..., Any] | None = None):
        self.settings = settings
        self.store = store
        self.runner = runner or subprocess.run
        self._exe: str | None = None  # 首次 parse 时查找并缓存，构造 LLM 永不失败

    def _claude_exe(self) -> str:
        if self._exe is None:
            self._exe = find_claude_exe(self.settings)
        return self._exe

    def month_cost(self) -> float:
        t = dt.date.today()
        return self.store.month_llm_cost(t.year, t.month)

    def check_budget(self, essential: bool) -> None:
        if not essential and self.month_cost() >= self.settings.monthly_budget_cny:
            raise BudgetExceeded(
                f"本月 LLM 花费 {self.month_cost():.2f} 元已达预算 {self.settings.monthly_budget_cny:.0f} 元")

    def parse(self, purpose: str, system: str, user: str, schema: type[BaseModel], effort: str = "medium",
              timeout: float = 120.0, max_budget_usd: float = 1.0, essential: bool = False) -> BaseModel:
        """一次无头调用，返回 schema 实例。

        预算不足抛 BudgetExceeded；超时抛 subprocess.TimeoutExpired（子进程已被杀掉）；
        claude.exe 启动失败、CLI 报错、拒答、没有 structured_output、输出不是 JSON、结构校验失败都抛 LLMError。
        """
        self.check_budget(essential)
        exe = self._claude_exe()
        schema_json = json.dumps(strict_schema(schema), ensure_ascii=True)
        fd, system_file = tempfile.mkstemp(prefix="rsiqs_system_", suffix=".md")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(system.encode("utf-8"))
            args = build_args(self.settings, exe, system_file, schema_json, effort, max_budget_usd)
            proc = self.runner(args, input=user.encode("utf-8"), capture_output=True,
                               env=child_env(self.settings), cwd=str(self.settings.root), timeout=timeout)
        except OSError as e:  # claude.exe 启动不了（安装后被删除、没有权限等）
            raise LLMError(f"{purpose}: 无法运行 claude.exe：{e!r}") from e
        finally:
            with contextlib.suppress(OSError):
                os.unlink(system_file)
        try:
            out = json.loads(proc.stdout.decode("utf-8"))
        except ValueError as e:  # 含 UnicodeDecodeError / JSONDecodeError
            raise LLMError(f"{purpose}: claude 输出不是 JSON（exit={proc.returncode}）："
                           f"{_tail(proc.stderr) or _tail(proc.stdout)}") from e
        if not isinstance(out, dict):
            raise LLMError(f"{purpose}: claude 输出不是 JSON 对象（exit={proc.returncode}）")
        usage = out.get("usage") or {}
        tokens_in = sum(_int(usage.get(k)) for k in
                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.store.add_llm_usage(purpose, tokens_in, _int(usage.get("output_tokens")),
                                 float(out.get("total_cost_usd") or 0.0) * self.settings.usd_cny)
        structured = out.get("structured_output")
        if proc.returncode != 0 or out.get("is_error") or structured is None:
            raise LLMError(f"{purpose}: {out.get('subtype', '')} {str(out.get('result') or '')[:300]}")
        try:
            return schema.model_validate(structured)
        except ValidationError as e:
            raise LLMError(f"{purpose}: structured_output 不符合 {schema.__name__}：{e}") from e
