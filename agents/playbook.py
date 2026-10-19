"""LLM 层经验手册：active（线上）与 candidate（影子）两份，复盘只改 candidate。"""
from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import os
import re
import subprocess
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


def load(kind: str) -> dict:
    """读取 playbook；文件缺失或损坏（写到一半断电、手工编辑出错）时退回空 playbook 并记日志，
    盘前和复盘照常运行，只是不带规则。"""
    p = _path(kind)
    empty = {"version": 1, "created": dt.date.today().isoformat(), "rules": []}
    if not p.exists():
        return empty
    try:
        pb = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(pb, dict) or not isinstance(pb.get("rules", []), list):
            raise ValueError("playbook 格式不对")
        return pb
    except (ValueError, OSError):
        log.exception("playbook %s 损坏，按空 playbook 处理：%s", kind, p)
        return empty


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


def maybe_release(store: Store, today: dt.date, calendar: TradingCalendar) -> str | None:
    """每 MIN_DAYS_BETWEEN_RELEASES 个交易日最多发布一次 candidate。"""
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


def git_commit(paths: list[str], message: str) -> bool:
    """git add + commit（附 trailer）。失败或无变化返回 False，不抛异常。"""
    try:
        subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True, capture_output=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *paths], cwd=ROOT,
                                capture_output=True)
        if staged.returncode == 0:
            return False
        subprocess.run(["git", "commit", "-m", f"{message}\n\n{TRAILER}", "--", *paths], cwd=ROOT,
                       check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False
