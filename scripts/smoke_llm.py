"""手动冒烟：经 Claude Code CLI 真实调用一次 Claude（约 0.1~0.2 元），验证 claude.exe 查找、鉴权与结构化输出。
用法：uv run python scripts/smoke_llm.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from agents.llm import LLM, find_claude_exe  # noqa: E402
from config import get_settings  # noqa: E402
from engine.store import Store  # noqa: E402


class Ping(BaseModel):
    answer: str
    numbers: list[int]


def main() -> None:
    s = get_settings()
    if not (s.anthropic_api_key or (s.anthropic_base_url and s.anthropic_auth_token)):
        print("警告：.env 中既没有 ANTHROPIC_API_KEY，也没有 ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN")
    print("claude.exe:", find_claude_exe(s))
    store = Store(s.root / s.db_path)
    llm = LLM(s, store)
    t0 = time.monotonic()
    out = llm.parse("smoke", "你是连通性测试助手，严格按 JSON schema 输出。",
                    "answer 填“好”，numbers 按顺序列出 1 到 3。", Ping,
                    effort="low", timeout=120.0, max_budget_usd=0.2, essential=True)
    print(f"result: {out!r}（耗时 {time.monotonic() - t0:.1f} 秒）")
    print(f"month cost CNY: {llm.month_cost():.4f}")
    assert out.numbers == [1, 2, 3], out


if __name__ == "__main__":
    main()
