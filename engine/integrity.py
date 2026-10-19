"""锁定区哈希校验：engine/ 下所有 .py 的 sha256 清单。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCKED_DIRS = ["engine"]
MANIFEST_PATH = ".integrity/manifest.json"


def _digest(path: Path) -> str:
    # 归一换行，避免 Windows autocrlf 造成误报
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def compute_manifest(root: Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for d in LOCKED_DIRS:
        for p in sorted((root / d).rglob("*.py")):
            out[p.relative_to(root).as_posix()] = _digest(p)
    return out


def write_manifest(root: Path) -> None:
    path = Path(root) / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compute_manifest(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify(root: Path) -> list[str]:
    path = Path(root) / MANIFEST_PATH
    if not path.exists():
        return [MANIFEST_PATH]
    expected: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    actual = compute_manifest(root)
    return sorted(k for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k))
