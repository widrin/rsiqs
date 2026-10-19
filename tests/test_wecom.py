# tests/test_wecom.py
import json

import pytest

import notify.wecom as wc
from notify.wecom import FAIL_PREFIX, NullNotifier, WeCom, split_utf8

URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET123"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = {"errcode": 0, "errmsg": "ok"} if body is None else body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, script=None):
        self.script = list(script or [])  # Resp 或 Exception；耗尽后返回成功
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        item = self.script.pop(0) if self.script else Resp()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(wc.time, "sleep", lambda s: None)


def make(tmp_path, script=None):
    s = FakeSession(script)
    return WeCom(URL, session=s, log_dir=tmp_path / "logs"), s


def test_split_utf8_respects_byte_limit_for_chinese():
    text = "涨" * 1000  # 3000 字节
    chunks = split_utf8(text, 2000)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-8")) <= 2000 for c in chunks)
    assert len(chunks) == 2


def test_split_utf8_prefers_newlines():
    text = "\n".join(["行" * 300] * 5)  # 每行 900 字节
    chunks = split_utf8(text, 2000)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-8")) <= 2000 for c in chunks)
    assert chunks[0].endswith("\n") and len(chunks) == 3
    assert split_utf8("", 10) == [""]


def test_send_text_payload_and_success(tmp_path):
    w, s = make(tmp_path)
    assert w.send_text("RSIQS 连通测试") is True
    assert s.posts == [{"msgtype": "text", "text": {"content": "RSIQS 连通测试"}}]


def test_send_text_chunks_long_chinese(tmp_path):
    w, s = make(tmp_path)
    assert w.send_text("股" * 1500) is True  # 4500 字节 -> 3 段
    assert len(s.posts) == 3
    assert all(len(p["text"]["content"].encode("utf-8")) <= 2000 for p in s.posts)


def test_send_markdown_payload_and_4000_limit(tmp_path):
    w, s = make(tmp_path)
    assert w.send_markdown("**粗**" + "字" * 2000) is True
    assert s.posts[0]["msgtype"] == "markdown"
    assert len(s.posts) == 2
    assert all(len(p["markdown"]["content"].encode("utf-8")) <= 4000 for p in s.posts)


def test_retry_then_success(tmp_path):
    w, s = make(tmp_path, [ConnectionError("x"), Resp(body={"errcode": 45009, "errmsg": "freq"})])
    assert w.send_text("hi") is True
    assert len(s.posts) == 3
    assert not (tmp_path / "logs" / "notify_failed.jsonl").exists()


def test_final_failure_logs_and_prefixes_next_message_once(tmp_path):
    w, s = make(tmp_path, [Resp(500), Resp(500), ConnectionError(f"bad {URL}")])
    assert w.send_text("第一条") is False
    log = tmp_path / "logs" / "notify_failed.jsonl"
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["msgtype"] == "text" and rec["content"] == "第一条"
    assert "SECRET123" not in log.read_text(encoding="utf-8")

    assert w.send_text("第二条") is True
    assert s.posts[-1]["text"]["content"] == FAIL_PREFIX + "第二条"
    assert w.send_text("第三条") is True
    assert s.posts[-1]["text"]["content"] == "第三条"


def test_null_notifier_records():
    n = NullNotifier()
    assert n.send_text("a") is True
    assert n.send_markdown("**b**") is True
    assert n.sent == ["a", "**b**"]
