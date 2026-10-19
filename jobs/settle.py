"""收盘结算：15:35 定时 + 开机补跑。python -m jobs.settle"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd

from data.akshare_client import DataError
from engine import promotion
from engine.backtest import bars_for_day
from engine.broker import SimBroker
from engine.dataview import DataView
from engine.ledger import Account, ex_rights_price
from jobs import common
from notify.format import format_alert
from strategies.base import load_strategy

CORE_ACCOUNTS = (*common.LLM_ACCOUNTS, common.BENCH_ACCOUNT)
LOOKBACK_CAL_DAYS = 550  # 约 370 个交易日，供策略计算因子
MIN_COVERAGE = 0.9       # 当日有日线的股票占比低于此值视为数据未更新（股票池里同时停牌的很少超过几十只）
KIND_CN = {"promote": "晋升", "demote": "降级", "retire": "淘汰"}
BIG_EX_GAP = 0.03       # 参考昨收比上一收盘低这么多、又没有分红记录：多半是送转或大额派息的记录还没到
MAX_EX_WAIT_DAYS = 2    # 最多为等分红记录暂停这么多个交易日，之后按配股告警、不建模


def pending_days(store, calendar, now: datetime) -> list[date]:
    lasts = [x for x in (store.last_nav_date(n) for n in CORE_ACCOUNTS) if x is not None]
    if not lasts:
        return []  # 尚未 bootstrap
    start, end = calendar.next_day(min(lasts)), common.last_closed_day(now, calendar)
    return calendar.between(start, end) if start <= end else []


def tracked_codes(store) -> list[str]:
    """股票池 ∪ 各账户持仓（股票池变动后旧持仓仍能按规则退出）。"""
    codes = set(store.kv_get("universe") or [])
    for name in store.list_accounts():
        acc = store.load_account(name)
        if acc is not None:
            codes.update(acc.positions)
    return sorted(codes)


def refresh_universe(store, client) -> None:
    """每天刷新 kv universe（沪深300 + 中证500 当前成分），与盘前扫描用同一份名单；取不到时沿用旧名单。"""
    try:
        codes = sorted(client.universe())
    except Exception:  # noqa: BLE001 - 成分股取不到不影响结算
        common.log.exception("refresh universe failed, keeping the old one")
        return
    if codes:
        store.kv_set("universe", codes)


def recommended_codes(store, days) -> set[str]:
    """这些日子盘前推荐给两个 LLM 账户的代码（可能不在 kv universe 里）。"""
    out = set()
    for d in days:
        for name in common.LLM_ACCOUNTS:
            rec = store.load_recommendation(d, name)
            out.update(p.code for p in (rec[0] if rec else []))
    return out


def refresh_data(codes: list[str], end: date, client, cache) -> None:
    cache.update(codes, end=end)
    cache.save_index(common.INDEX_SYMBOL, client.index_daily(common.INDEX_SYMBOL))
    try:  # 每天刷新简称：新纳入的成分股、新戴帽的股票（基线策略按名称排除 ST）
        cache.set_names(client.names())
    except Exception:  # noqa: BLE001 - 名称取不到不影响结算，沿用旧名称
        common.log.exception("refresh names failed")


def refresh_dividends(store, client, cache, d: date | None = None) -> list[str]:
    """刷新所有账户持仓股票，以及 d 日盘前推荐股票的分红送转记录（新浪），返回获取失败的代码。"""
    held = set()
    for name in store.list_accounts():
        acc = store.load_account(name)
        if acc is not None:
            held.update(acc.positions)
    for name in (common.LLM_ACCOUNTS if d else ()):
        rec = store.load_recommendation(d, name)
        held.update(p.code for p in (rec[0] if rec else []))
    failed = []
    for code in sorted(held):
        try:
            cache.save_dividends(code, client.dividends(code))
        except Exception:  # noqa: BLE001 — 单只失败不阻塞结算，汇总后告警
            common.log.exception("dividends %s failed", code)
            failed.append(code)
    return failed


RAW_START_DAYS = 30  # 新代码的不复权日线从结算日前 30 天取起：估值和除权核对只需要最近的收盘价


def _scale(pick, k: float):
    return replace(pick, entry_max=pick.entry_max * k, stop=pick.stop * k, target=pick.target * k)


def _to_raw(pick, q, r):
    """量化策略按前复权价格写价位，结算按不复权日线撮合。按 d 日两根 K 线的收盘价之比换算（前复权扣减了 d 日之后
    的分红送转；没有除权时两者相同，价位不变）。不用高低价算斜率：振幅只有几分钱时，舍入到分的误差太大。
    派息时比例换算与精确的加回派息略有出入，误差约为 股息率 ×（价位 / 收盘 - 1），高股息低价股可达 0.5%，
    偏向宽松（买入上限与目标略高、止损略低）。只在多日补跑跨越之后的除权时才会用到。"""
    if q is None or r is None or not (q.close > 0 and r.close > 0):
        return pick
    k = r.close / q.close
    return pick if abs(k - 1.0) < 1e-9 else _scale(pick, k)


def _rescale_pick(pick, ref: float | None, bar):
    """盘前推荐的价位按 ref（写价位时的昨收）写成；d 日交易所的参考昨收与它不同（除权除息）时按比例换算。
    数据已按新基准复权、或盘前已按竞价快照换算时两者相同，不会重复换算。"""
    if not ref or bar is None or not bar.prev_close > 0 or abs(bar.prev_close - ref) <= 0.011:
        return pick
    return _scale(pick, bar.prev_close / ref)


def _valuation(cache, code: str, d: date) -> float | None:
    """持仓估值：d 日（含）之前最近的不复权收盘价；中间有除权（例如除权日缺 K 线）时换算到除权后，
    与已经按送转放大的股数一致。"""
    last = cache.last_raw_close(code, d)
    if last is None:
        return None
    t, price = last
    for ex, cash, ratio in cache.dividends(code):
        if t < ex <= d:
            price = ex_rights_price(price, cash, ratio)
    return price


def _ex_adjust_pick(pick, cache, d: date):
    """盘前推荐的价位按除权前的收盘价写成；d 是这只股票的除权除息日时换算到除权后。"""
    for ex, cash, ratio in cache.dividends(pick.code):
        if ex == d:
            return replace(pick, entry_max=ex_rights_price(pick.entry_max, cash, ratio),
                           stop=ex_rights_price(pick.stop, cash, ratio),
                           target=ex_rights_price(pick.target, cash, ratio))
    return pick


def _index_close(series: pd.Series, d: date) -> float:
    s = series.copy()
    s.index = pd.to_datetime(s.index).normalize()
    ts = pd.Timestamp(d)
    if ts not in s.index:
        raise DataError(f"沪深300 指数缺少 {d.isoformat()}，数据可能未更新")
    return float(s.loc[ts])


def _settled(store, name: str, d: date) -> bool:
    last = store.last_nav_date(name)
    return last is not None and last >= d


def settle_day(d: date, settings, store, cache, calendar, div_failed=frozenset(),
               run_date: date | None = None) -> list[str]:
    """结算单个交易日；已记录 d 日净值的账户跳过（幂等）。返回警告列表。

    前复权日线只给策略算因子：除权后它会整体变化，多日补跑时较早的日子已经是之后的复权基准。撮合和估值用
    不复权日线（cache.raw_bars），持仓在除权除息日按分红记录调整。div_failed 是今天分红记录获取失败的代码；
    run_date 是实际运行的日期（默认今天），用来判断为等分红记录已经暂停了几天。"""
    run_date = run_date or common.today()
    codes = tracked_codes(store)
    # 当天盘前推荐的代码也载入（盘前扫描用实时股票池，可能有 kv universe 之外的新成分股），只用于撮合检查
    recs = recommended_codes(store, [d])
    panel, names = cache.load_panel(sorted(set(codes) | recs), d - timedelta(days=LOOKBACK_CAL_DAYS), d)
    qfq_bars = bars_for_day(panel, names, d)
    if len(qfq_bars) < max(1, int(len(codes) * MIN_COVERAGE)):
        raise DataError(f"{d.isoformat()} 日线覆盖不足 {len(qfq_bars)}/{len(codes)}，数据可能未更新")
    bench_start = date.fromisoformat(store.kv_get("bench_start"))
    index = cache.load_index(common.INDEX_SYMBOL)
    bench_equity = settings.capital * _index_close(index, d) / _index_close(index, bench_start)

    asof = calendar.prev_day(d)
    # 策略只拿到截至 asof 的面板：d 日的行情只用于撮合。选股范围是股票池加持仓，不含当天 LLM 推荐的代码
    tracked = set(codes)
    view = DataView({c: df.loc[:pd.Timestamp(asof)] for c, df in panel.items() if c in tracked}, names, asof=asof)
    broker, per_pick, warnings = SimBroker(), settings.capital / settings.max_picks, []

    def raw_bars(codes) -> dict:
        need = sorted(set(codes))
        failed = cache.update_raw(need, d, d - timedelta(days=RAW_START_DAYS))
        bars = cache.raw_bars(need, d, names)
        # 前复权有 d 日 K 线、不复权却没有：取数失败或接口返回空表。按停牌处理会漏掉止损和买入，所以暂停结算
        missing = sorted(set(failed) | {c for c in need if c in qfq_bars and c not in bars})
        if missing:
            raise DataError(f"{d.isoformat()} 不复权日线获取失败：{', '.join(missing)}，暂停结算，下次重试")
        return bars

    def check_unexplained_ex(code: str, bar) -> None:
        """不复权日线的参考昨收与上一个收盘价不一致，说明 d 日除权除息。没有对应的分红记录时：分红接口今天失败
        就暂停结算（下次重试）；参考昨收低了 BIG_EX_GAP 以上（送转、大额派息，记录常常晚一天到）也暂停，
        最多等 MAX_EX_WAIT_DAYS 个交易日，否则按未换算的止损撮合会记下假止损；其余告警（多半是配股，不建模）。"""
        prev = cache.last_raw_close(code, calendar.prev_day(d))
        if bar is None or prev is None or abs(bar.prev_close - prev[1]) <= 0.011:
            return
        if any(prev[0] < ex <= d for ex, _, _ in cache.dividends(code)):
            return
        if code in div_failed:
            raise DataError(f"{code} 于 {d.isoformat()} 疑似除权除息，但分红记录获取失败：暂停结算，下次重试")
        waited = len(calendar.between(d, run_date)) - 1 if run_date >= d else 0
        if bar.prev_close < prev[1] * (1 - BIG_EX_GAP) and waited < MAX_EX_WAIT_DAYS:
            raise DataError(f"{code} 于 {d.isoformat()} 参考昨收 {bar.prev_close:.2f} 比上一收盘 {prev[1]:.2f} 低 "
                            f"{1 - bar.prev_close / prev[1]:.1%}，疑似送转或大额派息但还没有分红记录：暂停结算，"
                            f"等记录到了重试（最多等 {MAX_EX_WAIT_DAYS} 个交易日）")
        warnings.append(f"{code} 于 {d.isoformat()} 疑似除权除息，但没有分红记录（可能是配股，不建模），持仓未调整")

    def run(name: str, picks) -> None:
        acc = store.load_account(name)
        if acc is None:
            acc = Account(name=name, cash=settings.capital)
            store.record_nav(name, calendar.prev_day(d), settings.capital)
        bars = raw_bars([*acc.positions, *(p.code for p in picks)])
        if acc.last_trade_day is None or acc.last_trade_day < d:  # d 日尚未撮合：重跑时不重复调整
            # 除权除息：建仓之后到 d 日（含）之间除权、还没调整过的持仓，先换算股数、成本和价位，现金分红入账。
            # 不只看上次结算之后：除权日当天分红记录还没到、第二天才到时，这次补记（apply_dividend 按代码和日期去重）
            for code in list(acc.positions):
                check_unexplained_ex(code, bars.get(code))
                for ex, cash, ratio in cache.dividends(code):
                    pos = acc.positions.get(code)
                    if pos is not None and pos.entry_date < ex <= d:
                        act = acc.apply_dividend(ex, code, cash, ratio)
                        if act:
                            late = "（补记：除权日当天还没有分红记录）" if ex < d and (
                                acc.last_trade_day is not None and ex <= acc.last_trade_day) else ""
                            warnings.append(f"{name} 持仓 {code} 于 {ex.isoformat()} 除权除息：股数 "
                                            f"{act['qty_before']} → {act['qty_after']}，税后分红 {act['cash']:.2f} 元"
                                            + late)
        broker.execute_day(acc, d, bars, picks, calendar, per_pick)
        # 先存账户再记净值：中途中断时，重跑看到 last_trade_day == d，execute_day 不会重复成交，只补记净值
        store.save_account(acc)
        prices = {c: v for c in acc.positions if (v := _valuation(cache, c, d)) is not None}
        store.record_nav(name, d, acc.equity(prices))

    for status in ("incumbent", "shadow"):
        for strat in store.strategies_with_status(status):
            name = f"q:{strat}"
            if _settled(store, name, d):
                continue
            try:
                picks = load_strategy(strat).select(view)[: settings.max_picks]
            except Exception as e:  # noqa: BLE001 — 单个策略出错不阻塞其他账户
                common.log.exception("strategy %s failed on %s", strat, d)
                warnings.append(f"策略 {strat} 选股失败，当日跳过：{type(e).__name__}: {e}")
                continue
            raw = raw_bars(p.code for p in picks)
            run(name, [_to_raw(p, qfq_bars.get(p.code), raw.get(p.code)) for p in picks])

    for name in common.LLM_ACCOUNTS:
        if not _settled(store, name, d):
            rec = store.load_recommendation(d, name)
            picks = rec[0] if rec else []
            ref = rec[1].get("ref_close") if rec else None
            if ref is not None:  # 盘前记下了每只股票写价位时的昨收：与当天参考昨收比较
                raw = raw_bars(p.code for p in picks)
                picks = [_rescale_pick(p, ref.get(p.code), raw.get(p.code)) for p in picks]
            elif picks:  # 没有 ref_close 的旧推荐：按分红记录换算
                picks = [_ex_adjust_pick(p, cache, d) for p in picks]
            run(name, picks)

    if not _settled(store, common.BENCH_ACCOUNT, d):
        store.record_nav(common.BENCH_ACCOUNT, d, bench_equity)
    return warnings


def settle_and_decide(d: date, settings, store, cache, calendar, notifier, div_failed=frozenset(),
                      run_date: date | None = None) -> None:
    warnings = settle_day(d, settings, store, cache, calendar, div_failed, run_date)
    if warnings:
        notifier.send_text(format_alert(f"结算警告 {d.isoformat()}", "\n".join(warnings)))
    for ev in promotion.decide(store, d, calendar):
        notifier.send_text(format_alert(f"策略{KIND_CN.get(ev.kind, ev.kind)}",
                                        f"{ev.strategy}：{ev.detail}"))


def main(now: datetime | None = None) -> list[date]:
    """补跑所有待结算交易日；返回本次结算成功的日期。某日失败即停止（次日依赖前日持仓）。"""
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    days = pending_days(store, calendar, now)
    if not days:
        return []
    refresh_universe(store, client)
    # 待结算日的推荐代码也刷新：股票池外的推荐缺 d 日 K 线时，结算要能发现并暂停，而不是当作停牌
    codes = sorted(set(tracked_codes(store)) | recommended_codes(store, days))
    if not common.run_job("settle_data", days[-1],
                          lambda: refresh_data(codes, days[-1], client, cache),
                          store, notifier, force=True):
        return []
    done, div_failed = [], set()
    for d in days:
        failed = frozenset(refresh_dividends(store, client, cache, d))  # 截至前一日的持仓与 d 日推荐
        div_failed.update(failed)
        ok = common.run_job("settle", d, lambda d=d, failed=failed: settle_and_decide(
            d, settings, store, cache, calendar, notifier, failed, now.date()), store, notifier)
        if not ok:
            break
        done.append(d)
    if done:
        div_failed.update(refresh_dividends(store, client, cache))  # 当天新买入的持仓：供次日盘中监控
    if div_failed:
        notifier.send_text(format_alert("除权数据获取失败", f"{', '.join(sorted(div_failed))}：分红记录今天没有"
                                        "更新；如果持仓当天除权除息，结算会暂停，下次运行时重试"))
    return done


if __name__ == "__main__":
    main()
