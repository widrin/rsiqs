"""手动冒烟：直连火山方舟真实调用一次大模型，验证 Key、模型配置与结构化输出。
用法：uv run python scripts/smoke_llm.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from agents.llm import LLM  # noqa: E402
from config import get_settings  # noqa: E402
from engine.store import Store  # noqa: E402


class Ping(BaseModel):
    answer: str
    numbers: list[int]


def main() -> None:
    s = get_settings()
    if not s.ark_api_key:
        raise SystemExit("未配置 ARK_API_KEY：复制 .env.example 为 .env，填入火山方舟 API Key")
    print("model:", s.ark_model)
    store = Store(s.root / s.db_path)
    llm = LLM(s, store)
    t0 = time.monotonic()
    out = llm.parse("smoke", "你是连通性测试助手。", "answer 填“好”，numbers 按顺序列出 1 到 3。",
                    Ping, timeout=120.0)
    print(f"result: {out!r}（耗时 {time.monotonic() - t0:.1f} 秒）")
    assert out.numbers == [1, 2, 3], out


if __name__ == "__main__":
    main()
