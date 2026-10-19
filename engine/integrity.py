"""锁定区哈希校验：engine/ 下所有 .py / .pyw 的 sha256 清单。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCKED_DIRS = ["engine"]
SUFFIXES = (".py", ".pyw")  # Windows 上 .pyw 同样能被 import
MANIFEST_PATH = ".integrity/manifest.json"


def _digest(path: Path) -> str:
    # 归一换行，避免 Windows autocrlf 造成误报
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def compute_manifest(root: Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for d in LOCKED_DIRS:
        for p in sorted((root / d).rglob("*")):
            if p.is_file() and p.suffix.lower() in SUFFIXES:
                out[p.relative_to(root).as_posix()] = _digest(p)
    return out


def write_manifest(root: Path) -> None:
    path = Path(root) / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compute_manifest(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load(path: Path) -> dict[str, str] | None:
    """读取清单；缺失、无法解析或格式不对时返回 None。utf-8-sig 兼容记事本加的 BOM。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):  # 含 UnicodeDecodeError、JSONDecodeError
        return None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        return None
    return data


def verify(root: Path) -> list[str]:
    """返回与清单不符的文件；清单缺失或损坏时返回 [MANIFEST_PATH]（视为不通过）。"""
    expected = _load(Path(root) / MANIFEST_PATH)
    if expected is None:
        return [MANIFEST_PATH]
    actual = compute_manifest(root)
    return sorted(k for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k))
