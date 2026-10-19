"""盘前两段式：08:30 scan 出量化候选并抓资讯，随后 analyze 让 Claude 深度分析并写下竞价条件；
09:25 集合竞价结束后 run_final 先按条件做规则过滤，再做一次只能否决的竞价确认，09:28 前推送。"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import logging
import math
import threading
import time
from dataclasses import replace
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ValidationError, field_validator

from agents import playbook
from agents.llm import LLM
from config import Settings
from data.akshare_client import AkClient
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.dataview import DataView
from engine.rules import limit_pct, limit_prices
from engine.store import Store
from engine.types import Pick
from notify.format import format_recommendation

log = logging.getLogger(__name__)

CANDIDATE_COUNT = 15
NEWS_LIMIT = 30
STOCK_NEWS_LIMIT = 3
HISTORY_DAYS = 400       # 自然日，约 270 个交易日，够常见的一年期因子
PRICE_BAND = 0.5         # 计划价位须在量化 entry_max 的 ±50% 内
ANALYZE_TIMEOUT = 900.0  # 08:30 深度分析单次上限（秒）
AUCTION_TIMEOUT = 90.0   # 09:25 竞价确认单次上限（秒）
FINAL_DEADLINE = 110.0   # run_final 从开始到定稿的总时长上限（秒）；09:25:15 启动则约 09:27:05 推送
SPOT_TIMEOUT = 40.0      # run_final 等待竞价快照的上限（秒），超时按竞价数据缺失处理
KINDS = ("active", "candidate")
NOTE_UNCONFIRMED = "（竞价二次确认未完成，按盘前条件过滤）"
NOTE_NONE_PASSED = "（竞价条件均未满足，今日不推荐）"
NOTE_ALL_VETOED = "（盘前分析全部否决，今日不推荐）"
MIN_ANALYZE_SECONDS = 120.0  # 离截止不到这么多秒就不再发起深度分析
SPOT_MISSING = "（竞价数据缺失：未取到竞价快照，以下股票未经竞价条件检查，请依据盘前分析判断，并在 note 中注明）"


class PlannedPick(BaseModel):
    code: str
    action: Literal["keep", "veto"]
    confidence: float
    entry_max: float
    stop: float
    target: float
    min_gap_pct: float
    max_gap_pct: float
    reason: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _unit_interval(cls, v):
        """提示词要求 0~1；模型偶尔写成百分数（85）或越界：统一换算并截断，免得排序被打乱。"""
        v = float(v)
        if 1.0 < v <= 100.0:
            v /= 100.0
        return min(max(v, 0.0), 1.0)


class PremarketPlan(BaseModel):
    market_view: str
    picks: list[PlannedPick]


class AuctionDecision(BaseModel):
    code: str
    action: Literal["keep", "veto"]
    reason: str


class AuctionCheck(BaseModel):
    note: str
    picks: list[AuctionDecision]


AUCTION_SYSTEM = """你是 A 股集合竞价复核员。08:30 的盘前深度分析已为每只候选写好竞价条件和价位；09:25 集合竞价结束后，系统已按这些条件做了规则过滤，下面列出的是通过过滤的股票。

你的任务是结合竞价数据做最后确认，对每只股票给出 keep 或 veto。你只能否决，不能新增股票，也不能修改价位。

出现以下情况之一时 veto，并在 reason 中写明依据的数字：
1. 竞价成交额占 20 日均额的比例明显低于同批其他股票，说明资金不认可。
2. 竞价价距涨停价不足 2%，开盘可能直接封板，上行空间也有限。
3. 竞价表现与盘前理由的前提明显矛盾，例如盘前预期温和高开，实际却放量低开。
4. 候选整体竞价明显偏弱（低开家数远多于高开家数，中位竞价涨幅低于 -1%），且该股缺乏独立的支撑理由。

没有明确负面证据时一律 keep。只依据输入中的数字和文字判断，不要编造新闻或数据。如果输入注明竞价数据缺失，就只依据盘前分析判断，并在 note 中说明。

输出要求：note 用一两句话概括今日竞价情况（60 字以内）；picks 对列出的每只股票各给一条，code 与输入完全一致，reason 40 字以内。"""


# ---------- 08:30 scan（与上一版相同） ----------

def _safe(fn, default):
    """资讯类抓取失败不阻断盘前流程。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("premarket fetch failed: %r", e)
        return default


def _jsonable(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _notices(client: AkClient, days: list[dt.date], codes: list[str]) -> list[dict]:
    frames = [_safe(lambda day=day: client.notices(day), None) for day in days]
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df = df[df["code"].astype(str).isin(codes)].drop_duplicates(subset=["code", "title"])
    return _jsonable(df[["code", "name", "title", "type"]].to_dict("records"))


def _stats(view: DataView, code: str) -> dict:
    h = view.history(code, 20)
    if h.empty:
        return {}
    return {"prev_close": float(h["close"].iloc[-1]), "avg_amount20": float(h["amount"].mean())}


def scan(d: dt.date, settings: Settings, store: Store, cache: BarCache, client: AkClient, strategy,
         calendar: TradingCalendar) -> list[Pick]:
    asof = calendar.prev_day(d)
    codes = client.universe()
    cache.update(codes, asof)  # 增量：已是最新的代码不发请求
    panel, names = cache.load_panel(codes, asof - dt.timedelta(days=HISTORY_DAYS), asof)
    view = DataView(panel, names, asof)
    picks = strategy.select(view)[:CANDIDATE_COUNT]
    cand = [p.code for p in picks]
    context = {
        "news": _jsonable(_safe(lambda: client.news_global(NEWS_LIMIT), [])),
        "notices": _notices(client, [asof, d], cand),  # 晚间公告可能记在次日，两天都取
        "stock_news": {c: _jsonable(_safe(lambda c=c: client.stock_news(c, limit=STOCK_NEWS_LIMIT), [])
                                    [:STOCK_NEWS_LIMIT]) for c in cand},
        "stats": {c: _stats(view, c) for c in cand},
    }
    store.save_candidates(d, picks, context)
    log.info("premarket scan %s: %d candidates", d, len(picks))
    return picks


def _load_candidates(d: dt.date, store: Store) -> tuple[list[Pick], dict]:
    loaded = store.load_candidates(d)
    if loaded is None:
        raise RuntimeError(f"{d} 没有盘前候选（premarket_scan 未成功运行）")
    return loaded


def _plan_key(d: dt.date, kind: str) -> str:
    return f"plan:{d.isoformat()}:{kind}"


def load_plan(store: Store, d: dt.date, kind: str) -> PremarketPlan | None:
    """读回 analyze 存下的计划；没有或无法解析时返回 None。"""
    raw = store.kv_get(_plan_key(d, kind))
    if raw is None:
        return None
    try:
        return PremarketPlan.model_validate(raw)
    except ValidationError as e:
        log.warning("premarket plan %s %s unreadable: %r", d, kind, e)
        return None


# ---------- 提示词片段 ----------

def _f(x) -> float | None:
    """转为 float；缺失、NaN 或无法转换时为 None。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _num(x, scale: float = 1.0, unit: str = "") -> str:
    v = _f(x)
    return "未知" if v is None else f"{v / scale:.2f}{unit}"


def _rel(price: float, base) -> str:
    b = _f(base)
    return f"（{price / b - 1:+.1%}）" if b and b > 0 else ""


def _limits(p: Pick, prev_close) -> str:
    pct = limit_pct(p.code, p.name)
    pc = _f(prev_close)
    if not pc or pc <= 0:
        return f"涨跌幅限制 ±{pct:.0%}"
    up, down = limit_prices(p.code, p.name, pc)
    return f"涨跌幅限制 ±{pct:.0%}（涨停 {up:.2f} / 跌停 {down:.2f}）"


def _item(n: dict, width: int = 100) -> str:
    title, content = str(n.get("title") or ""), str(n.get("content") or "")
    body = f"{title}：{content[:width]}" if title and content else (title or content[:width])
    return f"[{n.get('time', '')}] {body}"


def _analyze_prompt(d: dt.date, pb: dict, cands: list[Pick], ctx: dict) -> str:
    stats = ctx.get("stats", {})
    lines = [f"# 交易日 {d.isoformat()}（08:30 盘前，集合竞价尚未开始）", "",
             f"## 经验规则（playbook v{pb.get('version', 1)}）", playbook.render(pb), "",
             f"## 候选股票（量化排序，共 {len(cands)} 只）"]
    for p in cands:
        s = stats.get(p.code, {})
        pc = s.get("prev_close")
        lines.append(f"- {p.code} {p.name}｜量化分 {p.score:.3f}｜昨收 {_num(pc)}｜{_limits(p, pc)}"
                     f"｜20日均额 {_num(s.get('avg_amount20'), 1e8, '亿')}"
                     f"｜量化价位 买≤{p.entry_max:.2f}{_rel(p.entry_max, pc)} 止损 {p.stop:.2f}{_rel(p.stop, pc)}"
                     f" 止盈 {p.target:.2f}{_rel(p.target, pc)}｜持有 {p.hold_days} 日｜{p.reason}")
    notices = [f"- {n['code']} {n['name']}：{n['title']}（{n['type']}）" for n in ctx.get("notices", [])]
    stock_news = [f"- {c}：" + "；".join(_item(n, 60) for n in items)
                  for c, items in ctx.get("stock_news", {}).items() if items]
    news = [f"- {_item(n)}" for n in ctx.get("news", [])]
    lines += ["", "## 候选相关公告", *(notices or ["（无）"])]
    lines += ["", "## 候选个股新闻", *(stock_news or ["（无）"])]
    lines += ["", "## 隔夜要闻（财联社电报）", *(news or ["（无）"])]
    lines += ["", f"请对以上 {len(cands)} 只候选逐一给出 action、confidence、价位与竞价条件"
                  "（min_gap_pct / max_gap_pct），code 与上表完全一致。"]
    return "\n".join(lines)


def _spot_rows(spot: pd.DataFrame | None, codes: list[str]) -> dict[str, dict]:
    if spot is None or spot.empty or "code" not in spot.columns:
        return {}
    sub = spot[spot["code"].astype(str).isin(codes)]
    return {str(r["code"]): r for r in sub.to_dict("records")}


def _gap_pct(row: dict) -> float | None:
    """竞价涨幅（%），四舍五入到 0.01 个百分点；竞价价或昨收缺失、非正（多为停牌）时为 None。"""
    op, pc = _f(row.get("open")), _f(row.get("prev_close"))
    if op is None or pc is None or op <= 0 or pc <= 0:
        return None
    return round((op / pc - 1) * 100, 2)


def _breadth(spot: pd.DataFrame | None) -> str:
    if spot is None or spot.empty or not {"open", "prev_close"} <= set(spot.columns):
        return SPOT_MISSING
    op = pd.to_numeric(spot["open"], errors="coerce")
    pc = pd.to_numeric(spot["prev_close"], errors="coerce")
    ok = (op > 0) & (pc > 0)
    if not ok.any():
        return SPOT_MISSING
    gap = (op[ok] / pc[ok] - 1) * 100
    return (f"有竞价 {int(ok.sum())} 只：高开 {int((gap > 0).sum())}，低开 {int((gap < 0).sum())}，"
            f"中位竞价涨幅 {gap.median():+.2f}%")


def _auction(row: dict | None, avg_amount20=None) -> str:
    if row is None:
        return "无竞价数据"
    gap = _gap_pct(row)
    if gap is None:
        return "无竞价（可能停牌）"
    amt, avg = _f(row.get("amount")), _f(avg_amount20)
    share = f"，占20日均额 {amt / avg:.2%}" if amt is not None and avg else ""
    return f"竞价 {_f(row.get('open')):.2f}（{gap:+.2f}%），竞价额 {_num(amt, 1e4, '万')}{share}"


def _auction_prompt(d: dt.date, plan: PremarketPlan, prelim: list[Pick], ctx: dict,
                    spot: pd.DataFrame | None) -> str:
    stats = ctx.get("stats", {})
    rows = _spot_rows(spot, [p.code for p in prelim])
    planned = {pp.code.strip(): pp for pp in plan.picks}
    lines = [f"# 交易日 {d.isoformat()}（09:25 集合竞价已结束）", "",
             "## 盘前市场观点", plan.market_view or "（无）", "",
             "## 候选整体竞价", _breadth(spot), "",
             f"## 通过竞价条件的股票（{len(prelim)} 只，按盘前信心排序）"]
    for p in prelim:
        pp, row, s = planned[p.code], rows.get(p.code), stats.get(p.code, {})
        pc = row.get("prev_close") if row is not None else s.get("prev_close")
        lines.append(f"- {p.code} {p.name}｜{_auction(row, s.get('avg_amount20'))}｜{_limits(p, pc)}"
                     f"｜盘前条件 竞价涨幅 {pp.min_gap_pct:+.2f}%~{pp.max_gap_pct:+.2f}%，买≤{p.entry_max:.2f}"
                     f"｜止损 {p.stop:.2f} 止盈 {p.target:.2f}｜盘前信心 {pp.confidence:.2f}｜盘前理由：{pp.reason}")
    lines += ["", f"请对以上 {len(prelim)} 只逐一给出 keep 或 veto，code 与上表完全一致。"]
    return "\n".join(lines)


# ---------- 08:30 深度分析 ----------

def analyze(d: dt.date, settings: Settings, store: Store, llm: LLM, playbook_kind: str,
            timeout: float = ANALYZE_TIMEOUT) -> PremarketPlan | None:
    """为每只候选给出 keep/veto、价位与竞价条件，存入 kv。任何异常都记录日志并返回 None。"""
    try:
        cands, ctx = _load_candidates(d, store)
        if not cands:  # 策略当天一只都没选出来：不花深度分析的钱
            log.info("premarket analyze %s: no candidates", playbook_kind)
            return None
        raw = llm.parse(purpose=f"analyze:{playbook_kind}", system=playbook.load_prompt("premarket"),
                        user=_analyze_prompt(d, playbook.load(playbook_kind), cands, ctx), schema=PremarketPlan,
                        effort="high", timeout=timeout, max_budget_usd=1.5, essential=True)
        codes = {p.code for p in cands}
        picks: list[PlannedPick] = []
        for pp in raw.picks:
            code = pp.code.strip()
            if code in codes and code not in {x.code for x in picks}:  # 丢弃候选外代码，重复代码保留第一条
                picks.append(pp.model_copy(update={"code": code}))
        if not picks:
            log.warning("premarket analyze %s: plan contains no candidate code", playbook_kind)
            return None
        plan = PremarketPlan(market_view=raw.market_view.strip(), picks=picks)
        store.kv_set(_plan_key(d, playbook_kind), plan.model_dump())
    except Exception as e:  # noqa: BLE001 - 超时/预算/CLI 报错/校验失败：09:25 该路退回量化
        log.warning("premarket analyze %s failed: %r", playbook_kind, e)
        return None
    log.info("premarket analyze %s %s: %d picks, %d keep", d, playbook_kind, len(picks),
             sum(p.action == "keep" for p in picks))
    return plan


def run_analyze(d: dt.date, settings: Settings, store: Store, llm: LLM, deadline: dt.datetime | None = None,
                now: dt.datetime | None = None) -> dict[str, bool]:
    """两套 playbook 各一个线程并发分析，返回 {"active": ok, "candidate": ok}。

    deadline（墙钟时刻）给出时，单次超时取 ANALYZE_TIMEOUT 与剩余秒数的较小者，剩余不到 MIN_ANALYZE_SECONDS
    就不发起：开机晚、补跑扫描时，分析不能拖到 09:25 定稿之后。"""
    timeout = ANALYZE_TIMEOUT
    if deadline is not None:
        timeout = min(ANALYZE_TIMEOUT, (deadline - (now or dt.datetime.now())).total_seconds())
        if timeout < MIN_ANALYZE_SECONDS:
            log.warning("premarket analyze skipped: only %.0fs left before %s", timeout, deadline.time())
            return {k: False for k in KINDS}
    with cf.ThreadPoolExecutor(max_workers=len(KINDS), thread_name_prefix="analyze") as pool:
        futures = {k: pool.submit(analyze, d, settings, store, llm, k, timeout) for k in KINDS}
        return {k: fut.result() is not None for k, fut in futures.items()}


# ---------- 09:25 竞价定稿 ----------

def _merge(q: Pick, pp: PlannedPick) -> Pick:
    """计划价位（四舍五入到分）满足 stop < entry_max < target 且都在量化 entry_max 的 ±PRICE_BAND 内才采用。"""
    entry, stop, target = (round(x, 2) for x in (pp.entry_max, pp.stop, pp.target))
    lo, hi = q.entry_max * (1 - PRICE_BAND), q.entry_max * (1 + PRICE_BAND)
    if not (stop < entry < target and all(lo <= x <= hi for x in (stop, entry, target))):
        entry, stop, target = q.entry_max, q.stop, q.target
    return Pick(code=q.code, name=q.name, score=float(pp.confidence), entry_max=entry, stop=stop, target=target,
                hold_days=q.hold_days, reason=pp.reason.strip() or q.reason)


def auction_filter(plan: PremarketPlan, cands: list[Pick], spot: pd.DataFrame | None) -> list[Pick]:
    """确定性规则：取计划中 action=keep 且仍在候选里的股票；有竞价行时要求竞价涨幅在
    [min_gap_pct, max_gap_pct] 内，且最终采用的 stop < 竞价价 <= 最终采用的 entry_max（与撮合口径一致）；
    没有竞价行则保留。按 confidence 降序。"""
    by_code = {p.code: p for p in cands}
    rows = _spot_rows(spot, list(by_code))
    out: list[Pick] = []
    seen: set[str] = set()
    for pp in sorted(plan.picks, key=lambda x: x.confidence, reverse=True):  # 稳定排序：同分保持计划顺序
        code = pp.code.strip()
        if pp.action != "keep" or code not in by_code or code in seen:
            continue
        seen.add(code)
        pick = _merge(by_code[code], pp)
        row = rows.get(code)
        if row is not None:
            gap, op = _gap_pct(row), _f(row.get("open"))
            if gap is None or not pp.min_gap_pct <= gap <= pp.max_gap_pct or not pick.stop < op <= pick.entry_max:
                continue
        out.append(pick)
    return out


def _ex_adjust(cands: list[Pick], plan: PremarketPlan | None, ctx: dict, spot: pd.DataFrame | None
               ) -> tuple[list[Pick], PremarketPlan | None]:
    """盘前价位按前一日收盘价写成。除权除息日，竞价快照的昨收是除权参考价，与候选统计里的昨收不同：按两者的
    比例换算候选和计划的买入上限、止损、目标价（派息时比例换算与精确的减法略有出入，对盘前价位足够）。
    数据已经按新基准复权时两个昨收相同，不换算。"""
    stats = ctx.get("stats", {})
    factor = {}
    for code, row in _spot_rows(spot, [p.code for p in cands]).items():
        new, old = _f(row.get("prev_close")), _f(stats.get(code, {}).get("prev_close"))
        if new and old and new > 0 and old > 0 and abs(new - old) > 0.011:
            factor[code] = new / old
    if not factor:
        return cands, plan

    def scaled(obj, k):  # 舍入到分：推送显示的价位与过滤、撮合用的价位一致
        return {"entry_max": round(obj.entry_max * k, 2), "stop": round(obj.stop * k, 2),
                "target": round(obj.target * k, 2)}

    cands = [replace(p, **scaled(p, factor[p.code])) if p.code in factor else p for p in cands]
    if plan is not None:
        plan = plan.model_copy(update={"picks": [
            pp.model_copy(update=scaled(pp, factor[pp.code.strip()])) if pp.code.strip() in factor else pp
            for pp in plan.picks]})
    log.info("premarket ex-rights price conversion: %s", {c: round(k, 4) for c, k in factor.items()})
    return cands, plan


def _ref_close(picks: list[Pick], ctx: dict, spot: pd.DataFrame | None) -> dict[str, float]:
    """推荐价位对应的昨收：有竞价快照昨收时价位已按它换算（或本来就一致），否则是扫描时的昨收。结算用它与当天
    交易所的参考昨收比较，不一致（除权除息）时按比例换算。"""
    rows, stats = _spot_rows(spot, [p.code for p in picks]), ctx.get("stats", {})
    out = {}
    for p in picks:
        pc = _f(rows.get(p.code, {}).get("prev_close")) or _f(stats.get(p.code, {}).get("prev_close"))
        if pc and pc > 0:
            out[p.code] = pc
    return out


def _rule_result(settings: Settings, plan: PremarketPlan | None, cands: list[Pick],
                 spot: pd.DataFrame | None) -> tuple[list[Pick], bool, str]:
    """不调用 LLM 的确定性结果：无计划用量化；有计划用规则过滤。也是竞价确认失败或超时时的结果。"""
    if plan is None:
        return cands[: settings.max_picks], False, ""
    prelim = auction_filter(plan, cands, spot)[: settings.max_picks]
    if not prelim:
        vetoed_all = not any(pp.action == "keep" for pp in plan.picks)
        return [], True, plan.market_view + (NOTE_ALL_VETOED if vetoed_all else NOTE_NONE_PASSED)
    return prelim, True, plan.market_view + NOTE_UNCONFIRMED


def finalize(d: dt.date, settings: Settings, store: Store, llm: LLM, client: AkClient, playbook_kind: str,
             timeout: float = AUCTION_TIMEOUT, spot: pd.DataFrame | None = None) -> tuple[list[Pick], bool, str]:
    """返回 (picks, llm_reviewed, market_view)。spot=None 时自行拉取快照；传入空表表示竞价数据缺失。"""
    cands, ctx = _load_candidates(d, store)
    plan = load_plan(store, d, playbook_kind)
    if plan is not None and spot is None:
        spot = _safe(lambda: client.spot([p.code for p in cands]), None)
    cands, plan = _ex_adjust(cands, plan, ctx, spot)
    base = _rule_result(settings, plan, cands, spot)
    if plan is None or not base[0]:
        return base
    prelim = base[0]
    try:
        check = llm.parse(purpose=f"auction:{playbook_kind}", system=AUCTION_SYSTEM,
                          user=_auction_prompt(d, plan, prelim, ctx, spot), schema=AuctionCheck,
                          effort="low", timeout=timeout, max_budget_usd=0.3, essential=True)
    except Exception as e:  # noqa: BLE001 - 超时/预算/CLI 报错/校验失败：按规则过滤结果
        log.warning("premarket auction check %s failed, using rule-filtered picks: %r", playbook_kind, e)
        return base
    vetoed = {x.code.strip() for x in check.picks if x.action == "veto"}
    picks = [p for p in prelim if p.code not in vetoed]  # 只能否决：不在 prelim 里的代码一律忽略
    if len(picks) < len(prelim):
        log.info("premarket auction %s vetoed %s", playbook_kind, [p.code for p in prelim if p.code in vetoed])
    note = check.note.strip()
    return picks, True, plan.market_view + (f"（竞价确认：{note}）" if note else "")


def _playbook_version(kind: str) -> int | None:
    try:  # 只为记录版本号：playbook 文件出问题也不能耽误 09:28 前推送
        return int(playbook.load(kind).get("version", 1))
    except Exception:  # noqa: BLE001
        log.exception("playbook %s unreadable", kind)
        return None


def _fetch_spot(client: AkClient, timeout: float, codes: list[str]) -> pd.DataFrame:
    """在守护线程里拉一次候选的竞价快照（逐只取个股报价，几秒内完成；全市场快照要将近一分钟），
    最多等 timeout 秒。超时时用已经取到的那部分（某一只卡住不会让其余候选的竞价数据作废）；
    一只都没取到时返回空表（按竞价数据缺失处理）。"""
    box: list[pd.DataFrame | None] = []
    rows: list[dict] = []
    worker = threading.Thread(target=lambda: box.append(_safe(lambda: client.spot(codes, sink=rows), None)),
                              name="premarket-spot", daemon=True)
    worker.start()
    worker.join(timeout)
    if box and box[0] is not None:
        return box[0]
    if rows:
        log.warning("auction spot partial within %.0fs: %d/%d codes", timeout, len(rows), len(codes))
        return pd.DataFrame(list(rows))
    log.warning("auction spot unavailable within %.0fs, treating auction data as missing", timeout)
    return pd.DataFrame()


def run_final(d: dt.date, settings: Settings, store: Store, llm: LLM, client: AkClient, notifier,
              calendar: TradingCalendar) -> None:
    """09:25 定稿：拉一次快照，两路并发 finalize；FINAL_DEADLINE 内没完成的那一路用确定性结果。只推送 h:active。"""
    deadline = time.monotonic() + FINAL_DEADLINE
    cands, ctx = _load_candidates(d, store)  # 没有候选直接抛错，由 run_job 告警
    plans = {k: load_plan(store, d, k) for k in KINDS}
    spot = pd.DataFrame()  # 两路都没有计划时用不到快照
    if any(p is not None for p in plans.values()):
        spot = _fetch_spot(client, min(SPOT_TIMEOUT, max(0.0, deadline - time.monotonic())), [p.code for p in cands])
    fallback = {k: _rule_result(settings, *reversed(_ex_adjust(cands, plans[k], ctx, spot)), spot) for k in KINDS}
    timeout = min(AUCTION_TIMEOUT, max(1.0, deadline - time.monotonic()))
    pool = cf.ThreadPoolExecutor(max_workers=len(KINDS), thread_name_prefix="premarket")
    futures = {k: pool.submit(finalize, d, settings, store, llm, client, k, timeout, spot) for k in KINDS}
    results: dict[str, tuple[list[Pick], bool, str]] = {}
    try:
        for kind, fut in futures.items():
            try:
                results[kind] = fut.result(timeout=max(0.0, deadline - time.monotonic()))
            except cf.TimeoutError:
                log.warning("premarket %s not final within %.0fs, using rule result", kind, FINAL_DEADLINE)
                results[kind] = fallback[kind]
            except Exception as e:  # noqa: BLE001
                log.warning("premarket finalize %s failed: %r, using rule result", kind, e)
                results[kind] = fallback[kind]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # 超时的线程留在后台跑完，结果丢弃
    for kind, (picks, reviewed, view) in results.items():
        meta = {"llm_reviewed": reviewed, "market_view": view,
                "playbook_version": _playbook_version(kind),
                "ref_close": _ref_close(picks, ctx, spot)}  # 结算据此判断价位是否需要按除权换算
        store.save_recommendation(d, f"h:{kind}", picks, meta)
    picks, reviewed, view = results["active"]
    notifier.send_markdown(format_recommendation(d, picks, reviewed, view))
