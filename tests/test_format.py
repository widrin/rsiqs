# tests/test_format.py
from datetime import date

import pytest

from engine.types import Pick
from notify.format import format_alert, format_daily_report, format_recommendation

P = Pick(code="600000", name="浦发银行", score=1.23, entry_max=10.30,
         stop=9.31, target=11.00, hold_days=5, reason="20日涨幅12.0%")


def test_recommendation_reviewed():
    s = format_recommendation(date(2026, 10, 16), [P], llm_reviewed=True, market_view="震荡偏强")
    assert "2026-10-16" in s
    assert "600000 浦发银行" in s
    assert "≤10.30" in s and "止损 9.31" in s and "止盈 11.00" in s and "持有 5 日" in s
    assert "20日涨幅12.0%" in s
    assert "震荡偏强" in s
    assert "未经 LLM 审核" not in s
    assert "不构成投资建议" in s


def test_recommendation_unreviewed_and_empty():
    s = format_recommendation(date(2026, 10, 16), [], llm_reviewed=False)
    assert "未经 LLM 审核" in s
    assert "今日无推荐" in s


def test_daily_report():
    rows = [{"account": "h:active", "equity": 512345.678, "day_ret": 0.0123, "total_ret": 0.0247},
            {"account": "bench:csi300", "equity": 498000.0, "day_ret": -0.004, "total_ret": -0.004}]
    s = format_daily_report(date(2026, 10, 16), rows, "今日复盘要点")
    assert "2026-10-16" in s
    assert "h:active" in s and "512,345.68" in s and "+1.23%" in s and "+2.47%" in s
    assert "-0.40%" in s
    assert "今日复盘要点" in s
    assert "复盘" in format_daily_report(date(2026, 10, 16), rows, "")


def test_daily_report_lists_holdings_pnl():
    rows = [{"account": "h:active", "equity": 512345.678, "day_ret": 0.0123, "total_ret": 0.0247}]
    holdings = [{"account": "h:active", "code": "600000", "name": "浦发银行", "qty": 1000, "cost": 10.0,
                 "price": 10.15, "day_ret": 0.015, "pnl": 150.0, "pnl_ret": 0.015}]
    s = format_daily_report(date(2026, 10, 16), rows, "", holdings)
    assert "收盘持仓收益" in s
    assert "**h:active** 浮动盈亏 +150 元（+1.50%）" in s
    assert "- 600000 浦发银行 1000 股：成本 10.00 → 收盘 10.15，浮动 +150 元（+1.50%），今日 +1.50%" in s


def test_daily_report_says_empty_when_no_holdings():
    s = format_daily_report(date(2026, 10, 16), [], "", [])
    assert "收盘持仓收益" in s and "（空仓）" in s


def test_daily_report_without_holdings_argument_has_no_section():
    assert "收盘持仓收益" not in format_daily_report(date(2026, 10, 16), [], "")


def test_alert():
    s = format_alert("任务失败 settle", "600000 数据不完整")
    assert s.startswith("【RSIQS 告警】任务失败 settle")
    assert "600000 数据不完整" in s


def test_alert_kinds_are_distinguishable():
    assert format_alert("触发止损", "现价 9.30", kind="trade").startswith("【RSIQS 交易】")
    assert format_alert("策略降级", "", kind="strategy").startswith("【RSIQS 策略】")
    assert format_alert("研究会话完成", "", kind="notice").startswith("【RSIQS 通知】")
    with pytest.raises(KeyError):  # 类别写错不能静默推成告警
        format_alert("x", "", kind="bad")


def test_multiline_reason_and_view_stay_in_place():
    p = Pick(code="600000", name="浦发银行", score=1.0, entry_max=10.3, stop=9.3, target=11.0, hold_days=5,
             reason="结论：看多\n# 证据：放量\n> 引用")
    s = format_recommendation(date(2026, 10, 16), [p], llm_reviewed=True, market_view="偏强\n## 竞价确认")
    lines = s.split("\n")
    assert not any(line.startswith(("# ", "## 竞价")) for line in lines[1:])  # 不会被渲染成标题
    assert any("结论：看多" in line and "证据：放量" in line for line in lines)


def test_daily_report_has_no_markdown_table():
    rows = [{"account": "h:active", "equity": 512345.678, "day_ret": 0.0123, "total_ret": 0.0247}]
    s = format_daily_report(date(2026, 10, 16), rows, "")
    assert "|" not in s  # 企业微信群机器人 markdown（v1）不渲染表格
