# notify/wecom.py
"""企业微信群机器人推送：按 UTF-8 字节分段、重试、失败落盘并在下一条补报（跨进程）。"""
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
RATE_LIMIT_WAIT = 61.0  # 45009：每分钟 20 条的额度用完，等到下一个分钟窗口再发（只等一次）
FATAL_ERRCODES = {93000, 40008, 44004, 40058}  # webhook 无效、消息类型或内容非法：重试没有用
PENDING_FILE = "notify_pending"  # 有未补报的失败；每个计划任务是独立进程，状态必须落盘
# 不能在这些码点之前切：变体选择符、零宽连接符、组合字符（硬切时拆开 emoji 序列会显示成乱码）
_NO_CUT_BEFORE = {"\ufe0f", "\ufe0e", "\u200d", "\u20e3"}


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
            while cut > 1 and line[cut] in _NO_CUT_BEFORE:
                cut -= 1
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

    @property
    def failed(self) -> bool:
        """上一条推送（可能在另一个任务进程里）是否最终失败、还没补报。"""
        return (self.log_dir / PENDING_FILE).exists()

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
        (self.log_dir / PENDING_FILE).unlink(missing_ok=True)
        return True

    def _post(self, payload: dict) -> str | None:
        """成功返回 None，否则返回最后一次的错误描述。

        45009（频率限制）等到下一个分钟窗口再发一次；webhook 无效等错误不重试；读超时不重发：服务端可能已经
        收到并转发，重发会在群里出现两条。"""
        err, rate_limited = "unknown", False
        for i in range(ATTEMPTS):
            wait = WAIT
            try:
                r = self.session.post(self.url, json=payload, timeout=10)
                body = r.json() if r.status_code == 200 else {}
                code = body.get("errcode")
                if r.status_code == 200 and code == 0:
                    return None
                err = f"HTTP {r.status_code}: {body}"
                if code in FATAL_ERRCODES:
                    log.warning("推送失败且不可重试: %s", _redact(err))
                    return err
                if code == 45009 and not rate_limited:
                    rate_limited, wait = True, RATE_LIMIT_WAIT
            except requests.ReadTimeout as e:
                err = f"ReadTimeout（可能已送达，不重发）: {e}"
                log.warning("推送读超时，可能已送达，不重发: %s", _redact(str(e)))
                return err
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            log.warning("推送第 %d/%d 次失败: %s", i + 1, ATTEMPTS, _redact(err))
            if i < ATTEMPTS - 1:
                time.sleep(wait)
        return err

    def _log_failure(self, msgtype: str, content: str, err: str) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / PENDING_FILE).touch()
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
