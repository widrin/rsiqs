# tests/test_akshare_client.py
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import data.akshare_client as akc
from data.akshare_client import AkClient, DataError, with_retry


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(akc.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def fake_ak(monkeypatch, **funcs):
    monkeypatch.setattr(akc, "ak", SimpleNamespace(**funcs))


def test_with_retry_succeeds_after_failures(no_sleep):
    calls = []

    def flaky(x, y=0):
        calls.append(x)
        if len(calls) < 3:
            raise ConnectionError("boom")
        return x + y

    assert with_retry(flaky, 1, y=2, attempts=3, wait=1.5) == 3
    assert len(calls) == 3
    assert no_sleep == [1.5, 1.5]


def test_with_retry_reraises_last_error(no_sleep):
    n = []

    def bad():
        n.append(1)
        raise ValueError(f"fail {len(n)}")

    with pytest.raises(ValueError, match="fail 2"):
        with_retry(bad, attempts=2, wait=0)
    assert len(no_sleep) == 1


def test_trade_calendar(monkeypatch):
    df = pd.DataFrame({"trade_date": [date(2026, 1, 6), date(2026, 1, 5), date(2026, 1, 5)]})
    fake_ak(monkeypatch, tool_trade_date_hist_sina=lambda: df)
    assert AkClient().trade_calendar() == [date(2026, 1, 5), date(2026, 1, 6)]


def test_daily_bars_normalizes_chinese_columns(monkeypatch):
    seen = {}
    raw = pd.DataFrame({
        "日期": ["2026-01-06", "2026-01-05"], "股票代码": ["600000", "600000"],
        "开盘": [10.1, 10.0], "收盘": [10.3, 10.2], "最高": [10.5, 10.4],
        "最低": [10.0, 9.9], "成交量": [1200, 1000], "成交额": [1.2e6, 1.0e6],
        "振幅": [1.0, 1.0],
    })

    def hist(**kw):
        seen.update(kw)
        return raw

    fake_ak(monkeypatch, stock_zh_a_hist=hist)
    df = AkClient().daily_bars("600000", date(2026, 1, 5), date(2026, 1, 6))
    assert seen == {"symbol": "600000", "period": "daily", "start_date": "20260105",
                    "end_date": "20260106", "adjust": "qfq"}
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]
    assert df["date"].tolist() == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
    assert df["close"].tolist() == [10.2, 10.3]
    assert df["volume"].dtype == float


def test_raw_bars_unadjusted_with_reference_prev_close(monkeypatch):
    seen = {}
    raw = pd.DataFrame({  # 1 月 6 日 10 转 5：除权参考价 10.40 / 1.5 = 6.93，当天收 7.00，涨跌额 0.07
        "日期": ["2026-01-06", "2026-01-05"], "开盘": [6.95, 10.1], "收盘": [7.0, 10.4], "最高": [7.1, 10.5],
        "最低": [6.9, 10.0], "成交量": [1500, 1000], "成交额": [1.05e6, 1.04e6], "涨跌幅": [1.01, 1.96],
        "涨跌额": [0.07, 0.2]})

    def hist(**kw):
        seen.update(kw)
        return raw

    fake_ak(monkeypatch, stock_zh_a_hist=hist)
    df = AkClient().raw_bars("600000", date(2026, 1, 5), date(2026, 1, 6))
    assert seen["adjust"] == ""
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "prev_close"]
    assert df["prev_close"].tolist() == [10.2, 6.93]  # 收盘 - 涨跌额：交易所当天的参考昨收
    # 高价股：昨收 1500.00，收 1523.45，涨跌幅只有两位小数（1.56%），按它反推会得到 1500.05
    raw2 = raw.iloc[:1].assign(收盘=1523.45, 最高=1530.0, 最低=1500.0, 开盘=1505.0, 涨跌幅=1.56, 涨跌额=23.45)
    fake_ak(monkeypatch, stock_zh_a_hist=lambda **kw: raw2)
    assert AkClient().raw_bars("600519", date(2026, 1, 6), date(2026, 1, 6))["prev_close"].tolist() == [1500.0]


def test_daily_bars_empty_and_bad_columns(monkeypatch):
    fake_ak(monkeypatch, stock_zh_a_hist=lambda **kw: pd.DataFrame())
    empty = AkClient().daily_bars("600000", date(2026, 1, 5), date(2026, 1, 6))
    assert empty.empty and "close" in empty.columns

    fake_ak(monkeypatch, stock_zh_a_hist=lambda **kw: pd.DataFrame({"日期": ["2026-01-05"]}))
    with pytest.raises(DataError, match="收盘"):
        AkClient().daily_bars("600000", date(2026, 1, 5), date(2026, 1, 6))


def test_index_daily(monkeypatch):
    raw = pd.DataFrame({"date": ["2026-01-06", "2026-01-05"], "open": [1, 1],
                        "high": [1, 1], "low": [1, 1], "close": [4010.5, 4000.0], "volume": [1, 1]})
    fake_ak(monkeypatch, stock_zh_index_daily=lambda symbol: raw)
    s = AkClient().index_daily()
    assert s.index.tolist() == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
    assert s.tolist() == [4000.0, 4010.5]
    assert s.name == "sh000300"


def test_dividends_keeps_implemented_events_per_share(monkeypatch):
    seen = {}

    def detail(symbol, indicator):
        seen.update(symbol=symbol, indicator=indicator)
        return pd.DataFrame({  # 新浪原表：每 10 股送股、转增（股）、派息（税前，元）
            "公告日期": [date(2026, 6, 1), date(2025, 6, 1), date(2024, 6, 1), date(2023, 6, 1)],
            "送股": [0.0, 3.0, float("nan"), 0.0], "转增": [5.0, 0.0, 0.0, 0.0],
            "派息": [3.0, 1.5, 2.0, 0.0], "进度": ["实施", "实施", "预案", "实施"],
            "除权除息日": [date(2026, 6, 20), date(2025, 6, 20), date(2024, 6, 20), date(2023, 6, 20)],
            "股权登记日": [None] * 4, "红股上市日": [None] * 4})

    fake_ak(monkeypatch, stock_history_dividend_detail=detail)
    df = AkClient().dividends("600000")
    assert seen == {"symbol": "600000", "indicator": "分红"}
    assert list(df.columns) == ["ex_date", "cash", "ratio"]
    # 按除权日升序；未实施的预案和全为 0 的"不分配"剔除；换算成每股
    assert df.to_dict("records") == [{"ex_date": date(2025, 6, 20), "cash": 0.15, "ratio": 0.3},
                                     {"ex_date": date(2026, 6, 20), "cash": 0.3, "ratio": 0.5}]


def test_dividends_empty_and_bad_columns(monkeypatch):
    fake_ak(monkeypatch, stock_history_dividend_detail=lambda symbol, indicator: pd.DataFrame())
    assert AkClient().dividends("600000").empty
    fake_ak(monkeypatch, stock_history_dividend_detail=lambda symbol, indicator: pd.DataFrame({"派息": [1.0]}))
    with pytest.raises(DataError):
        AkClient().dividends("600000")


def test_universe_unions_csi300_and_csi500(monkeypatch):
    cons = {"000300": pd.DataFrame({"成分券代码": ["600000", "1"]}),
            "000905": pd.DataFrame({"成分券代码": ["600000", "300750"]})}
    fake_ak(monkeypatch, index_stock_cons_csindex=lambda symbol: cons[symbol])
    assert AkClient().universe() == ["000001", "300750", "600000"]


EM_SPOT = pd.DataFrame({
    "序号": [1, 2], "代码": ["600000", "300750"], "名称": ["浦发银行", "宁德时代"],
    "最新价": [10.5, 200.0], "涨跌幅": [2.0, -1.0], "成交量": [1000, 2000],
    "成交额": [1.0e6, 4.0e7], "最高": [10.6, 203.0], "今开": [10.2, 201.0], "昨收": [10.3, 202.0],
})
SINA_SPOT = pd.DataFrame({
    "代码": ["sh600000", "sz300750"], "名称": ["浦发银行", "宁德时代"], "最新价": [10.5, 200.0],
    "涨跌额": [0.2, -2.0], "涨跌幅": [2.0, -1.0], "昨收": [10.3, 202.0], "今开": [10.2, 201.0],
    "最高": [10.6, 203.0], "最低": [10.1, 199.0], "成交量": [100000, 200000], "成交额": [1.0e6, 4.0e7],
})
SPOT_COLS = ["code", "name", "open", "prev_close", "last", "pct", "volume", "amount", "high"]


def test_spot_em(monkeypatch):
    fake_ak(monkeypatch, stock_zh_a_spot_em=lambda: EM_SPOT,
            stock_zh_a_spot=lambda: pytest.fail("fallback should not be called"))
    df = AkClient().spot()
    assert list(df.columns) == SPOT_COLS
    row = df.set_index("code").loc["300750"]
    assert (row["open"], row["prev_close"], row["last"], row["high"]) == (201.0, 202.0, 200.0, 203.0)


def test_spot_falls_back_to_sina_when_em_raises(monkeypatch):
    def em():
        raise ConnectionError("em down")

    fake_ak(monkeypatch, stock_zh_a_spot_em=em, stock_zh_a_spot=lambda: SINA_SPOT)
    df = AkClient().spot()
    assert list(df.columns) == SPOT_COLS
    assert df["code"].tolist() == ["600000", "300750"]
    assert df["volume"].tolist() == [1000.0, 2000.0]  # 股 -> 手


def test_spot_both_fail_raises_data_error(monkeypatch):
    def down():
        raise ConnectionError("down")

    fake_ak(monkeypatch, stock_zh_a_spot_em=down, stock_zh_a_spot=down)
    with pytest.raises(DataError):
        AkClient().spot()


def test_news_global(monkeypatch):
    raw = pd.DataFrame({"标题": ["a", "b", "c"], "内容": ["ca", "cb", "cc"],
                        "发布日期": ["2026-10-15"] * 3, "发布时间": ["09:00:00", "09:01:00", "09:02:00"]})
    fake_ak(monkeypatch, stock_info_global_cls=lambda symbol: raw)
    items = AkClient().news_global(limit=2)
    assert items == [{"title": "a", "content": "ca", "time": "2026-10-15 09:00:00"},
                     {"title": "b", "content": "cb", "time": "2026-10-15 09:01:00"}]


def test_stock_news(monkeypatch):
    raw = pd.DataFrame({"关键词": ["600000"] * 2, "新闻标题": ["t1", "t2"], "新闻内容": ["c1", "c2"],
                        "发布时间": ["2026-10-15 08:00:00", "2026-10-14 08:00:00"],
                        "文章来源": ["x", "y"], "新闻链接": ["u1", "u2"]})
    fake_ak(monkeypatch, stock_news_em=lambda symbol: raw)
    assert AkClient().stock_news("600000", limit=1) == [
        {"title": "t1", "content": "c1", "time": "2026-10-15 08:00:00"}]


def test_notices(monkeypatch):
    seen = {}
    raw = pd.DataFrame({"代码": ["600000"], "名称": ["浦发银行"], "公告标题": ["年报"],
                        "公告类型": ["定期报告"], "公告日期": ["2026-10-15"], "网址": ["u"]})

    def report(symbol, date):
        seen.update(symbol=symbol, date=date)
        return raw

    fake_ak(monkeypatch, stock_notice_report=report)
    df = AkClient().notices(date(2026, 10, 15))
    assert seen == {"symbol": "全部", "date": "20261015"}
    assert list(df.columns) == ["code", "name", "title", "type"]
    assert df.iloc[0].tolist() == ["600000", "浦发银行", "年报", "定期报告"]
