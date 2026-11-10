# notify/format.py
"""推送消息模板。"""
from datetime import date

from agents.market import bench_pct, summary_lines
from engine.types import Pick

DISCLAIMER = "> 模拟盘信号，仅供研究，不构成投资建议。"


def _one_line(text: str) -> str:
    """LLM 写的理由和市场观点可能含换行：合并成一行，免得后面的行跳出引用、被渲染成标题或列表。"""
    return "；".join(part.strip() for part in str(text).splitlines() if part.strip())


def format_recommendation(d: date, picks: list[Pick], llm_reviewed: bool, market_view: str = "") -> str:
    lines = [f"## RSIQS 盘前推荐 {d.isoformat()}"]
    if not llm_reviewed:
        lines.append("<font color=\"warning\">未经 LLM 审核（已退回纯量化推荐）</font>")
    if market_view:
        lines.append(f"**市场观点**：{_one_line(market_view)}")
    if not picks:
        lines.append("今日无推荐。")
    for i, p in enumerate(picks, 1):
        lines.append(f"**{i}. {p.code} {p.name}**  买入 ≤{p.entry_max:.2f} | 止损 {p.stop:.2f} | "
                     f"止盈 {p.target:.2f} | 持有 {p.hold_days} 日")
        if p.reason:
            lines.append(f"> {_one_line(p.reason)}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_daily_report(d: date, rows: list[dict], review_summary: str,
                        holdings: list[dict] | None = None,
                        market: dict | None = None) -> str:
    # 企业微信群机器人的 markdown（v1）不渲染表格：每个账户一行
    lines = [f"## RSIQS 日报 {d.isoformat()}"]
    bench = bench_pct(market)
    for r in rows:
        extra = ""
        if bench is not None and r.get("today") and not r["account"].startswith("bench:"):
            extra = f"，超额 {r['day_ret'] - bench:+.2%}"
        lines.append(f"**{r['account']}** 净值 {r['equity']:,.2f}，当日 {r['day_ret']:+.2%}，累计 {r['total_ret']:+.2%}{extra}")
    market_lines = summary_lines(market)
    if market_lines:
        lines.append("### 大盘")
        lines += market_lines
    if holdings is not None:
        lines.append("### 收盘持仓收益")
        if not holdings:
            lines.append("（空仓）")
        for account in dict.fromkeys(h["account"] for h in holdings):
            part = [h for h in holdings if h["account"] == account]
            priced = [h for h in part if h["price"] is not None]
            if priced:
                pnl = sum(h["pnl"] for h in priced)
                basis = sum(h["cost"] * h["qty"] for h in priced)
                lines.append(f"**{account}** 浮动盈亏 {pnl:+,.0f} 元（{pnl / basis:+.2%}）")
                day_pnls = [h.get("day_pnl") for h in priced if h.get("day_pnl") is not None]
                if day_pnls:
                    lines.append(f"今日持仓盈亏 {sum(day_pnls):+,.0f} 元")
            else:
                lines.append(f"**{account}**")
            for h in part:
                if h["price"] is None:
                    lines.append(f"- {h['code']} {h['name']} {h['qty']} 股：成本 {h['cost']:.2f}，收盘价缺失")
                    continue
                day = ""
                if h.get("day_pnl") is not None:
                    day = (f"，今日 {h['day_pnl']:+,.0f} 元（{h['day_pnl_ret']:+.2%}）")
                elif h.get("day_ret") is not None:
                    day = f"，今日 {h['day_ret']:+.2%}"
                lines.append(f"- {h['code']} {h['name']} {h['qty']} 股：成本 {h['cost']:.2f} → "
                             f"收盘 {h['price']:.2f}，浮动 {h['pnl']:+,.0f} 元（{h['pnl_ret']:+.2%}）{day}")
    lines.append("**复盘**：" + (_one_line(review_summary) or "（本日无 LLM 复盘）"))
    return "\n".join(lines)


def format_midday_report(d: date, account: str, positions: list[dict], status: list[dict],
                         watch: list[dict], market: dict | None, comment: str) -> str:
    """午盘简报：持仓汇总 + 早盘候选观察。明确标注观察项不会成交，避免与早盘推荐混淆。"""
    lines = [f"## RSIQS 午盘简报 {d.isoformat()}"]
    market_lines = summary_lines(market)
    if market_lines:
        lines.append("### 大盘")
        lines += market_lines
    lines.append(f"### 持仓 {account}")
    priced = [p for p in positions if p["price"] is not None]
    if not positions:
        lines.append("（空仓）")
    else:
        if priced:
            pnl = sum(p["pnl"] for p in priced)
            basis = sum(p["cost"] * p["qty"] for p in priced)
            day = sum(p["price"] * p["day_ret"] * p["qty"] for p in priced if p["day_ret"] is not None)
            lines.append(f"**浮动盈亏 {pnl:+,.0f} 元（{pnl / basis:+.2%}），今日 {day:+,.0f} 元**")
        for p in positions:
            if p["price"] is None:
                lines.append(f"- {p['code']} {p['name']} {p['qty']} 股：成本 {p['cost']:.2f}，现价缺失")
                continue
            day = f"，今日 {p['day_ret']:+.2%}" if p["day_ret"] is not None else ""
            lines.append(f"- {p['code']} {p['name']} {p['qty']} 股：成本 {p['cost']:.2f} → "
                         f"现价 {p['price']:.2f}，浮动 {p['pnl']:+,.0f} 元（{p['pnl_ret']:+.2%}）{day}；"
                         f"距止损 {p['to_stop']:+.2%}，距止盈 {p['to_target']:+.2%}，"
                         f"{p['exit_date'].isoformat()} 到期")
    if status:
        lines.append("### 早盘推荐执行（未在持仓的）")
        for s in status:
            price = f"，现价 {s['price']:.2f}" if s["price"] else ""
            lines.append(f"- {s['code']} {s['name']}（买入上限 ≤{s['entry_max']:.2f}）："
                         f"{s['why']}{price}")
    lines.append("### 早盘候选观察（不改变早盘推荐，不会自动成交）")
    if not watch:
        lines.append("（无）")
    for w in watch:
        lines.append(f"- {w['code']} {w['name']}：现价 {w['price']:.2f}（今日 {w['day_ret']:+.2%}），"
                     f"买入上限 ≤{w['entry_max']:.2f}，止损 {w['stop']:.2f}，止盈 {w['target']:.2f}，"
                     f"持有 {w['hold_days']} 日")
        if w.get("reason"):
            lines.append(f"> {_one_line(w['reason'])}")
    if watch:
        lines.append("> 撮合引擎只按开盘价成交，以上仅为盘中观察；今日未成交即视为放弃。")
    lines.append("**点评**：" + (_one_line(comment) or "（午盘点评不可用）"))
    lines.append(DISCLAIMER)
    return "\n".join(lines)


ALERT_PREFIXES = {
    "alert": "【RSIQS 告警】",     # 需要人工关注的错误、异常、任务失败
    "trade": "【RSIQS 交易】",     # 模拟盘成交、止损止盈等交易信号
    "strategy": "【RSIQS 策略】",  # 策略晋升、降级、淘汰
    "notice": "【RSIQS 通知】",    # 其他普通信息
}


def format_alert(title: str, body: str, kind: str = "alert") -> str:
    """kind 决定前缀，便于在群里区分错误和普通消息；未知 kind 直接报错，避免配错静默成错误类别。"""
    return f"{ALERT_PREFIXES[kind]}{title}\n{body}"
