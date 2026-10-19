import datetime as dt
import json
import subprocess
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from agents import playbook, premarket
from agents.llm import BudgetExceeded, LLMError
from agents.premarket import AuctionCheck, AuctionDecision, PlannedPick, PremarketPlan
from pydantic import ValidationError
from config import Settings
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick
from notify.format import format_recommendation

D = dt.date(2026, 10, 16)
PREV = dt.date(2026, 10, 15)
CODES = [f"600{i:03d}" for i in range(20)]
CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)
LLM_ERRORS = [LLMError("cli error"), BudgetExceeded("budget"), subprocess.TimeoutExpired("claude.exe", 90.0),
              ValueError("bad output")]


def settings(**kw) -> Settings:
    base = dict(anthropic_api_key="k", wecom_webhook_url="", root=Path("."), max_picks=3)
    base.update(kw)
    return Settings(**base)


def qpick(code: str, i: int = 0) -> Pick:
    return Pick(code=code, name=f"股{code}", score=100.0 - i, entry_max=10.3, stop=9.3, target=11.0,
                hold_days=5, reason=f"量化理由{code}")


CANDS = [qpick(c, i) for i, c in enumerate(CODES[:8])]
CTX = {"news": [{"title": "隔夜美股收涨", "content": "纳指涨 1%", "time": "2026-10-16 06:00:00"}],
       "notices": [{"code": "600000", "name": "股600000", "title": "关于股东减持计划的公告", "type": "减持"}],
       "stock_news": {"600001": [{"title": "600001 中标大单", "content": "", "time": "2026-10-15"}]},
       "stats": {c: {"prev_close": 10.0, "avg_amount20": 2e8} for c in CODES[:6]}}


def make_panel(codes, end=PREV, n=25):
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    cols = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 1e6, "amount": 2e8, "prev_close": 10.0}
    return {c: pd.DataFrame(cols, index=idx) for c in codes}, {c: f"股{c}" for c in codes}


class FakeAk:
    """AkClient 替身。spot() 默认竞价价 10.20（昨收 10.00，+2.00%）；opens 指定个股竞价价，missing 中的代码不出现在快照里。"""

    def __init__(self, spot_fail=False, opens=None, missing=(), prevs=None):
        self.spot_fail, self.opens, self.missing = spot_fail, dict(opens or {}), set(missing)
        self.prevs = dict(prevs or {})  # 指定个股快照里的昨收（除权除息日是除权参考价）
        self.calls = []

    def universe(self):
        return list(CODES)

    def news_global(self, limit=50):
        self.calls.append(("news_global", limit))
        return [{"title": "隔夜美股收涨", "content": "纳指涨 1%", "time": pd.Timestamp("2026-10-16 06:00")}]

    def stock_news(self, code, limit=5):
        return [{"title": f"{code} 新闻{i}", "content": "", "time": "2026-10-15"} for i in range(5)]

    def notices(self, d):
        return pd.DataFrame({"code": ["600000", "999999"], "name": ["股600000", "某股"],
                             "title": ["关于股东减持计划的公告", "无关公告"], "type": ["减持", "其他"]})

    def spot(self, codes=None, sink=None):
        self.calls.append(("spot",))
        self.spot_codes = None if codes is None else list(codes)
        if self.spot_fail:
            raise RuntimeError("spot down")
        codes = [c for c in (CODES if codes is None else codes) if c not in self.missing]
        opens = [self.opens.get(c, 10.2) for c in codes]
        return pd.DataFrame({"code": codes, "name": [f"股{c}" for c in codes], "open": opens,
                             "prev_close": [self.prevs.get(c, 10.0) for c in codes],
                             "last": opens, "pct": [(o / 10.0 - 1) * 100 for o in opens], "volume": 1e4,
                             "amount": 5e6, "high": opens})

    def spot_calls(self):
        return self.calls.count(("spot",))


class FakeCache:
    def __init__(self):
        self.updated = self.loaded = None

    def update(self, codes, end, start_default=dt.date(2015, 6, 1)):
        self.updated = (list(codes), end)

    def load_panel(self, codes, start, end):
        self.loaded = (start, end)
        return make_panel(codes, end)


class FakeStrategy:
    name = "fake"

    def __init__(self):
        self.asof = None

    def select(self, view):
        self.asof = view.asof
        return [qpick(c, i) for i, c in enumerate(sorted(view.codes()))]


class FakeLLM:
    """签名与 agents.llm.LLM.parse 一致；按 purpose 返回预设结果，值为 Exception 时抛出。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0, max_budget_usd=1.0,
              essential=False):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, max_budget_usd=max_budget_usd, essential=essential))
        r = self.results[purpose]
        if isinstance(r, Exception):
            raise r
        return r


class Recorder:
    def __init__(self):
        self.sent = []

    def send_markdown(self, content):
        self.sent.append(content)
        return True

    def send_text(self, content):
        self.sent.append(content)
        return True


@pytest.fixture(autouse=True)
def pbdir(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", tmp_path)
    (tmp_path / "premarket_system.md").write_text("盘前系统提示", encoding="utf-8")
    playbook.save("active", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "ACTIVE规则", "evidence": "e", "created": "x"}]})
    playbook.save("candidate", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "CANDIDATE规则", "evidence": "e", "created": "x"}]})
    return tmp_path


@pytest.fixture
def store():
    s = Store(":memory:")
    s.save_candidates(D, [qpick(c, i) for i, c in enumerate(CODES[:6])], CTX)
    return s


def pp(code, action="keep", conf=0.5, entry=10.25, stop=9.6, target=11.2, lo=-2.0, hi=3.0):
    return PlannedPick(code=code, action=action, confidence=conf, entry_max=entry, stop=stop, target=target,
                       min_gap_pct=lo, max_gap_pct=hi, reason=f"计划理由{code}")


def plan(*picks, view="偏强"):
    return PremarketPlan(market_view=view, picks=list(picks))


def save_plan(store, kind, p):
    store.kv_set(f"plan:{D.isoformat()}:{kind}", p.model_dump())


def check(actions: dict, note="竞价平稳"):
    return AuctionCheck(note=note, picks=[AuctionDecision(code=c, action=a, reason=f"{a}:{c}")
                                          for c, a in actions.items()])


# ---------- 08:30 scan（与上一版相同） ----------

def test_scan_saves_top_candidates_and_context():
    store, ak, cache, strat = Store(":memory:"), FakeAk(), FakeCache(), FakeStrategy()
    picks = premarket.scan(D, settings(), store, cache, ak, strat, CAL)
    assert len(picks) == premarket.CANDIDATE_COUNT == 15
    assert strat.asof == PREV
    assert cache.updated == (CODES, PREV) and cache.loaded[1] == PREV
    saved, ctx = store.load_candidates(D)
    assert [p.code for p in saved] == [p.code for p in picks]
    assert ("news_global", 30) in ak.calls
    assert [n["code"] for n in ctx["notices"]] == ["600000"]
    assert len(ctx["stock_news"]["600000"]) == 3
    assert ctx["stats"]["600000"] == {"prev_close": 10.0, "avg_amount20": 2e8}
    json.dumps(ctx)  # 可序列化


def test_scan_tolerates_news_failure():
    ak = FakeAk()

    def boom(*args, **kwargs):
        raise RuntimeError("news source down")

    ak.news_global = ak.notices = ak.stock_news = boom
    store = Store(":memory:")
    assert len(premarket.scan(D, settings(), store, FakeCache(), ak, FakeStrategy(), CAL)) == 15
    ctx = store.load_candidates(D)[1]
    assert ctx["news"] == [] and ctx["notices"] == [] and ctx["stock_news"]["600000"] == []


# ---------- 08:30 深度分析 ----------

def test_analyze_saves_plan_and_drops_non_candidates(store):
    raw = plan(pp("600001", conf=0.9), pp(" 600002 ", "veto", 0.0), pp("999999", conf=1.0),
               pp("600001", conf=0.1), view=" 偏强 ")
    llm = FakeLLM({"analyze:active": raw})
    out = premarket.analyze(D, settings(), store, llm, "active")
    assert [(p.code, p.action, p.confidence) for p in out.picks] == [("600001", "keep", 0.9), ("600002", "veto", 0.0)]
    assert out.market_view == "偏强"
    assert store.kv_get("plan:2026-10-16:active") == out.model_dump()
    assert premarket.load_plan(store, D, "active") == out
    assert premarket.load_plan(store, D, "candidate") is None
    call = llm.calls[0]
    assert call["schema"] is PremarketPlan and call["system"] == "盘前系统提示"
    assert call["effort"] == "high" and call["essential"] is True and call["max_budget_usd"] == 1.5
    assert call["timeout"] == premarket.ANALYZE_TIMEOUT == 900.0
    for s in ("ACTIVE规则", "600005", "关于股东减持计划的公告", "600001 中标大单", "隔夜美股收涨", "涨停 11.00",
              "买≤10.30（+3.0%）"):
        assert s in call["user"], s


@pytest.mark.parametrize("exc", LLM_ERRORS)
def test_analyze_llm_failure_returns_none(store, exc):
    assert premarket.analyze(D, settings(), store, FakeLLM({"analyze:active": exc}), "active") is None
    assert store.kv_get("plan:2026-10-16:active") is None


def test_analyze_plan_without_candidate_codes_returns_none(store):
    assert premarket.analyze(D, settings(), store, FakeLLM({"analyze:active": plan(pp("999999"))}), "active") is None
    assert store.kv_get("plan:2026-10-16:active") is None


def test_analyze_without_candidates_returns_none():
    llm = FakeLLM({})
    assert premarket.analyze(D, settings(), Store(":memory:"), llm, "active") is None
    assert llm.calls == []


def test_run_analyze_runs_both_kinds_concurrently(store):
    barrier = threading.Barrier(2, timeout=5)  # 若串行执行，第一路等不到第二路，Barrier 超时，该路失败

    class ConcurrentLLM(FakeLLM):
        def parse(self, purpose, *args, **kwargs):
            barrier.wait()
            return super().parse(purpose, *args, **kwargs)

    llm = ConcurrentLLM({"analyze:active": plan(pp("600001"), view="A观点"),
                         "analyze:candidate": plan(pp("600002"), view="C观点")})
    assert premarket.run_analyze(D, settings(), store, llm) == {"active": True, "candidate": True}
    assert premarket.load_plan(store, D, "active").market_view == "A观点"
    assert [p.code for p in premarket.load_plan(store, D, "candidate").picks] == ["600002"]
    users = {c["purpose"]: c["user"] for c in llm.calls}
    assert "ACTIVE规则" in users["analyze:active"] and "CANDIDATE规则" in users["analyze:candidate"]


def test_analyze_skips_llm_when_no_candidates():
    s = Store(":memory:")
    s.save_candidates(D, [], CTX)
    llm = FakeLLM({})
    assert premarket.run_analyze(D, settings(), s, llm) == {"active": False, "candidate": False}
    assert llm.calls == []  # 没有候选就不花深度分析的钱


def test_run_analyze_timeout_capped_by_deadline(store):
    llm = FakeLLM({"analyze:active": plan(pp("600001")), "analyze:candidate": plan(pp("600002"))})
    now = dt.datetime.combine(D, dt.time(9, 15))
    premarket.run_analyze(D, settings(), store, llm, deadline=dt.datetime.combine(D, dt.time(9, 22)), now=now)
    assert {c["timeout"] for c in llm.calls} == {420.0}  # 离截止还有 7 分钟，不再给满 900 秒
    llm2 = FakeLLM({})
    late = dt.datetime.combine(D, dt.time(9, 21, 30))
    assert premarket.run_analyze(D, settings(), store, llm2, deadline=dt.datetime.combine(D, dt.time(9, 22)),
                                 now=late) == {"active": False, "candidate": False}
    assert llm2.calls == []  # 剩余时间不够一次分析：不发起


def test_scan_fetches_notices_for_every_day_since_last_session(monkeypatch):
    seen = []
    monkeypatch.setattr(premarket, "_notices", lambda client, days, codes: seen.extend(days) or [])
    monday = dt.date(2026, 10, 19)
    premarket.scan(monday, settings(), Store(":memory:"), FakeCache(), FakeAk(), FakeStrategy(), CAL)
    assert seen == [dt.date(2026, 10, 16), dt.date(2026, 10, 17), dt.date(2026, 10, 18), monday]


def test_confidence_normalized_to_unit_interval():
    assert pp("600001", conf=85).confidence == pytest.approx(0.85)  # 百分数
    assert pp("600001", conf=-1).confidence == 0.0 and pp("600001", conf=1.0).confidence == 1.0
    assert pp("600001", conf=1.2).confidence == 1.0  # 略微越界：截断，不当成 1.2%
    assert pp("600001", conf=2).confidence == pytest.approx(0.02)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "高"])
def test_confidence_rejects_non_numbers(bad):
    with pytest.raises(ValidationError):
        pp("600001", conf=bad)


def test_playbook_version_none_when_malformed(pbdir):
    (pbdir / "active.json").write_text('{"version": "v3", "rules": []}', encoding="utf-8")
    assert premarket._playbook_version("active") is None
    playbook.save("candidate", {"version": 4, "created": "x", "rules": []})
    assert premarket._playbook_version("candidate") == 4


def test_all_vetoed_note_differs_from_auction_failure(store):
    p = plan(pp("600001", "veto", 0.0), pp("600002", "veto", 0.0))
    picks, reviewed, view = premarket._rule_result(settings(), p, CANDS, FakeAk().spot())
    assert picks == [] and reviewed is True and view.endswith(premarket.NOTE_ALL_VETOED)


def test_ex_adjusted_prices_rounded_to_cents(store):
    cands, _ = premarket._ex_adjust(CANDS, None, {"stats": {c: {"prev_close": 10.0} for c in CODES}},
                                    FakeAk(prevs={"600001": 9.80}).spot())
    p = next(x for x in cands if x.code == "600001")
    assert (p.entry_max, p.stop, p.target) == (round(10.3 * 0.98, 2), round(9.3 * 0.98, 2), round(11.0 * 0.98, 2))


def test_small_dividend_triggers_conversion_but_one_cent_does_not():
    ctx = {"stats": {c: {"prev_close": 10.0} for c in CODES}}
    small, _ = premarket._ex_adjust(CANDS, None, ctx, FakeAk(prevs={"600001": 9.70}).spot())  # 派息 0.30
    tiny, _ = premarket._ex_adjust(CANDS, None, ctx, FakeAk(prevs={"600001": 9.99}).spot())   # 舍入差异
    assert next(x for x in small if x.code == "600001").stop == round(9.3 * 0.97, 2)
    assert next(x for x in tiny if x.code == "600001").stop == 9.3


def test_run_final_time_limits(store, monkeypatch):
    save_plan(store, "active", plan(pp("600001")))
    seen = {}
    monkeypatch.setattr(premarket, "_fetch_spot", lambda client, timeout, codes: seen.setdefault("spot", timeout)
                        and FakeAk().spot(codes))
    llm = FakeLLM({"auction:active": check({})})
    premarket.run_final(D, settings(), store, llm, FakeAk(), Recorder(), CAL)
    assert seen["spot"] == premarket.SPOT_TIMEOUT
    assert all(c["timeout"] <= premarket.AUCTION_TIMEOUT for c in llm.calls)


def test_run_final_survives_corrupt_playbook(store, pbdir):
    (pbdir / "candidate.json").write_text("", encoding="utf-8")  # 写到一半时断电
    n = Recorder()
    premarket.run_final(D, settings(), store, FakeLLM({}), FakeAk(), n, CAL)
    assert len(n.sent) == 1 and store.load_recommendation(D, "h:candidate") is not None


def test_run_analyze_reports_each_kind(store):
    llm = FakeLLM({"analyze:active": plan(pp("600001")), "analyze:candidate": LLMError("refused")})
    assert premarket.run_analyze(D, settings(), store, llm) == {"active": True, "candidate": False}
    assert premarket.load_plan(store, D, "candidate") is None


# ---------- 09:25 规则过滤 ----------

def test_auction_filter_gap_window():
    p = plan(pp("600000", conf=0.6),            # +2.00% 在 [-2, 3] 内
             pp("600001", conf=0.9, hi=1.5),    # +2.00% 高于上限
             pp("600002", conf=0.8, lo=-1.0),   # -1.50% 低于下限
             pp("600003", conf=0.7),            # -2.00% 恰在下限（闭区间，按 0.01 个百分点比较）
             pp("600004", conf=0.5, hi=2.0))    # +2.00% 恰在上限
    spot = FakeAk(opens={"600002": 9.85, "600003": 9.8}).spot()
    assert [x.code for x in premarket.auction_filter(p, CANDS, spot)] == ["600003", "600000", "600004"]


def test_auction_filter_entry_max_uses_adopted_price():
    p = plan(pp("600000", conf=0.9, entry=10.15),                         # 竞价 10.20 > 买入上限 10.15
             pp("600001", conf=0.8, entry=10.2),                          # 竞价 10.20 = 买入上限，保留
             pp("600002", conf=0.7, entry=10.1, stop=10.5, target=11.0))  # 价位不合理 -> 量化上限 10.30，保留
    out = premarket.auction_filter(p, CANDS, FakeAk().spot())
    assert [(x.code, x.entry_max) for x in out] == [("600001", 10.2), ("600002", 10.3)]


def test_auction_filter_drops_open_at_or_below_adopted_stop():
    # 撮合在开盘价不高于止损价时不买（Task 1.6）；竞价过滤同样剔除，名额留给后面能成交的股票
    p = plan(pp("600000", conf=0.9, lo=-5.0),   # 竞价 9.55 < 采用的止损 9.60
             pp("600001", conf=0.8, lo=-5.0),   # 竞价 9.60 = 止损
             pp("600002", conf=0.7, lo=-5.0))   # 竞价 9.65 > 止损，保留
    spot = FakeAk(opens={"600000": 9.55, "600001": 9.6, "600002": 9.65}).spot()
    assert [x.code for x in premarket.auction_filter(p, CANDS, spot)] == ["600002"]


def test_auction_filter_missing_spot_row_keeps_pick():
    p = plan(pp("600000", conf=0.9, hi=-5.0),   # 快照里没有这只：不检查条件，保留
             pp("600001", conf=0.8),            # 有快照行但竞价价缺失（停牌）：不满足条件
             pp("600002", conf=0.7))
    spot = FakeAk(opens={"600001": float("nan")}, missing={"600000"}).spot()
    assert [x.code for x in premarket.auction_filter(p, CANDS, spot)] == ["600000", "600002"]
    for empty in (None, pd.DataFrame()):  # 整个快照缺失：所有 keep 都保留
        assert [x.code for x in premarket.auction_filter(p, CANDS, empty)] == ["600000", "600001", "600002"]


def test_auction_filter_keeps_candidates_only_sorted_by_confidence():
    p = plan(pp("600000", conf=0.2), pp("600001", "veto", 0.0), pp("600002", conf=0.9),
             pp("999999", conf=1.0), pp("600003", conf=0.9), pp("600004", conf=0.5))
    out = premarket.auction_filter(p, CANDS, FakeAk().spot())
    assert [x.code for x in out] == ["600002", "600003", "600004", "600000"]  # 同分保持计划顺序
    assert [x.score for x in out] == [0.9, 0.9, 0.5, 0.2]


def test_auction_filter_price_sanity_falls_back_to_quant():
    p = plan(pp("600000", conf=0.9, stop=10.5, target=11.0),                 # stop > entry_max
             pp("600001", conf=0.8, target=30.0),                            # target 超出量化上限 1.5 倍
             pp("600002", conf=0.7, entry=10.254, stop=9.601, target=11.2))  # 合理，四舍五入到分
    out = premarket.auction_filter(p, CANDS, FakeAk().spot())
    assert [(x.entry_max, x.stop, x.target) for x in out] == [(10.3, 9.3, 11.0), (10.3, 9.3, 11.0),
                                                              (10.25, 9.6, 11.2)]
    assert (out[2].name, out[2].score, out[2].hold_days, out[2].reason) == ("股600002", 0.7, 5, "计划理由600002")


# ---------- 09:25 finalize ----------

def test_finalize_without_plan_returns_quant(store):
    llm, ak = FakeLLM({}), FakeAk()
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, ak, "active")
    assert (picks, reviewed, view) == ([qpick(c, i) for i, c in enumerate(CODES[:3])], False, "")
    assert llm.calls == [] and ak.spot_calls() == 0


def test_finalize_confirm_vetoes_but_never_adds(store):
    save_plan(store, "active", plan(pp("600001", conf=0.9), pp("600002", conf=0.8), pp("600003", conf=0.7),
                                    pp("600004", conf=0.6), pp("600000", "veto", 0.0)))
    llm = FakeLLM({"auction:active": check({"600001": "keep", "600002": "veto", "600004": "keep",
                                           "600000": "keep", "999999": "keep"})})
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, FakeAk(), "active")
    assert [p.code for p in picks] == ["600001", "600003"]
    assert reviewed is True and view == "偏强（竞价确认：竞价平稳）"
    call = llm.calls[0]
    assert call["system"] == premarket.AUCTION_SYSTEM and call["schema"] is AuctionCheck
    assert call["effort"] == "low" and call["essential"] is True and call["max_budget_usd"] == 0.3
    assert call["timeout"] == premarket.AUCTION_TIMEOUT == 90.0
    for s in ("600001", "600003", "竞价 10.20（+2.00%）", "500.00万", "占20日均额 2.50%", "涨停 11.00",
              "计划理由600002", "偏强", "有竞价 6 只：高开 6"):  # 竞价概况只统计候选（只取候选的报价）
        assert s in call["user"], s
    assert "600004" not in call["user"] and "600000" not in call["user"]  # 只列出通过过滤且在 max_picks 内的


@pytest.mark.parametrize("exc", LLM_ERRORS)
def test_finalize_confirm_failure_returns_prelim_with_note(store, exc):
    save_plan(store, "active", plan(pp("600001", conf=0.9), pp("600002", conf=0.8)))
    picks, reviewed, view = premarket.finalize(D, settings(), store, FakeLLM({"auction:active": exc}), FakeAk(),
                                               "active")
    assert [p.code for p in picks] == ["600001", "600002"]
    assert reviewed is True and view == "偏强（竞价二次确认未完成，按盘前条件过滤）"


def test_finalize_empty_prelim_skips_confirm(store):
    save_plan(store, "active", plan(pp("600001", hi=1.0), pp("600002", "veto", 0.0), view="谨慎"))
    llm = FakeLLM({})
    assert premarket.finalize(D, settings(), store, llm, FakeAk(), "active") == (
        [], True, "谨慎（竞价条件均未满足，今日不推荐）")
    assert llm.calls == []


def test_finalize_spot_failure_keeps_plan_picks_and_tells_llm(store):
    save_plan(store, "active", plan(pp("600001", hi=-5.0)))
    llm = FakeLLM({"auction:active": check({}, note="")})
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, FakeAk(spot_fail=True), "active")
    assert [p.code for p in picks] == ["600001"] and reviewed is True and view == "偏强"
    assert "竞价数据缺失" in llm.calls[0]["user"]


def test_finalize_without_candidates_raises():
    with pytest.raises(RuntimeError):
        premarket.finalize(D, settings(), Store(":memory:"), FakeLLM({}), FakeAk(), "active")


# ---------- 09:25 run_final ----------

def test_run_final_fetches_spot_once_saves_both_and_pushes_only_active(store):
    save_plan(store, "active", plan(pp("600001", conf=0.9), pp("600002", conf=0.8), view="A观点"))
    save_plan(store, "candidate", plan(pp("600003", conf=0.9), view="C观点"))
    llm = FakeLLM({"auction:active": check({"600002": "veto"}, note="A确认"),
                   "auction:candidate": check({}, note="")})
    ak, notifier = FakeAk(), Recorder()
    premarket.run_final(D, settings(), store, llm, ak, notifier, CAL)
    assert ak.spot_calls() == 1 and ak.spot_codes == [p.code for p in store.load_candidates(D)[0]]  # 只取候选
    act, act_meta = store.load_recommendation(D, "h:active")
    cand, cand_meta = store.load_recommendation(D, "h:candidate")
    assert [p.code for p in act] == ["600001"] and [p.code for p in cand] == ["600003"]
    assert act_meta == {"llm_reviewed": True, "market_view": "A观点（竞价确认：A确认）", "playbook_version": 1,
                        "ref_close": {"600001": 10.0}}
    assert cand_meta["llm_reviewed"] is True and cand_meta["market_view"] == "C观点"
    assert sorted(c["purpose"] for c in llm.calls) == ["auction:active", "auction:candidate"]
    assert all(c["timeout"] <= premarket.AUCTION_TIMEOUT for c in llm.calls)
    assert notifier.sent == [format_recommendation(D, act, True, "A观点（竞价确认：A确认）")]


def test_run_final_converts_prices_on_ex_date(store):
    # 600001 今天 10 转 10：快照昨收 5.00（除权参考价），竞价 5.10；盘前计划按除权前的昨收 10.00 写成
    save_plan(store, "active", plan(pp("600001", conf=0.9, lo=-5.0)))
    ak = FakeAk(opens={"600001": 5.1}, prevs={"600001": 5.0})
    premarket.run_final(D, settings(), store, FakeLLM({"auction:active": check({})}), ak, Recorder(), CAL)
    act, meta = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == ["600001"] and meta["ref_close"] == {"600001": 5.0}  # 不换算的话竞价价低于止损 9.60
    assert (act[0].entry_max, act[0].stop, act[0].target) == pytest.approx((10.25 / 2, 9.6 / 2, 11.2 / 2), abs=0.006)  # 采用计划价位时舍入到分
    cand, _ = store.load_recommendation(D, "h:candidate")  # 没有计划的一路：量化候选同样换算
    assert cand[1].stop == pytest.approx(9.3 / 2) and cand[0].stop == pytest.approx(9.3)


def test_run_final_fallback_also_converts_on_ex_date(store, monkeypatch):
    save_plan(store, "active", plan(pp("600001", conf=0.9, lo=-5.0)))
    monkeypatch.setattr(premarket, "finalize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("定稿失败")))
    ak = FakeAk(opens={"600001": 5.1}, prevs={"600001": 5.0})
    premarket.run_final(D, settings(), store, FakeLLM({}), ak, Recorder(), CAL)
    act, _ = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == ["600001"] and act[0].stop == pytest.approx(4.8, abs=0.006)


def test_run_final_kind_without_plan_uses_quant(store):
    save_plan(store, "active", plan(pp("600001"), view="A观点"))
    llm = FakeLLM({"auction:active": check({})})
    premarket.run_final(D, settings(), store, llm, FakeAk(), Recorder(), CAL)
    cand, meta = store.load_recommendation(D, "h:candidate")
    assert cand == [qpick(c, i) for i, c in enumerate(CODES[:3])]
    assert meta["llm_reviewed"] is False and meta["market_view"] == ""
    assert [c["purpose"] for c in llm.calls] == ["auction:active"]


def test_fetch_spot_keeps_partial_rows_on_timeout():
    import threading
    gate = threading.Event()

    class Slow:
        def spot(self, codes, sink=None):  # 第一只取到后卡住
            sink.append({"code": codes[0], "name": "", "open": 10.2, "prev_close": 10.0, "last": 10.2, "pct": 2.0,
                         "volume": 1e4, "amount": 5e6, "high": 10.2})
            gate.wait(5)

    out = premarket._fetch_spot(Slow(), 0.3, ["600001", "600002"])
    gate.set()
    assert out["code"].tolist() == ["600001"]


def test_run_final_without_plans_pushes_quant_without_spot(store):
    ak, notifier = FakeAk(), Recorder()
    premarket.run_final(D, settings(), store, FakeLLM({}), ak, notifier, CAL)
    assert ak.spot_calls() == 0
    act, meta = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == CODES[:3] and meta["llm_reviewed"] is False
    assert meta["ref_close"] == {c: 10.0 for c in CODES[:3]}  # 没有快照：记扫描时的昨收，结算再比较
    assert len(notifier.sent) == 1 and "未经 LLM 审核" in notifier.sent[0]


def test_run_final_slow_confirm_falls_back_to_prelim(store, monkeypatch):
    monkeypatch.setattr(premarket, "FINAL_DEADLINE", 0.5)
    gate = threading.Event()

    class SlowLLM(FakeLLM):
        def parse(self, purpose, *args, **kwargs):
            if purpose == "auction:candidate":
                gate.wait(5)
            return super().parse(purpose, *args, **kwargs)

    save_plan(store, "active", plan(pp("600001"), view="A观点"))
    save_plan(store, "candidate", plan(pp("600002", conf=0.9), pp("600003", conf=0.8), pp("600004", hi=1.0),
                                       view="C观点"))
    llm = SlowLLM({"auction:active": check({}, note=""), "auction:candidate": check({"600002": "veto"})})
    notifier = Recorder()
    t0 = time.monotonic()
    try:
        premarket.run_final(D, settings(), store, llm, FakeAk(), notifier, CAL)
    finally:
        gate.set()
    assert time.monotonic() - t0 < 3
    cand, meta = store.load_recommendation(D, "h:candidate")
    assert [p.code for p in cand] == ["600002", "600003"]  # 规则过滤结果；迟到的否决不生效
    assert meta["llm_reviewed"] is True and meta["market_view"] == "C观点（竞价二次确认未完成，按盘前条件过滤）"
    act, act_meta = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == ["600001"] and act_meta["market_view"] == "A观点"
    assert notifier.sent == [format_recommendation(D, act, True, "A观点")]


def test_run_final_without_candidates_raises():
    notifier = Recorder()
    with pytest.raises(RuntimeError):
        premarket.run_final(D, settings(), Store(":memory:"), FakeLLM({}), FakeAk(), notifier, CAL)
    assert notifier.sent == []
