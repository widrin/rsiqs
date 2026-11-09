"""收盘后的大盘快照：指数收盘与涨跌幅、涨跌家数、成交额、行业板块强弱。

数据按交易日缓存在 kv：复盘任务最多重试 9 次，命中缓存后不再重复拉全市场行情。
只有指数、家数、板块三块都拿到才缓存；临时失败留到下一次重试补全，不把半份数据固化。
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

log = logging.getLogger(__name__)

INDEX_SYMBOLS = (("沪深300", "sh000300"), ("上证指数", "sh000001"))
TOP_SECTORS = 5
BENCH_NAME = "沪深300"


def _indexes(cache, client, d: dt.date) -> list[dict]:
    out = []
    for name, symbol in INDEX_SYMBOLS:
        try:
            series = cache.load_index(symbol).dropna()
        except Exception as e:  # noqa: BLE001
            log.warning("指数 %s 读取失败: %s", symbol, e)
            series = pd.Series(dtype=float)
        series.index = pd.to_datetime(series.index).normalize()
        ts = pd.Timestamp(d)
        if (series.empty or ts not in series.index) and client is not None:
            try:
                series = client.index_daily(symbol).dropna()
                cache.save_index(symbol, series)
                series.index = pd.to_datetime(series.index).normalize()
            except Exception as e:  # noqa: BLE001
                log.warning("指数 %s 获取失败: %s", symbol, e)
        if series.empty or ts not in series.index:
            continue
        i = series.index.get_loc(ts)
        close = float(series.iloc[i])
        pct = None
        if i > 0 and float(series.iloc[i - 1]) > 0:
            pct = close / float(series.iloc[i - 1]) - 1.0
        out.append({"name": name, "symbol": symbol, "close": close, "pct": pct})
    return out


def _breadth(client) -> dict | None:
    spot = client.spot()
    if spot is None or spot.empty or "pct" not in spot.columns:
        return None
    pct = pd.to_numeric(spot["pct"], errors="coerce").dropna()
    if pct.empty:
        return None
    amount = pd.to_numeric(spot.get("amount"), errors="coerce").dropna() \
        if "amount" in spot.columns else pd.Series(dtype=float)
    return {"up": int((pct > 0).sum()), "down": int((pct < 0).sum()),
            "flat": int((pct == 0).sum()),
            "amount": float(amount.sum()) / 1e8 if not amount.empty else None}


def _sectors(client) -> dict | None:
    df = client.industry_boards()
    if df is None or df.empty or "pct" not in df.columns:
        return None
    df = df.dropna(subset=["pct"]).sort_values("pct", ascending=False)
    if df.empty:
        return None
    rec = lambda r: {"board": str(r["board"]), "pct": float(r["pct"])}  # noqa: E731

    return {"top": [rec(r) for _, r in df.head(TOP_SECTORS).iterrows()],
            "bottom": [rec(r) for _, r in df.tail(TOP_SECTORS).sort_values("pct").iterrows()]}


def collect(store, cache, client, d: dt.date) -> dict | None:
    """当日大盘快照；取不到任何数据时返回 None。client 为 None（回补/测试）时不联网。"""
    key = f"market:{d.isoformat()}"
    cached = store.kv_get(key)
    if isinstance(cached, dict) and cached.get("complete"):
        return cached
    if client is None:
        return None
    indexes = _indexes(cache, client, d)
    breadth = _safe(_breadth, client, "涨跌家数")
    sectors = _safe(_sectors, client, "行业板块")
    if not indexes and not breadth and not sectors:
        return None
    data = {"indexes": indexes, "breadth": breadth, "sectors": sectors,
            "complete": bool(indexes and breadth and sectors)}
    if data["complete"]:
        store.kv_set(key, data)
    return data


def _safe(fn, client, what: str):
    try:
        return fn(client)
    except Exception as e:  # noqa: BLE001 - 任一块失败都只影响该段，不能让日报失败
        log.warning("%s获取失败: %s", what, e)
        return None


def bench_pct(market: dict | None) -> float | None:
    """沪深300 当日涨跌幅（小数），用于算账户超额。"""
    if not market:
        return None
    for item in market.get("indexes") or []:
        if item.get("name") == BENCH_NAME and item.get("pct") is not None:
            return float(item["pct"])
    return None


def summary_lines(market: dict | None) -> list[str]:
    """大盘的文字摘要（不含标题），日报与 LLM 复盘输入共用。"""
    if not market:
        return []
    lines = []
    idx = [i for i in market.get("indexes") or [] if i.get("pct") is not None]
    if idx:
        lines.append("；".join(f"{i['name']} {i['close']:,.2f}（{i['pct']:+.2%}）" for i in idx))
    b = market.get("breadth")
    if b:
        amount = f"，成交额 {b['amount']:,.0f} 亿" if b.get("amount") is not None else ""
        lines.append(f"涨跌家数：上涨 {b['up']} / 下跌 {b['down']} / 平盘 {b['flat']}{amount}")
    s = market.get("sectors")
    if s and s.get("top"):
        lines.append("板块领涨：" + "、".join(f"{x['board']} {x['pct']:+.2f}%" for x in s["top"]))
    if s and s.get("bottom"):
        lines.append("板块领跌：" + "、".join(f"{x['board']} {x['pct']:+.2f}%" for x in s["bottom"]))
    return lines
