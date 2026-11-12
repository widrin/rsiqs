import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from agents import news
from agents.llm import LLMError
from agents.news import NewsPick, NewsPlan
from config import Settings
from engine.calendar import TradingCalendar
from engine.dataview import DataView
from engine.store import Store
from jobs import common, news_scan

D = dt.date(2026, 10, 16)
PREV = dt.date(2026, 10, 15)
CODES = [f"600{i:03d}" for i in range(10)]
CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)


def settings(**kw) -> Settings:
    base = dict(ark_api_key="k", wecom_webhook_url="", root=Path("."), max_picks=3,
                llm_base_url="https://llm.invalid/v1")
    base.update(kw)
    return Settings(**base)


def panel(codes=CODES, end=PREV, n=25, **overrides):
    """构造面板：默认 10.00 收盘、20 日均额 2 亿；overrides 按代码覆盖整列（例如 {"600001": {"close": 11.0}}）。"""
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    cols = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 1e6, "amount": 2e8, "prev_close": 10.0}
    out = {}
    for c in codes:
        df = pd.DataFrame({k: [v] * n for k, v in cols.items()}, index=idx)
        for k, v in overrides.get(c, {}).items():
            df[k] = v
        out[c] = df
    return out


def view(data=None, names=None, asof=PREV) -> DataView:
    return DataView(data if data is not None else panel(), names or {c: f"股{c}" for c in CODES}, asof)


def plan(*picks, market="科技主线") -> NewsPlan:
    return NewsPlan(market_view=market, picks=list(picks))


def npick(code, conf=0.5, reason="利好"):
    return NewsPick(code=code, reason=reason, confidence=conf)


class FakeAk:
    def __init__(self, items=None, universe=None):
        self.items = items if items is not None else [
            {"title": "某部委发布算力规划", "content": "支持国产算力", "time": "2026-10-16 07:00:00"}]
        self.codes = list(universe or CODES)

    def news_global(self, limit=50):
        return self.items

    def universe(self):
        return list(self.codes)


class FakeCache:
    def __init__(self, data=None):
        self.data = data if data is not None else panel()
        self.updated = self.loaded = None

    def update(self, codes, end, start_default=dt.date(2015, 6, 1)):
        self.updated = (list(codes), end)

    def load_panel(self, codes, start, end):
        self.loaded = (start, end)
        return {c: self.data[c] for c in codes if c in self.data}, {c: f"股{c}" for c in CODES}


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def parse(self, purpose, system, user, schema, timeout=120.0):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, timeout=timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# ---------- 采集与 LLM ----------

def test_collect_tolerates_failure():
    class Down:
        def news_global(self, limit=50):
            raise RuntimeError("cls down")

    assert news.collect(Down()) == []


def test_make_plan_sends_news_and_universe():
    llm = FakeLLM(plan(npick("600001")))
    out = news.make_plan(llm, D, FakeAk().items, CODES)
    assert [p.code for p in out.picks] == ["600001"]
    call = llm.calls[0]
    assert call["purpose"] == "news" and call["schema"] is NewsPlan and call["system"] == news.NEWS_SYSTEM
    assert call["timeout"] == news.TIMEOUT
    for s in ("某部委发布算力规划", "支持国产算力", " ".join(CODES)):
        assert s in call["user"], s


@pytest.mark.parametrize("exc", [LLMError("http 500"), ValueError("bad output")])
def test_make_plan_failure_returns_none(exc):
    assert news.make_plan(FakeLLM(exc), D, FakeAk().items, CODES) is None


def test_confidence_percent_and_clamp():
    assert npick("600001", conf=85).confidence == pytest.approx(0.85)
    assert npick("600001", conf=-1).confidence == 0.0
    assert npick("600001", conf="高").confidence == 0.5


# ---------- 硬过滤与价位 ----------

def test_build_picks_uses_close_template():
    picks = news.build_picks(plan(npick("600001", conf=0.9, reason="算力受益")), view())
    p = picks[0]
    assert (p.code, p.name, p.score, p.hold_days) == ("600001", "股600001", 0.9, news.HOLD_DAYS)
    assert (p.entry_max, p.stop, p.target) == (10.3, 9.3, 11.0)
    assert p.reason == "消息面：算力受益" and p.weight is None


def test_build_picks_drops_codes_outside_universe():
    assert news.build_picks(plan(npick("999999")), view()) == []
    picks = news.build_picks(plan(npick("999999", conf=0.9), npick("600002", conf=0.2)), view())
    assert [p.code for p in picks] == ["600002"]


def test_build_picks_drops_st():
    names = {c: f"股{c}" for c in CODES} | {"600001": "*ST退"}
    assert news.build_picks(plan(npick("600001")), view(names=names)) == []


def test_build_picks_drops_limit_up():
    # 昨收 10.00、收盘 11.00 即涨停：无法在建仓日按开盘价买入
    data = panel(**{"600001": {"close": 11.0, "prev_close": 10.0}})
    assert news.build_picks(plan(npick("600001")), view(data=data)) == []


def test_build_picks_drops_illiquid_and_stale():
    thin = panel(**{"600001": {"amount": 1e7}})
    short = panel(n=10)
    assert news.build_picks(plan(npick("600001")), view(data=thin)) == []
    assert news.build_picks(plan(npick("600001")), view(data=short)) == []
    paused = view(data=panel(end=PREV - dt.timedelta(days=1)))  # asof 当天没有 K 线（停牌）
    assert news.build_picks(plan(npick("600001")), paused) == []


def test_build_picks_sorts_by_confidence_dedups_and_caps():
    picks = [npick(c) for c in CODES]
    picks[0] = npick(CODES[0], conf=0.1)
    picks[5] = npick(CODES[5], conf=0.99)
    picks.append(npick(CODES[5], conf=0.99))
    out = news.build_picks(plan(*picks), view())
    assert len(out) == news.PICK_COUNT == 5
    assert out[0].code == CODES[5] and CODES[0] not in {p.code for p in out}
    assert len({p.code for p in out}) == 5


def test_build_picks_drops_empty_name():
    names = {c: f"股{c}" for c in CODES} | {"600001": ""}
    assert news.build_picks(plan(npick("600001")), view(names=names)) == []


# ---------- select ----------

def test_select_returns_picks_and_meta_with_ref_close():
    llm = FakeLLM(plan(npick("600001", conf=0.8), npick("999999", conf=0.9)))
    picks, meta = news.select(llm, FakeAk(), D, view(), CODES)
    assert [p.code for p in picks] == ["600001"]
    assert meta["source"] == "news" and meta["news_count"] == 1
    assert meta["market_view"] == "科技主线"
    assert meta["raw_codes"] == ["600001", "999999"]
    assert meta["ref_close"] == {"600001": 10.0}  # 结算按它判断是否需要除权换算


def test_select_empty_when_llm_fails():
    picks, meta = news.select(FakeLLM(LLMError("timeout")), FakeAk(), D, view(), CODES)
    assert picks == [] and meta["market_view"] == "" and meta["ref_close"] == {}


def test_select_skips_llm_when_no_news():
    llm = FakeLLM(plan(npick("600001")))
    picks, meta = news.select(llm, FakeAk(items=[]), D, view(), CODES)
    assert picks == [] and llm.calls == [] and meta["news_count"] == 0


# ---------- jobs/news_scan ----------

def test_news_scan_saves_recommendation_to_shadow_account(monkeypatch):
    llm = FakeLLM(plan(npick("600001", conf=0.8)))
    monkeypatch.setattr(common, "make_llm", lambda settings, store: llm)
    store, ak, cache = Store(":memory:"), FakeAk(), FakeCache()
    picks = news_scan.scan(D, settings(), store, ak, cache, CAL)
    assert [p.code for p in picks] == ["600001"]
    assert cache.updated == (CODES, PREV) and cache.loaded[1] == PREV
    saved, meta = store.load_recommendation(D, news_scan.ACCOUNT)
    assert [p.code for p in saved] == ["600001"] and meta["ref_close"] == {"600001": 10.0}
    assert news_scan.ACCOUNT == "h:news" and news_scan.ACCOUNT in common.LLM_ACCOUNTS


def test_news_scan_saves_empty_recommendation_when_nothing_picked(monkeypatch):
    monkeypatch.setattr(common, "make_llm", lambda settings, store: FakeLLM(LLMError("down")))
    store = Store(":memory:")
    assert news_scan.scan(D, settings(), store, FakeAk(), FakeCache(), CAL) == []
    assert store.load_recommendation(D, news_scan.ACCOUNT) == ([], {
        "source": "news", "news_count": 1, "market_view": "", "raw_codes": [], "ref_close": {}})
