"""研究循环：Claude 每轮返回分析和策略草稿（结构化输出，没有任何工具）。

Claude 只能返回代码文本。命名规则、import 白名单（strategies.sandbox.check_source）、编译、
写文件、子进程评估、评估次数上限、留出集只回传通过/不通过、会话前后的 engine/ 哈希校验和
月度预算，全部由本模块的 Python 代码执行。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tokenize
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE, SPLITS
from engine.store import Store
from strategies.base import NAME_RE, RESERVED
from strategies.sandbox import ALLOWED_IMPORTS, check_source

MAX_ROUNDS = 3
MAX_DRAFTS_PER_ROUND = 3
MAX_EVALS_PER_SESSION = 5
EVAL_TIMEOUT = 1800  # 基线策略全量评估实测约 7 分钟
CONTEXT_MAX_CHARS = 60_000       # build_context 总长度上限（字符）
FILE_MAX_CHARS = 15_000          # 背景里单个源码文件的上限
TRIALS_IN_CONTEXT = 20
BASELINE = "baseline_momo"
INTERFACE_FILES = (
    ("strategies/base.py", "策略接口"),
    ("engine/types.py", "Pick 等数据类型"),
    ("engine/dataview.py", "DataView：策略唯一的数据入口"),
    ("engine/rules.py", "A 股交易规则工具，可以 import"),
)
STATUS_CN = {"incumbent": "现任", "shadow": "影子盘", "candidate": "候选", "retired": "已退役"}


class Draft(BaseModel):
    name: str
    hypothesis: str
    code: str


class ResearchRound(BaseModel):
    analysis: str
    drafts: list[Draft]
    done: bool


def _strip_comments(src: str) -> str:
    """去掉 # 注释（保留 docstring 与字符串里的 #）；无法分词时原样返回。"""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return src
    lines = src.split("\n")
    drop: set[int] = set()
    for t in toks:
        if t.type == tokenize.COMMENT:
            row, col = t.start[0] - 1, t.start[1]
            head = lines[row][:col].rstrip()
            lines[row] = head
            if not head:
                drop.add(row)
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


def _source_block(root: Path, rel: str, title: str) -> str:
    p = root / rel
    text = _strip_comments(p.read_text(encoding="utf-8")) if p.is_file() else "（文件不存在）"
    if len(text) > FILE_MAX_CHARS:
        text = text[:FILE_MAX_CHARS] + "\n# ...（已截断）"
    return f"### {rel}（{title}）\n```python\n{text.rstrip()}\n```"


def _hypothesis(store: Store, name: str) -> str:
    return str(store.kv_get(f"research:hypothesis:{name}", "") or "")


def _trial_line(store: Store, t: dict) -> str:
    def f(x):
        return "?" if x is None else f"{x:.2f}"

    ho = {True: "通过", False: "未通过", None: "未运行"}[t["holdout_pass"]]
    hyp = _hypothesis(store, t["strategy"])[:120]
    return (f"- {t['ts'][:10]} {t['strategy']}：train 夏普 {f(t['train_sharpe'])}，val 夏普 "
            f"{f(t['val_sharpe'])}，门槛{'达标' if t['gate_pass'] else '未达标'}，holdout {ho}"
            + (f"；假设：{hyp}" if hyp else ""))


def _status_lines(store: Store) -> list[str]:
    out = []
    for st, cn in STATUS_CN.items():
        names = store.strategies_with_status(st)
        shown = "、".join(names[:50]) + (f" 等 {len(names)} 个" if len(names) > 50 else "")
        out.append(f"- {cn}（{st}）：{shown or '无'}")
    return out


def build_context(settings: Settings, store: Store) -> str:
    """每轮共用的背景：会话限制、门槛、可用模块、策略状态、最近试验、接口与策略源码。

    短而关键的部分在前，源码在后；超过 CONTEXT_MAX_CHARS 时从尾部截断。"""
    root = Path(settings.root)
    splits = {k: [a.isoformat(), b.isoformat() if b else "最新"] for k, (a, b) in SPLITS.items()}
    trials = store.recent_trials(TRIALS_IN_CONTEXT)
    head = [
        f"# 本次研究会话（{date.today().isoformat()}）",
        "",
        "## 会话限制",
        f"- 最多 {MAX_ROUNDS} 轮；每轮最多处理 {MAX_DRAFTS_PER_ROUND} 个草稿；整个会话最多评估 "
        f"{MAX_EVALS_PER_SESSION} 次；单次评估限时 {EVAL_TIMEOUT} 秒。",
        "",
        "## 数据切分与门槛",
        f"- 切分 SPLITS：{json.dumps(splits, ensure_ascii=False)}",
        f"- 回测门槛 GATE（全部为 >= 比较；val_max_dd 为负数）：{json.dumps(GATE)}",
        f"- holdout 门槛 HOLDOUT_GATE：{json.dumps(HOLDOUT_GATE)}。holdout 只返回 holdout_pass（通过/不通过）。",
        f"- 历史试验总数 n_trials：{store.trial_count()}（下一次评估计为第 {store.trial_count() + 1} 次）",
        "",
        "## 策略代码可以 import 的模块（含其子模块）",
        "- " + ", ".join(sorted(ALLOWED_IMPORTS)),
        "",
        "## 现有策略（新草稿不能再用这些名字）",
        *_status_lines(store),
        "",
        f"## 最近 {TRIALS_IN_CONTEXT} 次试验（新到旧）",
        *([_trial_line(store, t) for t in trials] or ["- 暂无"]),
        "",
        "## 接口与示例源码（已去掉 # 注释）",
    ]
    blocks = [_source_block(root, rel, title) for rel, title in INTERFACE_FILES]
    incumbents = [n for n in store.strategies_with_status("incumbent") if NAME_RE.fullmatch(n)]
    for name in incumbents:
        if name != BASELINE:
            blocks.append(_source_block(root, f"strategies/{name}.py", "当前现任策略"))
    baseline_title = "基线策略，当前现任" if BASELINE in incumbents else "基线策略，写法示例"
    blocks.append(_source_block(root, f"strategies/{BASELINE}.py", baseline_title))
    text = "\n".join(head) + "\n\n" + "\n\n".join(blocks)
    if len(text) > CONTEXT_MAX_CHARS:
        text = text[:CONTEXT_MAX_CHARS - 20] + "\n...（背景已截断）"
    return text


def submit_draft(settings: Settings, store: Store, draft: Draft) -> tuple[bool, str]:
    """校验并保存一个草稿。成功返回 (True, 说明)；失败返回 (False, 原因)，原因会原样反馈给模型。

    规则：名字匹配 NAME_RE 且不是保留名；不是现任；是新名字（任何已有状态或已有文件都不覆盖，
    保证每个试验记录对应唯一一份代码）；compile() 通过；check_source() 无违规。"""
    name, code = draft.name.strip(), draft.code
    if not NAME_RE.fullmatch(name) or name in RESERVED:
        return False, (f"策略名 {name!r} 不合规：须匹配 ^[a-z][a-z0-9_]{{2,40}}$，"
                       "且不能是 strategies/ 下的保留模块名")
    if name in store.strategies_with_status("incumbent"):
        return False, f"{name} 是当前现任策略，不能覆盖；请换一个新名字"
    path = Path(settings.root) / "strategies" / f"{name}.py"
    status = store.get_status(name)
    if status is not None or path.exists():
        return False, f"策略名 {name} 已被使用（{STATUS_CN.get(status, '已有同名文件')}）；每个草稿都要用新名字"
    try:
        compile(code, f"strategies/{name}.py", "exec", dont_inherit=True)
    except SyntaxError as e:
        return False, f"语法错误（第 {e.lineno} 行）：{e.msg}"
    except (ValueError, RecursionError, MemoryError) as e:
        return False, f"源码无法编译：{type(e).__name__}: {e}"
    problems = check_source(code)
    if problems:
        return False, "代码规范检查未通过：" + "；".join(problems)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8", newline="\n")
    store.set_status(name, "candidate", date.today())
    store.kv_set(f"research:hypothesis:{name}", draft.hypothesis.strip())
    return True, f"已写入 strategies/{name}.py（状态 candidate）"


def _last_json_line(stdout: str) -> dict | None:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def evaluate_strategy(settings: Settings, store: Store, name: str, runner=None) -> dict:
    """在子进程中运行 engine.run_eval；本次写入数据库的试验记录 gate_pass 且 holdout_pass 时设为 shadow。

    策略代码和评估器在同一个子进程里，stdout 可能被伪造，所以报告只用于展示，是否通过以子进程写入的
    试验记录为准。返回 {"ok": bool, "report": dict | None, "error": str}；任何失败都不抛异常。"""
    run = runner or subprocess.run
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # Windows 管道默认是 ANSI 代码页
    before = store.trial_count(name)
    try:
        proc = run([sys.executable, "-m", "engine.run_eval", name], cwd=str(settings.root),
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=env, timeout=EVAL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "report": None, "error": f"评估超时（{EVAL_TIMEOUT} 秒），请简化计算"}
    except OSError as e:
        return {"ok": False, "report": None, "error": f"无法启动评估进程：{e}"}
    report = _last_json_line(proc.stdout)
    if proc.returncode != 0 or report is None or "error" in report or "gate_pass" not in report:
        if report and report.get("error"):
            detail = str(report["error"])
        else:
            detail = (proc.stderr or proc.stdout or "无输出").strip()[-1500:]
        return {"ok": False, "report": None, "error": f"评估失败（退出码 {proc.returncode}）：{detail}"}
    trial = store.last_trial(name) if store.trial_count(name) > before else None
    if trial is None:
        return {"ok": False, "report": None, "error": "评估没有写入试验记录，报告不予采信"}
    report = {**report, "gate_pass": trial["gate_pass"], "holdout_pass": trial["holdout_pass"]}
    if trial["gate_pass"] and trial["holdout_pass"]:
        store.set_status(name, "shadow", date.today())
    return {"ok": True, "report": report, "error": ""}
