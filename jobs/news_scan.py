"""08:35 消息面影子扫描（仅交易日）。python -m jobs.news_scan

新闻选股写入独立的影子账户 h:news：只记录净值和日志，不推送，也不影响 h:active / h:candidate 的盘前流程。
前向跑满 20 个交易日后，用净值与 h:active、bench:csi300 对比再决定是否值得转正。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from agents import news
from agents.premarket import HISTORY_DAYS
from engine.dataview import DataView
from jobs import common

ACCOUNT = "h:news"


def scan(d: date, settings, store, client, cache, calendar) -> list:
    """采集新闻、LLM 选股、按硬过滤生成推荐并保存；没有选出来也保存空推荐（照常结算持仓）。"""
    asof = calendar.prev_day(d)
    codes = client.universe()
    cache.update(codes, asof)
    panel, names = cache.load_panel(codes, asof - timedelta(days=HISTORY_DAYS), asof)
    view = DataView(panel, names, asof)
    picks, meta = news.select(common.make_llm(settings, store), client, d, view, codes)
    store.save_recommendation(d, ACCOUNT, picks, meta)
    common.log.info("news_scan %s: news %d, llm codes %s, picks %s", d, meta["news_count"],
                    meta["raw_codes"], [p.code for p in picks])
    return picks


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("news_scan %s: not a trading day", d)
        return False
    return common.run_job("news_scan", d, lambda: scan(d, settings, store, client, cache, calendar),
                          store, notifier)


if __name__ == "__main__":
    main()
