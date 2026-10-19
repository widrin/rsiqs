from datetime import datetime, time

import agents.playbook as pb
import engine.integrity as integrity
from jobs import bootstrap
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, cal, install_context,
                            make_dates, make_index, make_panel, make_settings, new_store,
                            next_saturday, prepare_root)
from notify.wecom import NullNotifier


def setup(tmp_path, monkeypatch):
    root = prepare_root(tmp_path, monkeypatch)
    manifests = []
    monkeypatch.setattr(integrity, "write_manifest", lambda r: manifests.append(r))
    dates = make_dates()
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    client.current = dates[100]
    cache, store, notifier = FakeCache(panel, {}, index), new_store(), NullNotifier()
    settings = make_settings(root)
    install_context(monkeypatch, settings, store, client, cache, cal(dates), notifier)
    return dates, settings, store, cache, notifier, manifests


def test_bootstrap_creates_everything(tmp_path, monkeypatch):
    dates, settings, store, cache, notifier, manifests = setup(tmp_path, monkeypatch)
    summary = bootstrap.main(now=datetime.combine(dates[100], time(20, 0)))

    expected = {"q:baseline_momo", "h:active", "h:candidate", "bench:csi300"}
    assert expected <= set(store.list_accounts())
    for name in expected:
        assert store.load_account(name).cash == settings.capital
        assert store.last_nav_date(name) == dates[100]
    assert store.get_status("baseline_momo") == "incumbent"
    assert store.kv_get("universe") == CODES
    assert store.kv_get("bench_start") == dates[100].isoformat()
    assert store.kv_get("playbook_released") == dates[100].isoformat()
    assert cache.names["600001"] == NAMES["600001"]
    assert cache.updates == [dates[100]]
    assert "sh000300" in cache.saved_index
    for kind in ("active", "candidate"):
        assert pb.load(kind)["version"] == 1 and pb.load(kind)["rules"] == []
    assert manifests == [settings.root]
    assert (settings.root / "logs").is_dir() and (settings.root / "data").is_dir()
    assert sorted(summary["created"]) == sorted(expected)
    assert len(notifier.sent) == 1


def test_bootstrap_names_fall_back_to_spot(tmp_path, monkeypatch):
    dates, settings, store, cache, _, _ = setup(tmp_path, monkeypatch)
    from jobs import common
    client = common.context()[2]

    def down():
        raise ConnectionError("exchanges down")

    monkeypatch.setattr(client, "names", down)
    bootstrap.main(now=datetime.combine(dates[100], time(20, 0)))
    assert cache.names["600001"] == NAMES["600001"]


def test_bootstrap_rerun_does_not_overwrite(tmp_path, monkeypatch):
    dates, settings, store, _, _, _ = setup(tmp_path, monkeypatch)
    now = datetime.combine(dates[100], time(20, 0))
    bootstrap.main(now=now)
    acc = store.load_account("h:active")
    acc.cash = 123.0
    store.save_account(acc)
    pb.save("candidate", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "t", "evidence": "e", "created": "x"}]})

    summary = bootstrap.main(now=now)
    assert summary["created"] == []
    assert store.load_account("h:active").cash == 123.0
    assert len(pb.load("candidate")["rules"]) == 1


def test_bootstrap_rerun_keeps_existing_manifest_and_warns(tmp_path, monkeypatch):
    dates, settings, _, _, notifier, manifests = setup(tmp_path, monkeypatch)
    now = datetime.combine(dates[100], time(20, 0))
    bootstrap.main(now=now)
    (settings.root / integrity.MANIFEST_PATH).write_text("{}", encoding="utf-8")  # 清单已经存在
    monkeypatch.setattr(integrity, "verify", lambda r: ["engine/broker.py"])
    bootstrap.main(now=now)
    assert manifests == [settings.root]  # 没有重写
    assert "engine/broker.py" in notifier.sent[-1]


def test_bootstrap_on_weekend_starts_from_last_trading_day(tmp_path, monkeypatch):
    dates, _, store, _, _, _ = setup(tmp_path, monkeypatch)
    sat = next_saturday(dates[99])
    bootstrap.main(now=datetime.combine(sat, time(10, 0)))
    assert store.last_nav_date("h:active") == max(x for x in dates if x < sat)


def test_bootstrap_before_close_anchors_previous_day(tmp_path, monkeypatch):
    dates, _, store, cache, _, _ = setup(tmp_path, monkeypatch)
    bootstrap.main(now=datetime.combine(dates[100], time(9, 0)))
    assert store.last_nav_date("h:active") == dates[99]
    assert cache.updates == [dates[99]]  # 不取当天未收盘的 K 线


def test_bootstrap_reports_failed_downloads(tmp_path, monkeypatch):
    dates, _, _, cache, notifier, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cache, "update", lambda codes, end, start_default=None: ["600003"])
    out = bootstrap.main(now=datetime.combine(dates[100], time(20, 0)))
    assert out["failed"] == ["600003"]
    assert "1 只股票日线下载失败（600003）" in notifier.sent[-1]
