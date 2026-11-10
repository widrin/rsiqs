# agents/midday.py
"""午盘简报：持仓分析 + 早盘候选观察。只读，不改变早盘推荐、不产生成交。

撮合引擎只在收盘后按当日日线开盘价成交，午盘的任何"观察项"都不等于成交：这里既不写
recommendations 表，也不影响 settle。持仓的止损/止盈仍由 monitor 实时告警，简报只做汇总。
"""
from __future__ import annotations

import logging
import math
from datetime import date

import pandas as pd
from pydantic import BaseModel

from agents import market as market_agent
from agents.premarket import load_plan
from engine.rules import at_limit_up, limit_prices
from engine.store import Store

log = logging.getLogger(__name__)

ACCOUNT = "h:active"
MAX_WATCH = 3          # 观察项最多列这么几只，按早盘量化评分降序
COMMENT_TIMEOUT = 60.0

SYSTEM_PROMPT = """你是 A 股模拟盘的午盘观察助手。根据给定的持仓和早盘候选观察项，写一段不超过 120 字的午盘点评。

硬性要求：
- 只描述现状与风险（离止损/止盈的距离、板块强弱、观察项是否接近买点），不推翻早盘推荐，不给新的买入指令
- 不重复表格里已经有的具体数字，不复述价位
- 语气克制，不用感叹号，不给收益承诺"""


class MiddayComment(BaseModel):
    comment: str


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _spot_rows(spot: pd.DataFrame | None) -> dict:
    if spot is None or spot.empty:
        return {}
    return {str(r["code"]): r for r in spot.to_dict("records")}


def positions(acc, rows: dict, d: date) -> list[dict]:
    """h:active 持仓的盘中状态：现价、浮动盈亏、距止损/止盈的空间。取不到报价时价格字段为 None。"""
    if acc is None or not acc.positions:
        return []
    out = []
    for code in sorted(acc.positions):
        pos = acc.positions[code]
        row = rows.get(code) or {}
        price, prev = _f(row.get("last")), _f(row.get("prev_close"))
        item = {"code": code, "name": pos.name, "qty": pos.qty, "cost": pos.cost,
                "price": price or None, "stop": pos.stop, "target": pos.target,
                "exit_date": pos.exit_date, "days_left": (pos.exit_date - d).days,
                "day_ret": None, "pnl": None, "pnl_ret": None,
                "to_stop": None, "to_target": None}
        if price > 0:
            item["pnl"] = (price - pos.cost) * pos.qty
            item["pnl_ret"] = price / pos.cost - 1.0
            item["to_stop"] = pos.stop / price - 1.0
            item["to_target"] = pos.target / price - 1.0
            if prev > 0:
                item["day_ret"] = price / prev - 1.0
        out.append(item)
    return out


def watch(d: date, store: Store, rows: dict, held: set[str], recommended: set[str]) -> list[dict]:
    """早盘候选里现价回到买入区间的观察项（排除已推荐、已持仓、涨停）。

    早盘定稿时高开超买区被过滤掉的候选，午盘回踩到买入上限以下就是这里要提示的对象。
    早盘 LLM 计划存在时只听它的 keep：被 veto 的股票午盘不再翻出来，避免与早盘结论矛盾。"""
    loaded = store.load_candidates(d)
    if loaded is None:
        return []
    plan = load_plan(store, d, "active")
    keep = None
    if plan is not None:
        keep = {p.code.strip() for p in plan.picks if p.action == "keep"}
        if not keep:
            return []  # 早盘全部 veto：午盘不另外推荐
    out = []
    for p in loaded[0]:
        if p.code in held or p.code in recommended:
            continue
        if keep is not None and p.code not in keep:
            continue
        row = rows.get(p.code)
        if row is None:
            continue
        price, prev = _f(row.get("last")), _f(row.get("prev_close"))
        if price <= 0 or prev <= 0:
            continue  # 停牌或没有有效昨收
        up, _ = limit_prices(p.code, str(row.get("name") or p.name), prev, d)
        if at_limit_up(price, up) or not p.stop < price <= p.entry_max:
            continue
        out.append({"code": p.code, "name": p.name, "price": price, "entry_max": p.entry_max,
                    "stop": p.stop, "target": p.target, "hold_days": p.hold_days,
                    "score": float(p.score), "reason": p.reason, "day_ret": price / prev - 1.0})
    out.sort(key=lambda x: -x["score"])
    return out[:MAX_WATCH]


def _prompt(d: date, pos: list[dict], watch_items: list[dict], market_lines: list[str]) -> str:
    lines = [f"# 午盘 {d.isoformat()}", ""]
    if market_lines:
        lines += ["## 大盘", *market_lines, ""]
    lines.append(f"## h:active 持仓（{len(pos)} 只）")
    for p in pos:
        if p["price"] is None:
            lines.append(f"- {p['code']} {p['name']} {p['qty']} 股：现价缺失")
            continue
        day = f"，今日 {p['day_ret']:+.2%}" if p["day_ret"] is not None else ""
        lines.append(f"- {p['code']} {p['name']} {p['qty']} 股：成本 {p['cost']:.2f}，现价 {p['price']:.2f}"
                     f"，浮动 {p['pnl']:+,.0f} 元（{p['pnl_ret']:+.2%}）{day}"
                     f"，距止损 {p['to_stop']:+.2%}，距止盈 {p['to_target']:+.2%}"
                     f"，{p['exit_date'].isoformat()} 到期")
    if not pos:
        lines.append("（空仓）")
    lines += ["", f"## 早盘候选观察项（{len(watch_items)} 只，未推荐、现价已回到买入区间）"]
    for w in watch_items:
        lines.append(f"- {w['code']} {w['name']}：现价 {w['price']:.2f}（今日 {w['day_ret']:+.2%}）")
    if not watch_items:
        lines.append("（无）")
    lines += ["", "请写午盘点评。"]
    return "\n".join(lines)


def comment(llm, d: date, pos: list[dict], watch_items: list[dict], market_lines: list[str]) -> str:
    """LLM 点评；失败只记日志并返回空串，简报照发（规则部分不受影响）。"""
    try:
        out = llm.parse(purpose="midday", system=SYSTEM_PROMPT,
                        user=_prompt(d, pos, watch_items, market_lines),
                        schema=MiddayComment, timeout=COMMENT_TIMEOUT)
        return out.comment.strip()
    except Exception as e:  # noqa: BLE001 - 超时/未配置/校验失败：只影响点评段
        log.warning("midday comment failed: %r", e)
        return ""


def run_midday(d: date, store: Store, client, llm, notifier) -> None:
    """拉一次全市场快照，生成持仓分析 + 观察项 + LLM 点评并推送。"""
    from notify.format import format_midday_report

    acc = store.load_account(ACCOUNT)
    held = set(acc.positions) if acc else set()
    loaded = store.load_recommendation(d, ACCOUNT)
    recommended = {p.code for p in loaded[0]} if loaded else set()
    cands = store.load_candidates(d)
    codes = held | recommended | ({p.code for p in cands[0]} if cands else set())
    spot = None
    try:
        spot = client.spot() if client is not None else None  # 全市场快照：持仓、候选、涨跌家数一次拿全
    except Exception as e:  # noqa: BLE001 - 退化为按代码取报价
        log.warning("全市场快照失败，改为按代码取报价: %r", e)
    if spot is None and codes and client is not None:
        try:
            spot = client.spot(sorted(codes))
        except Exception as e:  # noqa: BLE001 - 没有报价也要发简报（价格缺失）
            log.warning("个股报价获取失败: %r", e)
    rows = _spot_rows(spot)
    pos = positions(acc, rows, d)
    watch_items = watch(d, store, rows, held, recommended)
    market = market_agent.intraday(client, spot if spot is not None and not spot.empty else None)
    market_lines = market_agent.summary_lines(market)
    text = comment(llm, d, pos, watch_items, market_lines)
    notifier.send_markdown(format_midday_report(d, ACCOUNT, pos, watch_items, market, text))
    log.info("midday %s: %d positions, %d watch", d, len(pos), len(watch_items))
