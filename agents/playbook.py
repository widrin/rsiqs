"""LLM 层经验手册：active（线上）与 candidate（影子）两份，复盘只改 candidate。"""
from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from engine.calendar import TradingCalendar
from engine.store import Store

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK_DIR = ROOT / "playbook"
KINDS = ("active", "candidate")
MAX_RULES = 30
MAX_CHARS = 6000
MIN_DAYS_BETWEEN_RELEASES = 10
MAX_RULE_CHARS = 300  # 单条规则的 text、evidence 各自的上限：一条超长规则不能挤掉其余规则
RELEASE_KEY = "playbook_released"
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _path(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown playbook kind: {kind}")
    return PLAYBOOK_DIR / f"{kind}.json"


def _valid(pb) -> bool:
    """version 是整数，rules 是列表，每条规则的 id、text、evidence 都是字符串。"""
    if not isinstance(pb, dict) or isinstance(pb.get("version", 1), bool) or not isinstance(pb.get("version", 1), int):
        return False
    rules = pb.get("rules", [])
    return isinstance(rules, list) and all(
        isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("id", "text", "evidence")) for r in rules)


def load_checked(kind: str) -> tuple[dict, bool]:
    """读取 playbook，返回 (playbook, 文件是否完好)。文件缺失算完好（返回空 playbook）；
    损坏（写到一半断电、手工编辑出错）时返回空 playbook 和 False。"""
    p = _path(kind)
    empty = {"version": 1, "created": dt.date.today().isoformat(), "rules": []}
    if not p.exists():
        return empty, True
    try:
        pb = json.loads(p.read_text(encoding="utf-8-sig"))  # 记事本、PowerShell 5.1 保存的文件带 BOM
    except (ValueError, OSError):
        pb = None
    if not _valid(pb):
        log.error("playbook %s 损坏：%s", kind, p)
        return empty, False
    return pb, True


def load(kind: str) -> dict:
    """读取 playbook；损坏时退回空 playbook：盘前照常运行，只是不带规则。
    会改写 playbook 的地方（发布、复盘）先用 corrupt_kinds() 检查，不能把空规则写回去。"""
    return load_checked(kind)[0]


def corrupt_kinds() -> list[str]:
    """文件存在但无法读取或格式不对的 playbook。"""
    return [k for k in KINDS if not load_checked(k)[1]]


def save(kind: str, pb: dict) -> None:
    """先写临时文件再原子替换：写到一半中断时，原文件保持完整。"""
    p = _path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(pb, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def load_prompt(name: str) -> str:
    return (PLAYBOOK_DIR / f"{name}_system.md").read_text(encoding="utf-8")


def render(pb: dict) -> str:
    if not pb.get("rules"):
        return "（暂无经验规则）"
    return "\n".join(f"{i}. [{r['id']}] {r['text']}（证据：{r['evidence']}）"
                     for i, r in enumerate(pb["rules"], 1))


def _next_id(rules: list[dict]) -> str:
    nums = [int(m.group(1)) for r in rules if (m := re.fullmatch(r"r(\d+)", r["id"]))]
    return f"r{max(nums, default=0) + 1}"


def _size(rules: list[dict]) -> int:
    return sum(len(r["text"]) + len(r["evidence"]) for r in rules)


def apply_lessons(pb: dict, lessons: list, today: dt.date) -> dict:
    """lesson 需有 action/rule_id/text/evidence 属性。返回新 dict，不改入参。"""
    out = copy.deepcopy(pb)
    rules: list[dict] = out.setdefault("rules", [])
    for les in lessons:
        evidence = (les.evidence or "").strip()[:MAX_RULE_CHARS]
        text = (les.text or "").strip()[:MAX_RULE_CHARS]
        if not evidence:
            continue
        idx = next((i for i, r in enumerate(rules) if r["id"] == les.rule_id), None)
        if les.action == "add" and text:
            rules.append({"id": _next_id(rules), "text": text, "evidence": evidence,
                          "created": today.isoformat()})
        elif les.action == "remove" and idx is not None:
            rules.pop(idx)
        elif les.action == "merge" and idx is not None and text:
            rules[idx] = {**rules[idx], "text": text, "evidence": evidence}
    while rules and (len(rules) > MAX_RULES or _size(rules) > MAX_CHARS):
        rules.pop(0)  # 列表按加入顺序排列，最前面的最旧
    return out


def _window_return(store: Store, account: str, start: dt.date, end: dt.date) -> float | None:
    nav = store.nav_series(account)
    if nav.empty:
        return None
    nav = nav.loc[pd.Timestamp(start):pd.Timestamp(end)]
    if len(nav) < 2:
        return None
    return float(nav.iloc[-1] / nav.iloc[0] - 1.0)


def _ran_without_playbook(store: Store, d: dt.date) -> bool:
    """d 日盘前有一路因为 playbook 读不出来而按空规则运行（推荐 meta 里 playbook_version 记为 None）。"""
    for kind in KINDS:
        rec = store.load_recommendation(d, f"h:{kind}")
        if rec and "playbook_version" in rec[1] and rec[1]["playbook_version"] is None:
            return True
    return False


def reset_window_if_degraded(store: Store, d: dt.date) -> bool:
    """任一份 playbook 损坏，或 d 日盘前有一路按空规则运行时，把发布窗口起点移到 d，返回 True。
    损坏期间盘前按空规则交易，这段净值不能用来比较两版规则；看盘前实际用的版本，不只看复盘时的文件：
    白天修好的那天，盘前用的仍是空规则。复盘回补较早的日子时也要逐日调用。"""
    if corrupt_kinds() or _ran_without_playbook(store, d):
        store.kv_set(RELEASE_KEY, d.isoformat())
        return True
    return False


def maybe_release(store: Store, today: dt.date, calendar: TradingCalendar) -> str | None:
    """每 MIN_DAYS_BETWEEN_RELEASES 个交易日最多发布一次 candidate。"""
    if reset_window_if_degraded(store, today):
        return None  # 任一份损坏都不发布也不重置：否则空规则会写进另一份并提交。由 run_review 告警
    last = store.kv_get(RELEASE_KEY)
    if last is None:
        store.kv_set(RELEASE_KEY, today.isoformat())
        return None
    start = dt.date.fromisoformat(last)
    if len(calendar.between(start, today)) - 1 < MIN_DAYS_BETWEEN_RELEASES:
        return None
    active, cand = load("active"), load("candidate")
    if cand.get("rules") == active.get("rules"):
        store.kv_set(RELEASE_KEY, today.isoformat())
        return None
    r_cand = _window_return(store, "h:candidate", start, today)
    r_act = _window_return(store, "h:active", start, today)
    if r_cand is None or r_act is None:
        return None  # 净值不足，继续观察
    store.kv_set(RELEASE_KEY, today.isoformat())
    if r_cand >= r_act:
        new = {"version": int(active.get("version", 1)) + 1, "created": today.isoformat(),
               "rules": copy.deepcopy(cand["rules"])}
        save("active", new)
        save("candidate", new)
        return (f"playbook v{new['version']} 上线：候选 {r_cand:+.2%} vs 现行 {r_act:+.2%}"
                f"（{start.isoformat()} 至 {today.isoformat()}），共 {len(new['rules'])} 条规则")
    save("candidate", copy.deepcopy(active))
    return (f"playbook 候选落后（{r_cand:+.2%} vs 现行 {r_act:+.2%}），已重置为现行 v{active.get('version', 1)}")


GIT_TIMEOUT = 60  # 秒：计划任务里没有人能输入口令，钩子或签名卡住时不能挡住日报
NO_HOOKS = ROOT / ".git-no-hooks"  # 不存在的目录：core.hooksPath 指向它，所有钩子都不跑


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":  # git.exe 可能已经起了子进程：连同子进程一起结束
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    proc.wait()


def _git(*args) -> tuple[int, str]:
    """运行一次 git：不签名、不跑任何钩子；输出写临时文件而不是管道（残留的子进程不会挡住等待），
    超时就结束整个进程树并抛 TimeoutExpired。"""
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={NO_HOOKS}", *args]
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=out, stdin=subprocess.DEVNULL)
        try:
            code = proc.wait(timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise
        out.seek(0)
        return code, out.read().decode("utf-8", "replace")


def git_commit(paths: list[str], message: str) -> bool | None:
    """git add + commit（附 trailer）。提交成功返回 True，没有变化返回 None，失败返回 False（记警告日志），
    不抛异常。调用方用 `is False` 判断失败：没有变化不是失败。"""
    try:
        code, text = _git("add", "--", *paths)
        if code == 0:
            code, text = _git("diff", "--cached", "--quiet", "--", *paths)
            if code == 0:
                return None  # 没有变化
            code, text = _git("commit", "-m", f"{message}\n\n{TRAILER}", "--", *paths)
        if code != 0:
            log.warning("git commit failed (exit %s): %s", code, text[-500:])
            return False
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("git commit failed: %s（如果留下了 .git/index.lock，确认没有 git 在运行后手动删除）", e)
        return False
