"""盘中监控：09:30-15:00 每 120 秒检查 h:active 持仓（仅交易日）。python -m jobs.monitor"""
from __future__ import annotations

import math
import time as _time
from datetime import date, datetime, time, timedelta

from engine.ledger import ex_rights_price
from engine.rules import at_limit_up, limit_prices
from jobs import common
from jobs.settle import BIG_EX_GAP
from notify.format import format_alert

ACCOUNT = "h:active"
INTERVAL_SEC = 120
SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))  # 午休 11:30-13:00 不查
MAX_FAILS = 3  # 连续失败达到此次数告警一次
MISSING_ALERT_AFTER = 3  # 某只持仓连续这么多次取不到报价才推送提醒（单次偶发失败只记日志）


def _check_recommendation_buys(d: date, store, rows: dict, sent: set, notifier) -> list[str]:
    """检查今日推荐的买入触发，返回本次新推的消息。

    两种粒度（撮合引擎只按开盘价成交，盘中触发是额外信号，不代表撮合会执行）：
    - 开盘触发：spot.open（竞价价）在 (stop, entry_max] 且不涨停，撮合 step 2 口径。spot.open 全天都是
      集合竞价价，任何时刻查都一样，每天只推一次。
    - 盘中触发：spot.last（现价）回踩入 (stop, entry_max] 区间，每天只推一次（去重 key 天然幂等）。

    已持仓的推荐代码跳过（撮合已买）。"""
    out = []
    rec = store.load_recommendation(d, ACCOUNT)
    if rec is None:
        return out
    picks = rec[0]
    if not picks:
        return out
    acc = store.load_account(ACCOUNT)
    held = set(acc.positions) if acc else set()
    for pick in picks:
        if pick.code in held:
            continue  # 撮合已买或之前已持仓
        row = rows.get(pick.code)
        if row is None:
            continue  # 推荐代码今天没取到报价
        open_price = _num(row.get("open"))
        last_price = _num(row.get("last"))
        prev = _num(row.get("prev_close"))
        if prev <= 0:
            continue  # 停牌或无有效昨收，无法算涨停价
        name = str(row.get("name") or pick.name)
        up, _ = limit_prices(pick.code, name, prev, d)
        # --- 开盘触发（撮合口径） ---
        key_open = f"{pick.code}:开盘可买"
        if (key_open not in sent and open_price > 0
                and pick.stop < open_price <= pick.entry_max and not at_limit_up(open_price, up)):
            sent.add(key_open)
            msg = format_alert(
                f"开盘可买 {pick.code} {name}",
                f"竞价价 {open_price:.2f}，在买入区间（≤ {pick.entry_max:.2f}，止损 {pick.stop:.2f}），"
                f"止盈 {pick.target:.2f}，持有 {pick.hold_days} 日；撮合引擎将按开盘价成交",
                kind="trade")
            notifier.send_text(msg)
            out.append(msg)
        # --- 盘中触发（现价回踩） ---
        key_intra = f"{pick.code}:盘中可买"
        if (key_intra not in sent and last_price > 0
                and pick.stop < last_price <= pick.entry_max):
            sent.add(key_intra)
            msg = format_alert(
                f"盘中可买 {pick.code} {name}",
                f"现价 {last_price:.2f} 回踩入买入区间（≤ {pick.entry_max:.2f}，止损 {pick.stop:.2f}），"
                f"止盈 {pick.target:.2f}；此为盘中信号，撮合引擎仅按开盘价成交（今日若未撮合则视为放弃）",
                kind="trade")
            notifier.send_text(msg)
            out.append(msg)
    return out


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


def _unexplained_ex(code: str, prev: float, cache, d: date, calendar, settled_prev: bool = False) -> str:
    """报价的参考昨收比缓存里最近的不复权收盘低 BIG_EX_GAP 以上、又没有对应的分红记录时，返回说明。
    最近的不复权收盘不是上一交易日的：上一交易日已结算（这只是停牌，复牌当天可能就是除权日）时照常判断；
    还没结算（数据没更新，正常的大跌不能当成除权）时不判断。"""
    if cache is None or calendar is None or not prev > 0 or not hasattr(cache, "last_raw_close"):
        return ""
    last = cache.last_raw_close(code, d - timedelta(days=1))
    if last is None or (last[0] != calendar.prev_day(d) and not settled_prev) or prev >= last[1] * (1 - BIG_EX_GAP):
        return ""
    if any(last[0] < ex <= d for ex, _, _ in cache.dividends(code)):
        return ""
    return (f"参考昨收 {prev:.2f} 比上一收盘 {last[1]:.2f} 低 {1 - prev / last[1]:.0%}，还没有分红记录，"
            "止损止盈未换算，今天暂不按它们提醒")


def check_once(d: date, store, client, notifier, cache=None, calendar=None) -> list[str]:
    """检查一次持仓 + 今日推荐，返回本次新发出的告警；(code, 事件) 去重键按日持久化在 kv。

    检查顺序：
    1. 今日推荐的买入触发（开盘可买 / 盘中可买）——在持仓检查之前，撮合口径与 broker step 2 一致
    2. 持仓止损/止盈/炸板/T+1 解锁

    09:30 起每次扫描都会给刚解锁的今日新建仓推一条确认（T+1 前不能卖，09:30 start_day 后解锁）。
    cache 提供分红送转记录：建仓后除权除息、结算还没调整的持仓按换算后的止损、止盈价判断。
    东财个股报价取不到时回退全市场快照（见 AkClient.spot 的 fallback）；某只持仓仍然连续
    MISSING_ALERT_AFTER 次没有报价，说明这只股票今天没有止损、止盈保护，按日推送一次提醒。"""
    acc = store.load_account(ACCOUNT)
    held_codes = set(acc.positions) if acc else set()
    rec = store.load_recommendation(d, ACCOUNT)
    rec_codes = {p.code for p in (rec[0] if rec else [])}
    all_codes = sorted(held_codes | rec_codes)
    if not all_codes:
        return []  # 既没持仓也没推荐，不用拉 spot
    rows = {str(r["code"]): r for r in client.spot(all_codes, fallback=True).to_dict("records")}
    key = f"monitor_sent:{d.isoformat()}"
    sent = set(store.kv_get(key) or [])
    miss_key = f"monitor_missing:{d.isoformat()}"
    misses = dict(store.kv_get(miss_key) or {})
    out, dirty, miss_dirty = [], False, False
    # --- 推荐买入触发 ---
    if rec is not None and rec[0]:
        buys = _check_recommendation_buys(d, store, rows, sent, notifier)
        if buys:
            out.extend(buys)
            dirty = True
    # --- 持仓检查（已有逻辑） ---
    if acc is None or not acc.positions:
        if dirty:
            store.kv_set(key, sorted(sent))
        return out
    last_nav = store.last_nav_date(ACCOUNT)
    settled_prev = calendar is not None and last_nav is not None and last_nav >= calendar.prev_day(d)
    for code, pos in acc.positions.items():
        # 建仓确认：买入次日（或更久后首次被 start_day 解锁）首次扫到就推一条
        # acc.available 在 T+1 解锁前为 0（start_day 设的），解锁后等于 pos.qty
        # calendar is None 时跳过（离线测试；生产环境 main() 总传入 calendar）
        if calendar is not None and pos.available > 0 \
                and pos.entry_date == calendar.prev_day(d) \
                and f"{code}:T+1解锁" not in sent:
            sent.add(f"{code}:T+1解锁")
            dirty = True
            pnl = ""
            r = rows.get(code)
            if r is not None:
                last, prev = _num(r.get("last")), _num(r.get("prev_close"))
                if last > 0:
                    pnl = (f"，浮盈 {(last - pos.cost) * pos.qty:+.0f} 元（{last / pos.cost - 1:+.1%}）"
                           f"，昨收 {prev:.2f}")
            msg = format_alert(
                f"T+1 解锁 {code} {pos.name}",
                f"昨收买入 {pos.qty} 股，成本 {pos.cost:.2f}，止损 {pos.stop:.2f}，"
                f"止盈 {pos.target:.2f}，持有至 {pos.exit_date.isoformat()}；"
                f"今日起可卖{pnl}",
                kind="trade")
            notifier.send_text(msg)
            out.append(msg)
            # T+1 解锁是一次性确认：后面不再对这只持仓推止损/止盈/炸板之外的事件
            # 继续往下走到下面的到期判断
        if pos.exit_date <= d:  # 到期的持仓开盘已按到期卖出，盘中不再提醒止损、止盈
            continue
        r = rows.get(code)
        if r is None:  # 报价源没有返回这只：记一次日志（每天一次），连续多次才推送提醒
            misses[code] = min(misses.get(code, 0) + 1, MISSING_ALERT_AFTER)
            miss_dirty = True
            if f"{code}:无报价" not in sent:
                common.log.warning("monitor %s: no quote for %s, not monitored", d, code)
                sent.add(f"{code}:无报价")
                dirty = True
            if misses[code] >= MISSING_ALERT_AFTER and f"{code}:行情缺失" not in sent:
                sent.add(f"{code}:行情缺失")
                dirty = True
                msg = format_alert(f"行情缺失 {code} {pos.name}",
                                   f"连续 {misses[code]} 次取不到报价，今天的止损、止盈、炸板提醒对这只持仓失效；"
                                   "请检查数据源，或手动盯盘")
                notifier.send_text(msg)
                out.append(msg)
            continue
        if misses.pop(code, None) is not None:
            miss_dirty = True
        last, high, prev = _num(r.get("last")), _num(r.get("high")), _num(r.get("prev_close"))
        if not last > 0:  # 停牌或无报价
            continue
        events = []
        stop, target = _levels(pos, acc, cache, d)
        unexplained = _unexplained_ex(code, prev, cache, d, calendar, settled_prev)
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
            # 疑似除权时现价已按除权后、成本还没换算，两者不可比，不算浮盈
            pnl = "" if event == "疑似除权" else (
                f"，浮动盈亏 {(last - pos.cost) * pos.qty:+.0f} 元（{last / pos.cost - 1:+.1%}）")
            msg = format_alert(f"{event} {code} {pos.name}",
                               f"{detail}；持仓 {pos.qty} 股，成本 {pos.cost:.2f}{pnl}", kind="trade")
            notifier.send_text(msg)
            out.append(msg)
    if out or dirty:
        store.kv_set(key, sorted(sent))
    if miss_dirty:
        store.kv_set(miss_key, misses)
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
                check_once(d, store, client, notifier, cache, calendar)
                fails = 0
            except Exception as e:  # noqa: BLE001 — 单次失败不退出循环
                fails += 1
                common.log.warning("monitor check failed (%d): %s", fails, e)
                if fails == MAX_FAILS:
                    notifier.send_text(format_alert("盘中监控异常", f"行情连续 {fails} 次获取失败：{e}"))
        _time.sleep(interval)


if __name__ == "__main__":
    main()
