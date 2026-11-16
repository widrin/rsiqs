"""每周复盘：按当前行情状态比较各策略的模拟盘表现，满足条件时自动切换策略。

行情标签来自 engine.regime（每日结算写入 kv）。切换规则是确定性的，口径与晋升/降级一致：
当前行情窗口内，影子盘策略相对现任的年化超额差距达到 10% 且差异的 t 值 >= 1.5，或现任在当前窗口
显著跑输基准、同时有影子盘策略跑赢基准时，自动把影子盘策略升为现任、原任降为影子盘。
只从影子盘里选（研究已通过门槛并前向模拟过），候选与已退役策略不会被自动启用。"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from engine import promotion, regime
from engine.calendar import TradingCalendar
from engine.store import Store
from notify.format import format_alert

BENCH_ACCOUNT = "bench:csi300"
MIN_DAYS = 10          # 窗口内至少这么多收益日才纳入比较
FALLBACK_DAYS = 60     # 行情标签不足一个窗口时，改看最近这么多收益日
SWITCH_MARGIN = 0.10   # 相对现任的年化超额差距达到此值才建议切换
SWITCH_T = 1.5         # 差距的 t 值门槛
BAD_EXCESS = -0.05     # 现任年化超额低于此值视为显著跑输基准
STATUS_CN = {"incumbent": "现任", "shadow": "影子盘", "retired": "已退役"}


def regime_window(store: Store, bench: pd.Series, today: dt.date) -> tuple[str | None, dt.date | None, int]:
    """当前行情状态及其起点：从最新交易日往回数，直到 regime 标签第一次变化或缺失。"""
    dates = [ts.date() for ts in bench[bench.index <= pd.Timestamp(today)].index]
    if not dates:
        return None, None, 0
    rec = store.kv_get(f"regime:{dates[-1].isoformat()}")
    state = rec.get("state") if isinstance(rec, dict) else None
    if not state:
        latest = regime.latest(store)
        state = latest.state if latest is not None else None
    if not state:
        return None, None, 0
    start, count = dates[-1], 0
    for d in reversed(dates):
        rec = store.kv_get(f"regime:{d.isoformat()}")
        if not isinstance(rec, dict) or rec.get("state") != state:
            break
        start, count = d, count + 1
    return state, start, count


def window_stats(nav: pd.Series, bench: pd.Series, start: dt.date, today: dt.date) -> dict | None:
    """窗口内的收益、年化超额（相对基准）与最大回撤；点数不足两个时返回 None。"""
    a, b = pd.Timestamp(start), pd.Timestamp(today)
    win = nav[(nav.index >= a) & (nav.index <= b)].dropna()
    if len(win) < 2:
        return None
    bwin = bench[(bench.index >= a) & (bench.index <= b)].dropna()
    excess_ann, t = promotion.compare(bwin, win)
    days = len(win) - 1
    ret = float(win.iloc[-1] / win.iloc[0]) - 1.0
    ret_ann = (1.0 + ret) ** (252.0 / days) - 1.0 if days > 0 and ret > -1.0 else ret
    max_dd = float((win / win.cummax() - 1.0).min())
    return {"days": days, "ret": ret, "ret_ann": ret_ann,
            "excess_ann": excess_ann, "t": t, "max_dd": max_dd, "nav": win}


def collect(store: Store, today: dt.date) -> dict:
    """当前行情窗口内各策略的模拟盘表现。窗口不足 MIN_DAYS 时退回最近 FALLBACK_DAYS 个收益日。"""
    bench = store.nav_series(BENCH_ACCOUNT)
    bench = bench[bench.index <= pd.Timestamp(today)].dropna()
    incs = store.strategies_with_status("incumbent")
    incumbent = incs[0] if incs else None
    inc_since = store.status_since(incumbent) if incumbent else None
    today = today if bench.empty else min(today, bench.index[-1].date())
    state, start, days = regime_window(store, bench, today) if len(bench) >= 2 else (None, None, 0)
    fallback = state is None or days < MIN_DAYS
    if fallback:
        start = None if bench.empty else bench.index[max(0, len(bench) - 1 - FALLBACK_DAYS)].date()
    rows = []
    if start is not None:
        for status in STATUS_CN:  # 账户枚举来自策略状态：净值表里没有状态的记录不参与比较
            for strategy in store.strategies_with_status(status):
                stats = window_stats(store.nav_series(f"q:{strategy}"), bench, start, today)
                if stats is not None:
                    rows.append({"strategy": strategy, "status": status, **stats})
    rows.sort(key=lambda r: r["excess_ann"], reverse=True)
    return {"state": state, "start": start, "days": days, "fallback": fallback,
            "rows": rows, "incumbent": incumbent, "inc_since": inc_since}


def decide(data: dict) -> dict:
    """给出切换建议。返回 {"action": none|hold|switch, "to": 策略名|None, "text": 中文说明}。

    只把影子盘策略作为切换目标：已退役的策略不再自动启用，候选策略没有前向净值。"""
    rows, incumbent = data["rows"], data["incumbent"]
    if not rows:
        return {"action": "none", "to": None, "text": "暂无可比较的策略净值（账户尚未结算），本周不出切换建议。"}
    eligible = [r for r in rows if r["days"] >= MIN_DAYS]
    inc = next((r for r in eligible if r["strategy"] == incumbent), None)
    alts = [r for r in eligible if r["strategy"] != incumbent and r["status"] == "shadow"]
    best = max(alts, key=lambda r: r["excess_ann"]) if alts else None
    if inc is None:
        if best is None:
            return {"action": "none", "to": None,
                    "text": f"没有影子盘策略在当前窗口有 {MIN_DAYS} 个以上收益日，暂不给出建议。"}
        return {"action": "switch", "to": best["strategy"],
                "text": (f"当前没有可比较的现任（{incumbent or '未设置'}），建议以 {best['strategy']} 为现任"
                         f"（窗口年化超额 {best['excess_ann']:+.1%}）。")}
    if best is None:
        return {"action": "hold", "to": incumbent,
                "text": f"维持现任 {incumbent}：影子盘在当前窗口的净值天数不足 {MIN_DAYS}，无法比较。"}
    diff = best["excess_ann"] - inc["excess_ann"]
    t_diff = promotion.compare(inc["nav"], best["nav"])[1]
    if diff >= SWITCH_MARGIN and t_diff >= SWITCH_T and best["excess_ann"] > 0:
        return {"action": "switch", "to": best["strategy"],
                "text": (f"把现任 {incumbent} 切换为 {best['strategy']}：当前窗口年化超额 "
                         f"{best['excess_ann']:+.1%} 对 {inc['excess_ann']:+.1%}（差 {diff:+.1%}，t={t_diff:.2f}）。")}
    if inc["excess_ann"] <= BAD_EXCESS and best["excess_ann"] > 0:
        return {"action": "switch", "to": best["strategy"],
                "text": (f"把现任 {incumbent} 切换为 {best['strategy']}：现任当前窗口年化超额 "
                         f"{inc['excess_ann']:+.1%} 已显著跑输基准，{best['strategy']} 为 "
                         f"{best['excess_ann']:+.1%}。")}
    return {"action": "hold", "to": incumbent,
            "text": (f"维持现任 {incumbent}：窗口年化超额 {inc['excess_ann']:+.1%}，最好的影子盘策略 "
                     f"{best['strategy']} 为 {best['excess_ann']:+.1%}，差距未同时达到切换门槛"
                     f"（{SWITCH_MARGIN:.0%} 且 t>={SWITCH_T}）。")}


def apply_switch(store: Store, data: dict, rec: dict, calendar: TradingCalendar, day: dt.date) -> dict:
    """条件满足时自动切换：目标升为现任，原任降为影子盘（从下一个交易日重新起算影子期）。

    返回 {"applied": bool, "detail": 中文说明}。目标必须是影子盘，否则不切换。"""
    if rec["action"] != "switch" or not rec["to"]:
        return {"applied": False, "detail": ""}
    target, old = rec["to"], data["incumbent"]
    if target == old:
        return {"applied": False, "detail": f"{target} 已是现任，无需切换"}
    if store.get_status(target) != "shadow":
        return {"applied": False, "detail": f"{target} 不在影子盘，未自动切换"}
    since = day
    try:  # 原任降为影子后，影子期从下一个交易日算起（与晋升/降级口径一致）
        since = calendar.next_day(day)
    except ValueError:  # 日历末端
        pass
    if old:
        store.set_status(old, "shadow", since)
    store.set_status(target, "incumbent", day)
    detail = f"{target} 成为现任" + (f"，{old} 降为影子盘（影子期自 {since.isoformat()} 起算）" if old else "")
    return {"applied": True, "detail": detail}


def render(day: dt.date, data: dict, rec: dict, applied: dict | None = None) -> str:
    state_cn = regime.cn(data["state"]) if data["state"] else "未知"
    if data["fallback"] or data["start"] is None:
        window = f"行情标签不足，改看最近 {FALLBACK_DAYS} 个收益日"
    else:
        window = f"{data['start'].isoformat()} 起 {data['days']} 个收益日"
    lines = [f"## 每周复盘 {day.isoformat()}", f"**行情**：{state_cn}（{window}）"]
    if data["incumbent"] and data["inc_since"] and data["start"] and not data["fallback"] \
            and data["inc_since"] < data["start"]:
        lines.append(f"现任 {data['incumbent']} 上任于 {data['inc_since'].isoformat()}，早于本轮行情，"
                     "当前行情下的表现此前未经检验。")
    if data["rows"]:
        lines.append("**当前窗口表现**（相对沪深300，年化超额）")
        rows = sorted(data["rows"], key=lambda r: (r["strategy"] != data["incumbent"], -r["excess_ann"]))
        for r in rows:
            lines.append(f"- {STATUS_CN.get(r['status'], r['status'])} {r['strategy']}：{r['days']} 日，"
                         f"收益 {r['ret']:+.1%}，年化超额 {r['excess_ann']:+.1%}（t={r['t']:.2f}），"
                         f"最大回撤 {r['max_dd']:.1%}")
    lines.append("**建议**：" + rec["text"])
    if rec["action"] == "switch" and applied is not None:
        lines.append("**执行**：" + ("已自动切换，" + applied["detail"] if applied["applied"]
                                    else "未自动切换（" + applied["detail"] + "）"))
    return "\n".join(lines)


def run_weekly(day: dt.date, store: Store, notifier, calendar: TradingCalendar) -> dict:
    """生成每周复盘、自动执行切换并推送；返回 {"state", "action", "to", "applied"}。"""
    data = collect(store, day)
    rec = decide(data)
    applied = apply_switch(store, data, rec, calendar, day)
    notifier.send_text(format_alert(f"每周复盘与策略切换 {day.isoformat()}",
                                    render(day, data, rec, applied), kind="strategy"))
    return {"state": data["state"], "action": rec["action"], "to": rec["to"],
            "applied": applied["applied"]}
