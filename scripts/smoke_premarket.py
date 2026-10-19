"""手动冒烟：手选 3 只股票作为候选，走一遍 analyze（真实 Claude 深度分析）和 finalize（真实竞价快照 + 竞价确认）。
运行：uv run python scripts/smoke_premarket.py [HH:MM]
可选参数 HH:MM：analyze 完成后等到当天该时刻再 finalize（交易日 09:15 前启动并传 09:26，可验证真实竞价数据）。
结果写入临时 sqlite，不影响正式库，也不推送。
"""
import datetime as dt
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import premarket  # noqa: E402
from agents.llm import LLM  # noqa: E402
from config import get_settings  # noqa: E402
from data.akshare_client import AkClient  # noqa: E402
from engine.store import Store  # noqa: E402
from engine.types import Pick  # noqa: E402

CODES = ("600519", "000001", "300750")


def main() -> None:
    s = get_settings()
    store = Store(Path(tempfile.mkdtemp()) / "smoke.sqlite")
    client, llm, d = AkClient(), LLM(s, store), dt.date.today()
    spot = client.spot().set_index("code")
    picks, stats = [], {}
    for code in CODES:
        pc = float(spot.loc[code, "prev_close"])
        picks.append(Pick(code=code, name=str(spot.loc[code, "name"]), score=1.0, entry_max=round(pc * 1.03, 2),
                          stop=round(pc * 0.93, 2), target=round(pc * 1.10, 2), hold_days=5, reason="冒烟测试候选"))
        stats[code] = {"prev_close": pc}
    ctx = {"news": client.news_global(10), "notices": [], "stats": stats,
           "stock_news": {p.code: client.stock_news(p.code, limit=3) for p in picks}}
    store.save_candidates(d, picks, json.loads(json.dumps(ctx, ensure_ascii=False, default=str)))

    t0 = time.monotonic()
    plan = premarket.analyze(d, s, store, llm, "active")
    print(f"analyze 用时 {time.monotonic() - t0:.0f}s")
    assert plan is not None, "深度分析失败，原因见 stderr 中的 'premarket analyze active failed' 日志"
    print("market_view:", plan.market_view)
    for x in plan.picks:
        print(" ", x)

    if len(sys.argv) > 1:
        wait = (dt.datetime.combine(d, dt.time.fromisoformat(sys.argv[1])) - dt.datetime.now()).total_seconds()
        if wait > 0:
            print(f"等待 {wait:.0f}s，到 {sys.argv[1]} 再定稿")
            time.sleep(wait)

    t0 = time.monotonic()
    out, reviewed, view = premarket.finalize(d, s, store, llm, client, "active")  # spot=None：现拉竞价快照
    print(f"finalize 用时 {time.monotonic() - t0:.0f}s，llm_reviewed={reviewed}")
    print("market_view:", view)
    for p in out:
        print(" ", p)
    print(f"本次花费约 {llm.month_cost():.3f} 元")


if __name__ == "__main__":
    main()
