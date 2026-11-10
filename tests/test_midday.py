from datetime import date, datetime, time

import pandas as pd
import pytest

from agents.midday import positions, run_midday, watch
from agents.premarket import PlannedPick, PremarketPlan
from engine.ledger import Account, Position
from engine.store import Store
from engine.types import Pick
from jobs import common, midday as midday_job
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, FakeLLM, cal, install_context,
                            make_dates, make_index, make_panel, make_settings)

D = date(2026, 11, 10)


class Client:
    """只实现 spot()：industry_boards 缺失，行业板块自动降级为空（market.intraday 内部吞异常）。"""

    def __init__(self, df: pd.DataFrame):
        self.df, self.calls = df, []

    def spot(self, codes=None, sink=None):
        self.calls.append(codes)
        if codes is None:
            return self.df.copy()
        return self.df[self.df["code"].isin(list(codes))].reset_index(drop=True)


class BrokenFullSnapshot(Client):
    def spot(self, codes=None, sink=None):
        if codes is None:
            self.calls.append(None)
            raise RuntimeError("全市场快照失败")
        return super().spot(codes)


def make_store(d: date = D) -> Store:
    store = Store(":memory:")
    acc = Account(name="h:active", cash=100_000.0)
    acc.positions["600001"] = Position(code="600001", name="测试01", qty=1000, available=1000,
                                       cost=10.0, entry_date=date(2026, 11, 6), stop=9.30,
                                       target=11.00, exit_date=date(2026, 11, 14))
    store.save_account(acc)
    store.save_candidates(d, [
        Pick("600002", "测试02", 0.9, 10.5, 9.5, 12.0, 3, reason="量化首选"),
        Pick("600003", "测试03", 0.8, 9.0, 8.4, 10.5, 4),
        Pick("600004", "测试04", 0.7, 20.0, 18.0, 24.0, 5),  # 现价未回到买入区间
    ], {})
    store.save_recommendation(d, "h:active", [Pick("600003", "测试03", 0.8, 9.0, 8.4, 10.5, 4)], {})
    return store


def spot_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "600001", "name": "测试01", "last": 10.20, "prev_close": 10.0, "pct": 2.0, "amount": 1e8},
        {"code": "600002", "name": "测试02", "last": 10.00, "prev_close": 9.8, "pct": 2.04, "amount": 1e8},
        {"code": "600003", "name": "测试03", "last": 8.50, "prev_close": 8.4, "pct": 1.19, "amount": 1e8},
        {"code": "600004", "name": "测试04", "last": 21.00, "prev_close": 20.5, "pct": 2.44, "amount": 1e8},
        {"code": "600009", "name": "其他", "last": 5.0, "prev_close": 5.1, "pct": -1.96, "amount": 1e8},
    ])


def test_positions_carry_pnl_and_distance_to_stop():
    rows = {r["code"]: r for r in spot_df().to_dict("records")}
    acc = Account(name="h:active", cash=0.0)
    acc.positions["600001"] = Position("600001", "测试01", 1000, 1000, 10.0, date(2026, 11, 6),
                                       9.30, 11.00, date(2026, 11, 14))
    (p,) = positions(acc, rows, D)
    assert p["price"] == 10.20 and round(p["pnl"], 6) == 200 and round(p["pnl_ret"], 6) == 0.02
    assert round(p["to_stop"], 6) == round(9.30 / 10.20 - 1, 6)
    assert round(p["to_target"], 6) == round(11.00 / 10.20 - 1, 6)
    assert p["days_left"] == 4


def test_watch_excludes_recommended_held_and_out_of_zone():
    rows = {r["code"]: r for r in spot_df().to_dict("records")}
    store = make_store()
    items = watch(D, store, rows, {"600001"}, {"600003"})
    assert [w["code"] for w in items] == ["600002"]
    assert round(items[0]["day_ret"], 6) == round(10.0 / 9.8 - 1, 6)


def test_watch_respects_morning_veto():
    """早盘 LLM 已 veto 的候选，午盘不能翻出来当观察项。"""
    store = make_store()
    store.kv_set(f"plan:{D.isoformat()}:active", PremarketPlan(market_view="震荡", picks=[PlannedPick(
        code="600002", action="veto", confidence=0.4, entry_max=10.5, stop=9.5, target=12.0,
        min_gap_pct=-5.0, max_gap_pct=5.0, reason="高开回落风险")]).model_dump())
    rows = {r["code"]: r for r in spot_df().to_dict("records")}
    assert watch(D, store, rows, {"600001"}, {"600003"}) == []


def test_midday_report_lists_positions_and_watch_only():
    store, notifier = make_store(), NullNotifier()
    run_midday(D, store, Client(spot_df()), FakeLLM(), notifier)
    assert len(notifier.sent) == 1
    text = notifier.sent[0]
    assert "午盘简报" in text and "持仓 h:active" in text
    assert "600001" in text and "浮动 +200 元（+2.00%）" in text
    assert "600002" in text                      # 回踩到买入区间的早盘候选
    assert "600003" not in text                  # 早盘已推荐，不重复出现
    assert "600004" not in text                  # 现价仍在买入区间之上
    assert "不会自动成交" in text
    assert "午盘：持仓接近止盈" in text            # LLM 点评


def test_midday_still_pushes_when_llm_fails():
    store, notifier = make_store(), NullNotifier()
    run_midday(D, store, Client(spot_df()), FakeLLM(fail={"midday": RuntimeError("超时")}), notifier)
    assert "（午盘点评不可用）" in notifier.sent[0]
    assert "600002" in notifier.sent[0]           # 规则部分不受影响


def test_midday_falls_back_to_code_quotes_when_full_snapshot_fails():
    store, notifier = make_store(), NullNotifier()
    client = BrokenFullSnapshot(spot_df())
    run_midday(D, store, client, FakeLLM(), notifier)
    assert client.calls[0] is None                # 先试全市场
    assert client.calls[1]                        # 失败后按代码取
    assert "现价 10.20" in notifier.sent[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    dates = make_dates(20)
    panel, index = make_panel(CODES, dates), make_index(dates)
    d = dates[10]
    client = FakeClient(dates, panel, NAMES, index)
    client.spot_df = spot_df()
    store, notifier = make_store(d), NullNotifier()
    cache = FakeCache(panel, NAMES, index)
    install_context(monkeypatch, make_settings(tmp_path), store, client, cache, cal(dates), notifier)
    monkeypatch.setattr(common, "make_llm", lambda settings, store: FakeLLM())
    return dict(d=d, notifier=notifier, store=store)


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm))


def test_midday_job_runs_once(env):
    d = env["d"]
    assert midday_job.main(now=at(d, 11, 35)) is True
    assert len(env["notifier"].sent) == 1
    assert midday_job.main(now=at(d, 11, 40)) is True  # 已完成，幂等跳过
    assert len(env["notifier"].sent) == 1


def test_midday_job_skips_stale_and_non_trading_days(env):
    d = env["d"]
    assert midday_job.main(now=at(d, 13, 30)) is False   # 下午开盘后不补发
    assert env["notifier"].sent == []
    assert midday_job.main(now=at(date(2026, 11, 14), 11, 35)) is False  # 周六
    assert env["notifier"].sent == []
