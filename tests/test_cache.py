# tests/test_cache.py
from datetime import date

import pandas as pd
import pytest

from data.cache import BarCache

COLS = ["date", "open", "high", "low", "close", "volume", "amount"]


def frame(rows):
    df = pd.DataFrame(rows, columns=COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df


class FakeClient:
    def __init__(self, data):
        self.data = data  # code -> DataFrame
        self.calls = []
        self.raw_calls = []

    def raw_bars(self, code, start, end):
        self.raw_calls.append((code, start, end))
        df = self.daily_bars(code, start, end)
        self.calls.pop()
        df["prev_close"] = df["close"].shift(1).fillna(df["open"])
        return df

    def daily_bars(self, code, start, end):
        self.calls.append((code, start, end))
        if code == "BAD":
            raise ConnectionError("down")
        df = self.data.get(code, frame([]))
        m = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
        return df[m].reset_index(drop=True)


ALL = frame([
    ("2026-01-05", 10.0, 10.5, 9.8, 10.2, 1000, 1.0e6),
    ("2026-01-06", 10.2, 10.8, 10.1, 10.6, 1100, 1.1e6),
    ("2026-01-07", 10.6, 10.9, 10.3, 10.4, 1200, 1.2e6),
    ("2026-01-08", 10.4, 10.7, 10.2, 10.5, 1300, 1.3e6),
])


@pytest.fixture
def cache(tmp_path):
    client = FakeClient({"600000": ALL})
    return BarCache(tmp_path / "c.sqlite", client), client


def test_first_update_fetches_from_start_default(cache):
    c, client = cache
    assert c.update(["600000"], date(2026, 1, 7)) == []
    assert client.calls == [("600000", date(2015, 6, 1), date(2026, 1, 7))]
    assert c.last_date("600000") == date(2026, 1, 7)
    assert c.codes() == ["600000"]


def test_incremental_update_only_fetches_after_last_date(cache):
    c, client = cache
    c.update(["600000"], date(2026, 1, 7))
    c.update(["600000"], date(2026, 1, 8))
    # 从已存的最后 OVERLAP_ROWS 行开始取（这里只有 3 行），用重叠部分检查复权基准有没有变
    assert client.calls[-1] == ("600000", date(2026, 1, 5), date(2026, 1, 8))
    assert c.last_rebased == []
    n = len(client.calls)
    c.update(["600000"], date(2026, 1, 8))  # 已是最新，不再请求
    assert len(client.calls) == n
    assert c.last_date("600000") == date(2026, 1, 8)


def test_full_update_refetches_everything(cache):
    c, client = cache
    c.update(["600000"], date(2026, 1, 8))
    c.update(["600000"], date(2026, 1, 8), full=True)
    assert client.calls[-1] == ("600000", date(2015, 6, 1), date(2026, 1, 8))
    panel, _ = c.load_panel(["600000"], date(2026, 1, 1), date(2026, 1, 8))
    assert len(panel["600000"]) == 4


def test_failure_of_one_code_does_not_block_others(cache):
    c, _ = cache
    assert c.update(["BAD", "600000"], date(2026, 1, 8)) == ["BAD"]
    assert c.last_date("600000") == date(2026, 1, 8)
    assert c.last_date("BAD") is None


def test_load_panel_prev_close_and_names(cache):
    c, _ = cache
    c.update(["600000"], date(2026, 1, 8))
    c.set_names({"600000": "浦发银行"})
    panel, names = c.load_panel(["600000", "000001"], date(2026, 1, 6), date(2026, 1, 8))
    assert list(panel) == ["600000"]  # 无数据的代码不出现
    df = panel["600000"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tolist() == [pd.Timestamp(f"2026-01-0{i}") for i in (6, 7, 8)]
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "amount", "prev_close"]
    # prev_close 在全历史上 shift，窗口第一天也有真实昨收
    assert df["prev_close"].tolist() == [10.2, 10.6, 10.4]
    assert names == {"600000": "浦发银行"}


def test_load_panel_first_bar_prev_close_falls_back_to_open(cache):
    c, _ = cache
    c.update(["600000"], date(2026, 1, 8))
    panel, names = c.load_panel(["600000"], date(2026, 1, 1), date(2026, 1, 6))
    assert panel["600000"]["prev_close"].tolist() == [10.0, 10.2]
    assert names == {"600000": ""}


def test_rebased_history_triggers_full_refetch(cache):
    c, client = cache
    c.update(["600000"], date(2026, 1, 7))
    rebased = ALL.copy()  # 1 月 8 日除息 0.3：前复权把此前的价格整体下调
    rebased.loc[:2, ["open", "high", "low", "close"]] -= 0.3
    client.data["600000"] = rebased
    c.update(["600000"], date(2026, 1, 8))
    assert c.last_rebased == ["600000"]
    assert client.calls[-1] == ("600000", date(2015, 6, 1), date(2026, 1, 8))
    panel, _ = c.load_panel(["600000"], date(2026, 1, 1), date(2026, 1, 8))
    assert panel["600000"]["close"].tolist() == pytest.approx([9.9, 10.3, 10.1, 10.5])
    assert panel["600000"]["prev_close"].iloc[-1] == pytest.approx(10.1)  # 除息日的昨收已是复权后的价格


def test_rebase_refetch_keeps_full_history(tmp_path):
    days = pd.bdate_range("2026-01-05", periods=12)
    rows = [(d.strftime("%Y-%m-%d"), 10.0 + i, 10.5 + i, 9.5 + i, 10.2 + i, 1000, 1e6) for i, d in enumerate(days)]
    client = FakeClient({"600000": frame(rows[:11])})
    c = BarCache(tmp_path / "c.sqlite", client)
    c.update(["600000"], days[10].date())
    rebased = frame(rows)  # 第 12 天除息：此前 11 天整体下调 0.3
    rebased.loc[:10, ["open", "high", "low", "close"]] -= 0.3
    client.data["600000"] = rebased
    c.update(["600000"], days[11].date())
    panel, _ = c.load_panel(["600000"], days[0].date(), days[11].date())
    assert c.last_rebased == ["600000"] and len(panel["600000"]) == 12  # 不是只剩重叠的最后几行


def test_rounding_noise_is_not_a_rebase(cache):
    c, client = cache
    c.update(["600000"], date(2026, 1, 7))
    client.data["600000"] = ALL.assign(close=ALL["close"] + 0.01)  # 一分钱的舍入差异
    c.update(["600000"], date(2026, 1, 8))
    assert c.last_rebased == [] and client.calls[-1][1] == date(2026, 1, 5)


def test_missing_overlap_rows_trigger_full_refetch(cache):
    c, client = cache
    c.update(["600000"], date(2026, 1, 7))
    client.data["600000"] = ALL.iloc[1:].reset_index(drop=True)  # 数据源不再返回已存的某个日期（其余价格不变）
    c.update(["600000"], date(2026, 1, 8))
    assert c.last_rebased == ["600000"]


def test_empty_full_refetch_keeps_old_history(cache, monkeypatch):
    c, client = cache
    c.update(["600000"], date(2026, 1, 7))
    rebased = ALL.copy()
    rebased.loc[:2, ["open", "high", "low", "close"]] -= 0.3
    client.data["600000"] = rebased
    real = client.daily_bars
    monkeypatch.setattr(client, "daily_bars",  # 全量重拉那一次接口返回空表
                        lambda code, start, end: frame([]) if start == date(2015, 6, 1) else real(code, start, end))
    assert c.update(["600000"], date(2026, 1, 8)) == ["600000"]
    assert c.last_date("600000") == date(2026, 1, 7) and c.last_rebased == []


def test_dividends_roundtrip(tmp_path):
    c = BarCache(tmp_path / "c.sqlite", FakeClient({}))
    assert c.dividends("600000") == []
    c.save_dividends("600000", pd.DataFrame({"ex_date": [date(2026, 6, 20), date(2025, 6, 20)],
                                             "cash": [0.3, 0.15], "ratio": [0.5, 0.0]}))
    assert c.dividends("600000") == [(date(2025, 6, 20), 0.15, 0.0), (date(2026, 6, 20), 0.3, 0.5)]
    c.save_dividends("600000", pd.DataFrame(columns=["ex_date", "cash", "ratio"]))  # 接口偶尔返回空表
    assert len(c.dividends("600000")) == 2  # 已有记录不被清掉
    assert c.dividends("000001") == []


def test_raw_bars_incremental_and_lookup(cache):
    c, client = cache
    assert c.update_raw(["600000", "BAD"], date(2026, 1, 7), date(2026, 1, 1)) == ["BAD"]
    assert c.update_raw(["600000"], date(2026, 1, 8), date(2026, 1, 1)) == []
    assert client.raw_calls == [("600000", date(2026, 1, 1), date(2026, 1, 7)), ("BAD", date(2026, 1, 1), date(2026, 1, 7)),
                                ("600000", date(2026, 1, 8), date(2026, 1, 8))]
    bars = c.raw_bars(["600000", "000001"], date(2026, 1, 7), {"600000": "浦发银行"})
    assert list(bars) == ["600000"]
    b = bars["600000"]
    assert (b.open, b.close, b.prev_close, b.name) == (10.6, 10.4, 10.6, "浦发银行")
    assert c.last_raw_close("600000", date(2026, 1, 7)) == (date(2026, 1, 7), 10.4)
    assert c.last_raw_close("600000", date(2026, 1, 4)) is None


def test_raw_bars_none_values_become_nan(cache):
    c, _ = cache
    c.update_raw(["600000"], date(2026, 1, 8), date(2026, 1, 1))
    with c.conn:
        c.conn.execute("UPDATE bar_raw SET close=NULL WHERE date='2026-01-08'")
    assert pd.isna(c.raw_bars(["600000"], date(2026, 1, 8))["600000"].close)
    assert c.last_raw_close("600000", date(2026, 1, 8)) == (date(2026, 1, 7), 10.4)  # 跳过缺失的收盘价


def test_index_roundtrip_and_persistence(tmp_path):
    path = tmp_path / "c.sqlite"
    c = BarCache(path, FakeClient({}))
    s = pd.Series([4000.0, 4010.5], index=pd.to_datetime(["2026-01-05", "2026-01-06"]))
    c.save_index("sh000300", s)
    c.set_names({"600000": "浦发银行"})
    c2 = BarCache(path, FakeClient({}))
    out = c2.load_index("sh000300")
    assert out.index.tolist() == s.index.tolist()
    assert out.tolist() == [4000.0, 4010.5]
    assert c2.load_index("sh000905").empty
