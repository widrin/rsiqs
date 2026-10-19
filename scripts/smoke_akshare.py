# scripts/smoke_akshare.py
"""手工冒烟：uv run python scripts/smoke_akshare.py  （需联网，不在 pytest 中运行）"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import akshare as ak  # noqa: E402

from data.akshare_client import AkClient  # noqa: E402


def main() -> int:
    c = AkClient(attempts=2, wait=1.0)
    end = date.today()
    start = end - timedelta(days=20)
    checks = {
        "trade_calendar": lambda: c.trade_calendar()[-3:],
        "daily_bars": lambda: c.daily_bars("600000", start, end).tail(3),
        "raw_bars": lambda: c.raw_bars("600000", start, end).tail(3),
        "index_daily": lambda: c.index_daily("sh000300").tail(3),
        "dividends": lambda: c.dividends("600000").tail(3),
        "universe": lambda: (len(u := c.universe()), u[:5]),
        "spot(em)": lambda: c.spot().head(3),
        "spot(sina raw)": lambda: ak.stock_zh_a_spot().head(3),
        "news_global": lambda: c.news_global(3),
        "stock_news": lambda: c.stock_news("600000", 2),
        "notices": lambda: c.notices(end - timedelta(days=1)).head(3),
    }
    failed = 0
    for name, fn in checks.items():
        try:
            print(f"=== {name} OK ===\n{fn()}\n")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"=== {name} FAILED: {type(e).__name__}: {e} ===\n")
    print(f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
