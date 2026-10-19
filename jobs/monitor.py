"""盘中监控：09:30-15:00 每 120 秒检查 h:active 持仓（仅交易日）。python -m jobs.monitor"""
from __future__ import annotations

import math
import time as _time
from datetime import date, datetime, time, timedelta

from engine.ledger import ex_rights_price
from engine.rules import limit_prices
from jobs import common
from jobs.settle import BIG_EX_GAP
from notify.format import format_alert

ACCOUNT = "h:active"
INTERVAL_SEC = 120
SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))  # 午休 11:30-13:00 不查
MAX_FAILS = 3  # 连续失败达到此次数告警一次


def in_session(t: time) -> bool:
    return any(a <= t <= b for a, b in SESSIONS)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _levels(pos, acc, cache, d: date) -> tuple[float, float]:
    """止损、止盈价：建仓之后到 d 日（含）之间除权除息、结算还没调整这笔持仓的（包括前一天除权、当天结算
    失败或还没补跑的），按日期顺序换算到除权后。"""
    stop, target = pos.stop, pos.target
    if cache is None:
        return stop, target
    done = {a["date"] for a in acc.corporate_actions if a["code"] == pos.code}
    for ex, cash, ratio in sorted(cache.dividends(pos.code)):
        if pos.entry_date < ex <= d and ex.isoformat() not in done:
            stop, target = ex_rights_price(stop, cash, ratio), ex_rights_price(target, cash, ratio)
    return stop, target


def _unexplained_ex(code: str, prev: float, cache, d: date) -> str:
    """报价的参考昨收比缓存里最近的不复权收盘低 BIG_EX_GAP 以上、又没有对应的分红记录时，返回说明。"""
    if cache is None or not prev > 0 or not hasattr(cache, "last_raw_close"):
        return ""
    last = cache.last_raw_close(code, d - timedelta(days=1))
    if last is None or prev >= last[1] * (1 - BIG_EX_GAP):
        return ""
    if any(last[0] < ex <= d for ex, _, _ in cache.dividends(code)):
        return ""
    return (f"参考昨收 {prev:.2f} 比上一收盘 {last[1]:.2f} 低 {1 - prev / last[1]:.0%}，还没有分红记录，"
            "止损止盈未换算，今天暂不按它们提醒")


def check_once(d: date, store, client, notifier, cache=None) -> list[str]:
    """检查一次持仓，返回本次新发出的告警；(code, 事件) 去重键按日持久化在 kv。
    cache 提供分红送转记录：建仓后除权除息、结算还没调整的持仓按换算后的止损、止盈价判断。
    报价源没有返回的持仓每天记一次警告日志。"""
    acc = store.load_account(ACCOUNT)
    if acc is None or not acc.positions:
        return []
    rows = {str(r["code"]): r for r in client.spot(list(acc.positions)).to_dict("records")}  # 只取持仓
    key = f"monitor_sent:{d.isoformat()}"
    sent = set(store.kv_get(key) or [])
    out, dirty = [], False
    for code, pos in acc.positions.items():
        if pos.exit_date <= d:  # 到期的持仓开盘已按到期卖出，盘中不再提醒止损、止盈
            continue
        r = rows.get(code)
        if r is None:  # 报价源没有返回这只：记一次日志（每天一次），不推送
            if f"{code}:无报价" not in sent:
                common.log.warning("monitor %s: no quote for %s, not monitored", d, code)
                sent.add(f"{code}:无报价")
                dirty = True
            continue
        last, high, prev = _num(r.get("last")), _num(r.get("high")), _num(r.get("prev_close"))
        if not last > 0:  # 停牌或无报价
            continue
        events = []
        stop, target = _levels(pos, acc, cache, d)
        unexplained = _unexplained_ex(code, prev, cache, d)
        if unexplained:  # 结算在等分红记录（疑似送转）：止损止盈没有换算，按旧价位判断会误报
            events.append(("疑似除权", unexplained))
        elif last <= stop:
            events.append(("触发止损", f"现价 {last:.2f} ≤ 止损 {stop:.2f}"))
        if last >= target and not unexplained:
            events.append(("触发止盈", f"现价 {last:.2f} ≥ 止盈 {target:.2f}"))
        if high > 0 and prev > 0:  # 无 high 列时跳过炸板判断
            up, _ = limit_prices(code, str(r.get("name") or pos.name), prev)
            if high >= up and last < up:
                events.append(("炸板", f"最高 {high:.2f} 曾触及涨停 {up:.2f}，现价 {last:.2f}"))
        for event, detail in events:
            if f"{code}:{event}" in sent:
                continue
            sent.add(f"{code}:{event}")
            msg = format_alert(f"{event} {code} {pos.name}",
                               f"{detail}；持仓 {pos.qty} 股，成本 {pos.cost:.2f}")
            notifier.send_text(msg)
            out.append(msg)
    if out or dirty:
        store.kv_set(key, sorted(sent))
    return out


def main(now: datetime | None = None, interval: float = INTERVAL_SEC) -> None:
    """循环到 15:00；now 仅用于判断是否交易日，循环内用真实时间。"""
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        return
    fails = 0
    while True:
        t = datetime.now()
        if t.date() != d or t.time() > SESSIONS[-1][1]:
            break
        if in_session(t.time()):
            try:
                check_once(d, store, client, notifier, cache)
                fails = 0
            except Exception as e:  # noqa: BLE001 — 单次失败不退出循环
                fails += 1
                common.log.warning("monitor check failed (%d): %s", fails, e)
                if fails == MAX_FAILS:
                    notifier.send_text(format_alert("盘中监控异常", f"行情连续 {fails} 次获取失败：{e}"))
        _time.sleep(interval)


if __name__ == "__main__":
    main()
