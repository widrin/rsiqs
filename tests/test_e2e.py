"""端到端：bootstrap -> 扫描+深度分析 -> 竞价定稿推送 -> 结算 -> 复盘 -> 次日。
全程无网络、不启动 claude.exe（FakeLLM），时间全部通过 main(now=...) 注入。"""
from datetime import datetime, time

import pytest

import agents.playbook as pb
import engine.integrity as integrity
from agents.llm import LLMError
from jobs import bootstrap, common, premarket_final, premarket_scan, review, settle
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, FakeLLM, cal,
                            install_context, make_dates, make_index, make_panel,
                            make_settings, new_store, prepare_root)

ACCOUNTS = ["q:baseline_momo", "h:active", "h:candidate", "bench:csi300"]


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm))


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(integrity, "write_manifest", lambda r: None)
    dates = make_dates()
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    cache, store, notifier = FakeCache(panel, NAMES, index), new_store(), NullNotifier()
    settings = make_settings(root)
    install_context(monkeypatch, settings, store, client, cache, cal(dates), notifier)
    llm = FakeLLM(store)
    monkeypatch.setattr(common, "make_llm", lambda s, st: llm)
    sent_at_settle = {}
    real_settle_day = settle.settle_day

    def spy_settle_day(d, *a, **k):
        sent_at_settle[d] = len(notifier.sent)
        return real_settle_day(d, *a, **k)

    monkeypatch.setattr(settle, "settle_day", spy_settle_day)
    client.current = dates[100]
    bootstrap.main(now=at(dates[100], 20))
    notifier.sent.clear()
    return dict(dates=dates, client=client, store=store, notifier=notifier, llm=llm,
                settings=settings, sent_at_settle=sent_at_settle)


def start_day(world, day):
    """“今天”切到 day：行情快照与 FakeLLM 覆盖的候选都按这一天。"""
    world["client"].current = world["llm"].day = day


def test_two_trading_days_full_cycle(world):
    dates, store, notifier, llm = world["dates"], world["store"], world["notifier"], world["llm"]
    for day in dates[101:103]:
        start_day(world, day)
        assert premarket_scan.main(now=at(day, 8, 30)) is True
        cands, _ = store.load_candidates(day)
        plan = store.kv_get(f"plan:{day.isoformat()}:active")
        assert cands and plan and {p["code"] for p in plan["picks"]} == {p.code for p in cands}
        assert store.kv_get(f"plan:{day.isoformat()}:candidate")
        assert not any("盘前分析未完成" in m for m in notifier.sent)

        n0, slept = len(notifier.sent), []
        assert premarket_final.main(now=at(day, 9, 25), sleep=slept.append) is True
        assert slept == [15.0]                                 # 等到 09:25:15 再定稿
        recs, meta = store.load_recommendation(day, "h:active")
        assert recs and len(recs) <= world["settings"].max_picks and meta["llm_reviewed"] is True
        pushed = notifier.sent[n0:]
        assert len(pushed) == 1 and all(p.code in pushed[0] for p in recs)  # 只推 h:active
        assert store.load_recommendation(day, "h:candidate") is not None

        assert settle.main(now=at(day, 15, 35)) == [day]
        assert world["sent_at_settle"][day] > n0               # 推送发生在结算之前
        n1 = len(notifier.sent)
        assert review.main(now=at(day, 16, 5)) is True
        assert store.job_done("review", day)
        assert len(notifier.sent) > n1                         # 日报已推送

    for acct in ACCOUNTS:
        assert [ts.date() for ts in store.nav_series(acct).index] == dates[100:103], acct
    day1_recs = {p.code for p in store.load_recommendation(dates[101], "h:active")[0]}
    buys = {f.code for f in store.load_account("h:active").trades
            if f.side == "BUY" and f.date == dates[101]}
    assert buys and buys <= day1_recs
    assert store.load_account("q:baseline_momo").trades
    assert store.nav_series("h:active").iloc[-1] != world["settings"].capital

    cand, active = pb.load("candidate"), pb.load("active")
    assert any("高开" in r["text"] for r in cand["rules"])    # 候选 playbook 已更新
    assert active["rules"] == [] and active["version"] == 1   # 未满 10 个交易日，不发布
    assert {"analyze:active", "analyze:candidate", "auction:active", "auction:candidate",
            "review"} <= set(llm.purposes)
    assert all(c["essential"] for c in llm.calls if c["purpose"] != "review")  # 盘前不受月预算限制


def test_relay_outage_degrades_to_quant_and_still_reports(world):
    """中转失效（Claude Code 升级或账号被封）：分析与复盘都抛 LLMError，推荐与日报照常推送。"""
    dates, store, notifier, llm = world["dates"], world["store"], world["notifier"], world["llm"]
    day = dates[101]
    start_day(world, day)
    llm.fail = {"analyze": LLMError("中转不可用"), "review": LLMError("中转不可用")}
    assert premarket_scan.main(now=at(day, 8, 30)) is True     # 扫描成功即记 ok
    assert store.kv_get(f"plan:{day.isoformat()}:active") is None
    assert any("盘前分析未完成" in m for m in notifier.sent)

    n0 = len(notifier.sent)
    assert premarket_final.main(now=at(day, 9, 26)) is True
    recs, meta = store.load_recommendation(day, "h:active")
    cands, _ = store.load_candidates(day)
    assert meta["llm_reviewed"] is False
    assert [p.code for p in recs] == [p.code for p in cands[: world["settings"].max_picks]]
    assert any("未经 LLM 审核" in m for m in notifier.sent[n0:])    # 量化推荐照常推送
    assert not any(p.startswith("auction") for p in llm.purposes)  # 无计划不做竞价确认

    assert settle.main(now=at(day, 15, 35)) == [day]
    assert review.main(now=at(day, 16, 5)) is True
    assert "复盘失败" in notifier.sent[-1] and "h:active" in notifier.sent[-1]
    assert pb.load("candidate")["rules"] == []


def test_logon_catch_up_after_missed_days(world):
    """电脑在 dates[101]、dates[102] 全天关机，dates[103] 10:00 开机。"""
    dates, store, llm = world["dates"], world["store"], world["llm"]
    day = dates[103]
    start_day(world, day)
    assert premarket_scan.main(now=at(day, 10, 0)) is True      # 错过的 08:30 任务：只扫描
    assert premarket_final.main(now=at(day, 10, 0)) is False    # 过时推荐跳过
    assert store.load_recommendation(day, "h:active") is None
    assert settle.main(now=at(day, 10, 3)) == dates[101:103]   # Catchup 动作 1
    assert review.main(now=at(day, 10, 5)) is True              # Catchup 动作 2
    assert store.job_done("review", dates[102])
    for acct in ACCOUNTS:
        assert store.last_nav_date(acct) == dates[102], acct
    assert store.load_account("h:active").trades == []          # 无推荐的日子不交易
    assert llm.calls == []                                      # 开机补跑不花 LLM 费用
