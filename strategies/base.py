# strategies/base.py
"""策略统一接口与加载。策略文件 strategies/<name>.py 必须定义模块级 STRATEGY。"""
import importlib
import re
from pathlib import Path
from typing import Protocol

from engine.dataview import DataView
from engine.types import Pick

NAME_RE = re.compile(r"[a-z][a-z0-9_]{2,40}")
RESERVED = {"base", "sandbox", "__init__"}
STRATEGY_DIR = Path(__file__).resolve().parent


class Strategy(Protocol):
    name: str

    def select(self, view: DataView) -> list[Pick]:
        """返回按优先级从高到低排序的推荐。"""
        ...


def _valid(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name)) and name not in RESERVED


def load_strategy(name: str):
    if not _valid(name):
        raise ValueError(f"非法策略名: {name!r}")
    return importlib.import_module(f"strategies.{name}").STRATEGY


def list_strategies() -> list[str]:
    return sorted(p.stem for p in STRATEGY_DIR.glob("*.py") if _valid(p.stem))
