# notify/wecom.py
"""企业微信群机器人推送：按 UTF-8 字节分段、重试、失败落盘并在下一条补报。"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TEXT_LIMIT = 2000
MARKDOWN_LIMIT = 4000
ATTEMPTS = 3
WAIT = 2.0
FAIL_PREFIX = "⚠️ 上条推送失败，详见本地日志\n"


def _nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def split_utf8(text: str, limit: int) -> list[str]:
    """按行贪心装箱，每段 <= limit 字节；单行超长时按字符硬切（不会切断多字节字符）。"""
    pieces: list[str] = []
    for line in text.splitlines(keepends=True):
        while _nbytes(line) > limit:
            cut, size = 0, 0
            while size + _nbytes(line[cut]) <= limit:
                size += _nbytes(line[cut])
                cut += 1
            pieces.append(line[:cut])
            line = line[cut:]
        if line:
            pieces.append(line)
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and _nbytes(cur) + _nbytes(p) > limit:
            chunks.append(cur)
            cur = ""
        cur += p
    chunks.append(cur)
    return chunks


def _redact(s: str) -> str:
    return re.sub(r"key=[^&\s'\"]+", "key=***", s)


class WeCom:
    def __init__(self, url: str, session=None, log_dir: Path = Path("logs")):
        self.url = url
        self.session = session or requests.Session()
        self.log_dir = Path(log_dir)
        self.failed = False  # 上一条是否最终失败

    def send_text(self, content: str) -> bool:
        return self._send("text", content, TEXT_LIMIT)

    def send_markdown(self, content: str) -> bool:
        return self._send("markdown", content, MARKDOWN_LIMIT)

    def _send(self, msgtype: str, content: str, limit: int) -> bool:
        body = (FAIL_PREFIX + content) if self.failed else content
        for chunk in split_utf8(body, limit):
            err = self._post({"msgtype": msgtype, msgtype: {"content": chunk}})
            if err is not None:
                self._log_failure(msgtype, content, err)
                return False
        self.failed = False
        return True

    def _post(self, payload: dict) -> str | None:
        """成功返回 None，否则返回最后一次的错误描述。"""
        err = "unknown"
        for i in range(ATTEMPTS):
            try:
                r = self.session.post(self.url, json=payload, timeout=10)
                if r.status_code == 200 and r.json().get("errcode") == 0:
                    return None
                err = f"HTTP {r.status_code}: {r.json() if r.status_code == 200 else ''}"
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            log.warning("推送第 %d/%d 次失败: %s", i + 1, ATTEMPTS, _redact(err))
            if i < ATTEMPTS - 1:
                time.sleep(WAIT)
        return err

    def _log_failure(self, msgtype: str, content: str, err: str) -> None:
        self.failed = True
        self.log_dir.mkdir(parents=True, exist_ok=True)
        rec = {"time": datetime.now().isoformat(timespec="seconds"), "msgtype": msgtype,
               "content": content, "error": _redact(err)}
        with open(self.log_dir / "notify_failed.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class NullNotifier:
    """测试与 dry-run 用：不发网络请求，只记录消息。"""

    def __init__(self):
        self.sent: list[str] = []

    def send_text(self, content: str) -> bool:
        self.sent.append(content)
        return True

    def send_markdown(self, content: str) -> bool:
        self.sent.append(content)
        return True
