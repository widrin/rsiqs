# notify/format.py
"""推送消息模板。"""
from datetime import date

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
                        holdings: list[dict] | None = None) -> str:
    # 企业微信群机器人的 markdown（v1）不渲染表格：每个账户一行
    lines = [f"## RSIQS 日报 {d.isoformat()}"]
    for r in rows:
        lines.append(f"**{r['account']}** 净值 {r['equity']:,.2f}，当日 {r['day_ret']:+.2%}，累计 {r['total_ret']:+.2%}")
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
            else:
                lines.append(f"**{account}**")
            for h in part:
                if h["price"] is None:
                    lines.append(f"- {h['code']} {h['name']} {h['qty']} 股：成本 {h['cost']:.2f}，收盘价缺失")
                    continue
                day = f"，当日 {h['day_ret']:+.2%}" if h["day_ret"] is not None else ""
                lines.append(f"- {h['code']} {h['name']} {h['qty']} 股：成本 {h['cost']:.2f} → "
                             f"收盘 {h['price']:.2f}，浮动 {h['pnl']:+,.0f} 元（{h['pnl_ret']:+.2%}）{day}")
    lines.append("**复盘**：" + (_one_line(review_summary) or "（本日无 LLM 复盘）"))
    return "\n".join(lines)


def format_alert(title: str, body: str) -> str:
    return f"【RSIQS 告警】{title}\n{body}"
