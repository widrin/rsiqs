from pathlib import Path

from engine import integrity


def _tree(root: Path):
    (root / "engine" / "sub").mkdir(parents=True)
    (root / "engine" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "engine" / "sub" / "b.py").write_text("y = 2\n", encoding="utf-8")
    (root / "engine" / "notes.txt").write_text("ignored", encoding="utf-8")
    (root / "strategies").mkdir()
    (root / "strategies" / "s.py").write_text("z = 3\n", encoding="utf-8")


def test_manifest_covers_only_locked_py(tmp_path):
    _tree(tmp_path)
    m = integrity.compute_manifest(tmp_path)
    assert set(m) == {"engine/a.py", "engine/sub/b.py"}
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
    (tmp_path / "engine" / "a.py").write_bytes(b"x = 1\r\n")
    assert integrity.verify(tmp_path) == []
