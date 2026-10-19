from pathlib import Path

import pytest

from engine import integrity


def _tree(root: Path):
    # write_bytes：Windows 上 write_text 会把 \n 写成 \r\n，换行归一的测试就失去意义
    (root / "engine" / "sub").mkdir(parents=True)
    (root / "engine" / "a.py").write_bytes(b"x = 1\n")
    (root / "engine" / "sub" / "b.py").write_bytes(b"y = 2\n")
    (root / "engine" / "notes.txt").write_text("ignored", encoding="utf-8")
    (root / "strategies").mkdir()
    (root / "strategies" / "s.py").write_text("z = 3\n", encoding="utf-8")


def test_manifest_covers_only_locked_py(tmp_path):
    _tree(tmp_path)
    m = integrity.compute_manifest(tmp_path)
    assert set(m) == {"engine/a.py", "engine/sub/b.py"}
    (tmp_path / "engine" / "c.pyw").write_bytes(b"w = 1\n")  # Windows 上 .pyw 同样可被 import
    assert set(integrity.compute_manifest(tmp_path)) == {"engine/a.py", "engine/c.pyw", "engine/sub/b.py"}
    assert all(len(v) == 64 for v in m.values())


def test_verify_ok_then_detects_changes(tmp_path):
    _tree(tmp_path)
    assert integrity.verify(tmp_path) == [integrity.MANIFEST_PATH]  # 尚无清单
    integrity.write_manifest(tmp_path)
    assert (tmp_path / ".integrity" / "manifest.json").exists()
    assert integrity.verify(tmp_path) == []
    (tmp_path / "strategies" / "s.py").write_text("z = 4\n", encoding="utf-8")
    assert integrity.verify(tmp_path) == []  # 可改区不受约束
    (tmp_path / "engine" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "engine" / "new.py").write_text("", encoding="utf-8")
    (tmp_path / "engine" / "sub" / "b.py").unlink()
    assert integrity.verify(tmp_path) == ["engine/a.py", "engine/new.py", "engine/sub/b.py"]


def test_crlf_does_not_trigger(tmp_path):
    _tree(tmp_path)
    integrity.write_manifest(tmp_path)
    assert (tmp_path / "engine" / "a.py").read_bytes() == b"x = 1\n"
    (tmp_path / "engine" / "a.py").write_bytes(b"x = 1\r\n")
    assert integrity.verify(tmp_path) == []
    (tmp_path / "engine" / "a.py").write_bytes(b"x = 2\r\n")
    assert integrity.verify(tmp_path) == ["engine/a.py"]


@pytest.mark.parametrize("content", [b"{", b"[]", b'{"engine/a.py": 1}', b"\xff\xfe"])
def test_unreadable_manifest_fails_verify(tmp_path, content):
    _tree(tmp_path)
    integrity.write_manifest(tmp_path)
    (tmp_path / integrity.MANIFEST_PATH).write_bytes(content)
    assert integrity.verify(tmp_path) == [integrity.MANIFEST_PATH]


def test_manifest_with_bom_is_accepted(tmp_path):
    _tree(tmp_path)
    integrity.write_manifest(tmp_path)
    p = tmp_path / integrity.MANIFEST_PATH
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())  # 记事本另存为 UTF-8 会加 BOM
    assert integrity.verify(tmp_path) == []
