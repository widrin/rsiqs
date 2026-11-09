# tests/test_market.py
from datetime import date

import pandas as pd

from agents import market


class FakeStore:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeCache:
    def __init__(self, series=None):
        self.series = series or {}

    def load_index(self, symbol):
        return self.series.get(symbol, pd.Series(dtype=float))

    def save_index(self, symbol, s):
        self.series[symbol] = s


class FakeClient:
    def __init__(self, spot=None, boards=None, index=None, fail=None):
        self.spot_df = spot
        self.boards_df = boards
        self.index = index or {}
        self.fail = fail or set()
        self.calls = 0

    def spot(self):
        self.calls += 1
        if "spot" in self.fail:
            raise RuntimeError("spot down")
        return self.spot_df

    def industry_boards(self):
        self.calls += 1
        if "boards" in self.fail:
            raise RuntimeError("boards down")
        return self.boards_df

    def index_daily(self, symbol):
        self.calls += 1
        if "index" in self.fail:
            raise RuntimeError("index down")
        return self.index[symbol]


D = date(2026, 11, 6)


def _fixture():
    idx = pd.Timestamp(D)
    days = pd.DatetimeIndex([idx - pd.Timedelta(days=1), idx])
    cache = FakeCache({"sh000300": pd.Series([4000.0, 4040.0], index=days),
                       "sh000001": pd.Series([3200.0, 3184.0], index=days)})
    spot = pd.DataFrame({"code": ["1", "2", "3"], "pct": [1.0, -2.0, 0.0],
                         "amount": [1e8, 2e8, 3e8]})
    boards = pd.DataFrame({"board": ["煤炭", "银行", "计算机", "军工"],
                           "pct": [3.2, 1.0, -2.0, -3.0]})
    return cache, FakeClient(spot=spot, boards=boards)


def test_collect_index_breadth_sectors():
    cache, client = _fixture()
    data = market.collect(FakeStore(), cache, client, D)
    assert [i["name"] for i in data["indexes"]] == ["沪深300", "上证指数"]
    assert round(data["indexes"][0]["pct"], 6) == 0.01
    assert round(data["indexes"][1]["pct"], 6) == -0.005
    assert data["breadth"] == {"up": 1, "down": 1, "flat": 1, "amount": 6.0}
    assert [s["board"] for s in data["sectors"]["top"]] == ["煤炭", "银行", "计算机", "军工"]
    assert [s["board"] for s in data["sectors"]["bottom"]] == ["军工", "计算机", "银行", "煤炭"]
    assert data["complete"] is True


def test_collect_caches_complete_result():
    cache, client = _fixture()
    store = FakeStore()
    market.collect(store, cache, client, D)
    calls = client.calls
    data = market.collect(store, cache, client, D)
    assert client.calls == calls  # 命中 kv，不再拉数据
    assert data["breadth"]["amount"] == 6.0


def test_partial_failure_not_cached_and_no_crash():
    cache, client = _fixture()
    store = FakeStore()
    client.fail = {"boards"}
    data = market.collect(store, cache, client, D)
    assert data["indexes"] and data["breadth"] and data["sectors"] is None
    assert data["complete"] is False
    assert f"market:{D.isoformat()}" not in store.kv


def test_collect_without_client_is_none():
    assert market.collect(FakeStore(), FakeCache(), None, D) is None


def test_summary_lines_and_bench():
    cache, client = _fixture()
    data = market.collect(FakeStore(), cache, client, D)
    lines = market.summary_lines(data)
    assert lines[0].startswith("沪深300 4,040.00（+1.00%）")
    assert "涨跌家数：上涨 1 / 下跌 1 / 平盘 1，成交额 6 亿" in lines[1]
    assert "板块领涨：煤炭 +3.20%" in lines[2]
    assert "板块领跌：军工 -3.00%" in lines[3]
    assert round(market.bench_pct(data), 6) == 0.01
    assert market.bench_pct(None) is None
