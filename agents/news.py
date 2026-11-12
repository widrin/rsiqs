"""消息面选股（影子通道）：隔夜财经新闻 → LLM 提取题材与个股 → 按基线价位模板生成 Pick。

只用于前向测试：推荐写入独立的影子账户 h:news，不推送、不影响现有量化/LLM 盘前流程。
LLM 只负责从新闻里挑代码和说明理由，价位由本模块按昨收统一计算；代码必须属于股票池并通过
与基线相同的硬过滤（ST、数据有效、非涨停、20 日均额），LLM 给的名称和价格一概不用。
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from pydantic import BaseModel, field_validator

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick

log = logging.getLogger(__name__)

NEWS_LIMIT = 30          # 财联社电报一次最多返回 20 条
PICK_COUNT = 5           # 最多选 5 只：与回测和结算取的条数一致
MIN_ROWS = 20            # 至少 20 根 K 线：算得动 20 日均额
MIN_AMOUNT = 1e8         # 20 日均额下限，与基线策略一致
HOLD_DAYS = 5
ENTRY_FACTOR = 1.03      # 价位模板与 strategies.baseline_momo 相同，便于和现任策略对比
STOP_FACTOR = 0.93
TARGET_FACTOR = 1.10
TIMEOUT = 600.0

NEWS_SYSTEM = """你是 A 股消息面选股员。输入是隔夜到今晨的财经新闻，以及一份可交易股票池（代码列表）。

任务：从新闻里找出直接受益、近期可能有资金关注的个股，输出 3~5 只，按信心从高到低排序。

要求：
1. 只从给定股票池里选，代码必须与池中完全一致；拿不准某只股票是否属于池中时不要选。
2. 只依据给定新闻判断，不要编造新闻、公告或财务数据。不要给出任何价位、目标价或涨跌幅预测。
3. reason 用一句话说明是哪条新闻通过什么逻辑受益（40 字以内）。
4. confidence 取 0~1，表示这条新闻的力度与你对受益映射的把握。
5. 新闻没有明确受益标的时宁可少选，甚至返回空列表。

market_view 用一两句话概括今日消息面主线（60 字以内）。"""


class NewsPick(BaseModel):
    code: str
    reason: str = ""
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v):
        """模型偶尔写成百分数或越界：百分数换算、其余截断；非数值按中性的 0.5 处理，不整份丢弃。"""
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.5
        if not math.isfinite(x):
            return 0.5
        if 2.0 <= x <= 100.0:
            x /= 100.0
        return min(max(x, 0.0), 1.0)


class NewsPlan(BaseModel):
    market_view: str = ""
    picks: list[NewsPick] = []


def collect(client) -> list[dict]:
    """财联社电报；取不到时返回空列表（影子通道失败不告警、不阻塞其他任务）。"""
    try:
        return list(client.news_global(NEWS_LIMIT) or [])
    except Exception as e:  # noqa: BLE001
        log.warning("news fetch failed: %r", e)
        return []


def _news_lines(items: list[dict]) -> list[str]:
    out = []
    for n in items[:NEWS_LIMIT]:
        title, content = str(n.get("title") or ""), str(n.get("content") or "")
        body = f"{title}：{content[:120]}" if title and content else (title or content[:120])
        out.append(f"- [{n.get('time', '')}] {body}")
    return out


def _prompt(d: dt.date, items: list[dict], codes: list[str]) -> str:
    return "\n".join([
        f"# 交易日 {d.isoformat()}（08:35 盘前）",
        "",
        f"## 隔夜到今晨的新闻（{len(items)} 条，最新的在前）",
        *(_news_lines(items) or ["（无）"]),
        "",
        f"## 可交易股票池（{len(codes)} 只，只能从中选）",
        " ".join(codes),
        "",
        f"请输出 market_view 与 picks（最多 {PICK_COUNT} 只，code 必须在上面的股票池里）。",
    ])


def make_plan(llm, d: dt.date, items: list[dict], codes: list[str],
              timeout: float = TIMEOUT) -> NewsPlan | None:
    """一次 LLM 调用；超时、网络错误、校验失败都返回 None。"""
    try:
        return llm.parse(purpose="news", system=NEWS_SYSTEM,
                         user=_prompt(d, items, codes), schema=NewsPlan, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - 影子通道：失败就当今天没有推荐
        log.warning("news plan failed: %r", e)
        return None


def _close(view: DataView, code: str, name: str) -> float | None:
    """通过硬过滤时返回 asof 收盘价，否则返回 None。过滤条件与基线策略完全一致。"""
    if is_st(name):
        return None
    h = view.history(code, MIN_ROWS)
    if len(h) < MIN_ROWS or h.index[-1].date() != view.asof:  # 数据不足或 asof 停牌
        return None
    if not h["amount"].iloc[-20:].mean() >= MIN_AMOUNT:  # NaN 也排除
        return None
    last = h.iloc[-1]
    close, prev = float(last["close"]), float(last["prev_close"])
    if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
        return None
    up, _ = limit_prices(code, name, prev, view.asof)
    return None if at_limit_up(close, up) else close


def build_picks(plan: NewsPlan, view: DataView) -> list[Pick]:
    """按信心排序生成推荐：丢弃股票池外的代码、重复代码和不合格（ST/停牌/低流动性/涨停）的股票。"""
    listed = set(view.codes())
    out: list[Pick] = []
    seen: set[str] = set()
    for np in sorted(plan.picks, key=lambda x: x.confidence, reverse=True):
        code = np.code.strip()
        if code in seen or code not in listed:
            if code and code not in listed:
                log.info("news pick %s dropped: not in universe", code)
            continue
        name = view.name(code)
        if not name:
            continue
        close = _close(view, code, name)
        if close is None:
            log.info("news pick %s %s dropped: failed hard filter", code, name)
            continue
        seen.add(code)
        out.append(Pick(code=code, name=name, score=float(np.confidence),
                        entry_max=round(close * ENTRY_FACTOR, 2), stop=round(close * STOP_FACTOR, 2),
                        target=round(close * TARGET_FACTOR, 2), hold_days=HOLD_DAYS,
                        reason=f"消息面：{np.reason.strip()}" if np.reason.strip() else "消息面选股"))
    return out[:PICK_COUNT]


def select(llm, client, d: dt.date, view: DataView, codes: list[str]) -> tuple[list[Pick], dict]:
    """采集新闻 → LLM 选股 → 硬过滤后生成推荐，返回 (picks, meta)。任何环节失败都返回空列表。"""
    items = collect(client)
    plan = make_plan(llm, d, items, codes) if items else None
    if plan is None:
        return [], {"source": "news", "news_count": len(items), "market_view": "", "raw_codes": [], "ref_close": {}}
    picks = build_picks(plan, view)
    ref = {}
    for p in picks:
        h = view.history(p.code, 1)
        if not h.empty:
            ref[p.code] = float(h["close"].iloc[-1])
    meta = {"source": "news", "news_count": len(items), "market_view": plan.market_view.strip(),
            "raw_codes": [x.code.strip() for x in plan.picks], "ref_close": ref}
    return picks, meta
