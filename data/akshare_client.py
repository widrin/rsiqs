# data/akshare_client.py
"""AKShare 统一封装：所有调用带重试，返回英文列名。

列名映射是模块级字典，AKShare 升级改列名时只改一行；用 scripts/smoke_akshare.py 实网核实。
"""
import logging
import time
from datetime import date

import akshare as ak
import pandas as pd

log = logging.getLogger(__name__)

DAILY_COLS = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
              "收盘": "close", "成交量": "volume", "成交额": "amount"}
RAW_COLS = {**DAILY_COLS, "涨跌额": "change"}
SPOT_EM_COLS = {"代码": "code", "名称": "name", "今开": "open", "昨收": "prev_close",
                "最新价": "last", "涨跌幅": "pct", "成交量": "volume", "成交额": "amount", "最高": "high"}
SPOT_SINA_COLS = {"代码": "code", "名称": "name", "今开": "open", "昨收": "prev_close",
                  "最新价": "last", "涨跌幅": "pct", "成交量": "volume", "成交额": "amount", "最高": "high"}
SINA_VOLUME_DIVISOR = 100.0  # 新浪成交量单位为股，东财为手
CONS_CODE_COL = "成分券代码"
CLS_COLS = {"标题": "title", "内容": "content", "发布日期": "date", "发布时间": "time"}
STOCK_NEWS_COLS = {"新闻标题": "title", "新闻内容": "content", "发布时间": "time"}
NOTICE_COLS = {"代码": "code", "名称": "name", "公告标题": "title", "公告类型": "type"}
SPOT_COLS = ["code", "name", "open", "prev_close", "last", "pct", "volume", "amount", "high"]
NUMERIC = ["open", "high", "low", "close", "volume", "amount", "prev_close", "last", "pct", "change"]


class DataError(Exception):
    pass

# 新浪分红明细（stock_history_dividend_detail，indicator="分红"）：数值按每 10 股给出
DIVIDEND_COLS = {"送股": "bonus10", "转增": "transfer10", "派息": "cash10", "进度": "status", "除权除息日": "ex_date"}


def with_retry(fn, *args, attempts=3, wait=2.0, **kwargs):
    """调用 fn，失败最多重试到 attempts 次，两次之间 sleep(wait)，最后一次的异常原样抛出。"""
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 网络库异常类型繁杂
            log.warning("%s 第 %d/%d 次失败: %s", getattr(fn, "__name__", fn), i + 1, attempts, e)
            if i == attempts - 1:
                raise
            time.sleep(wait)


def _pick(df: pd.DataFrame, mapping: dict[str, str], what: str) -> pd.DataFrame:
    missing = [c for c in mapping if c not in df.columns]
    if missing:
        raise DataError(f"{what} 缺少列 {missing}，实际列 {list(df.columns)}")
    out = df[list(mapping)].rename(columns=mapping)
    for c in out.columns:
        if c in NUMERIC:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    return out


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def _records(df: pd.DataFrame, limit: int) -> list[dict]:
    return [{"title": str(r.title), "content": str(r.content), "time": str(r.time)}
            for r in df.head(limit).itertuples(index=False)]


class AkClient:
    """所有方法都经 with_retry；spot() 东财失败时切新浪。"""

    def __init__(self, attempts: int = 3, wait: float = 2.0):
        self.attempts = attempts
        self.wait = wait

    def _call(self, fn, *args, **kwargs):
        return with_retry(fn, *args, attempts=self.attempts, wait=self.wait, **kwargs)

    def trade_calendar(self) -> list[date]:
        df = self._call(ak.tool_trade_date_hist_sina)
        return sorted({pd.Timestamp(x).date() for x in df["trade_date"]})

    def daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        df = self._call(ak.stock_zh_a_hist, symbol=code, period="daily",
                        start_date=_fmt(start), end_date=_fmt(end), adjust="qfq")
        cols = ["date", "open", "high", "low", "close", "volume", "amount"]
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        out = _pick(df, DAILY_COLS, f"stock_zh_a_hist({code})")
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        return out[cols].sort_values("date").reset_index(drop=True)

    def raw_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        """不复权日线，供模拟盘结算撮合与估值：date open high low close volume amount prev_close。

        不复权价格不会因为之后的除权而改变。prev_close = 收盘 - 涨跌额，即交易所当天的参考昨收；除权除息日
        就是除权参考价，涨跌停区间据此计算才与交易所一致。涨跌额精确到分；涨跌幅只有两位小数，高价股反推
        的误差可达几角。"""
        cols = ["date", "open", "high", "low", "close", "volume", "amount", "prev_close"]
        df = self._call(ak.stock_zh_a_hist, symbol=code, period="daily",
                        start_date=_fmt(start), end_date=_fmt(end), adjust="")
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        out = _pick(df, RAW_COLS, f"stock_zh_a_hist({code}, 不复权)")
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        out["prev_close"] = (out["close"] - out["change"]).round(2)
        return out[cols].sort_values("date").reset_index(drop=True)

    def index_daily(self, symbol: str = "sh000300") -> pd.Series:
        df = self._call(ak.stock_zh_index_daily, symbol=symbol)
        idx = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
        s = pd.Series(pd.to_numeric(df["close"]).astype(float).to_numpy(), index=idx, name=symbol)
        return s.sort_index()

    def dividends(self, code: str) -> pd.DataFrame:
        """已实施的分红送转：ex_date（除权除息日）、cash（每股税前派息，元）、ratio（每股送转股数）。

        数据来自新浪，原表按每 10 股给出；预案、没有除权日、送转和派息都为 0 的行剔除。不含配股。"""
        cols = ["ex_date", "cash", "ratio"]
        df = self._call(ak.stock_history_dividend_detail, symbol=code, indicator="分红")
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        out = _pick(df, DIVIDEND_COLS, f"stock_history_dividend_detail({code})")

        def num(c: str) -> pd.Series:
            return pd.to_numeric(out[c], errors="coerce").fillna(0.0)

        res = pd.DataFrame({"ex_date": pd.to_datetime(out["ex_date"], errors="coerce").dt.date,
                            "cash": (num("cash10") / 10).round(6),
                            "ratio": ((num("bonus10") + num("transfer10")) / 10).round(6)})
        keep = ((out["status"].astype(str).str.strip() == "实施") & res["ex_date"].notna()
                & ((res["cash"] > 0) | (res["ratio"] > 0)))
        return res[keep].sort_values("ex_date").reset_index(drop=True)

    def universe(self) -> list[str]:
        """沪深300 + 中证500 当前成分股。注意：用当前成分回测存在幸存者偏差，结果偏乐观。"""
        codes: set[str] = set()
        for sym in ("000300", "000905"):
            df = self._call(ak.index_stock_cons_csindex, symbol=sym)
            codes.update(str(c).zfill(6) for c in df[CONS_CODE_COL])
        return sorted(codes)

    def spot(self) -> pd.DataFrame:
        try:
            out = _pick(self._call(ak.stock_zh_a_spot_em), SPOT_EM_COLS, "stock_zh_a_spot_em")
        except Exception as e:  # noqa: BLE001
            log.warning("东财快照失败，切换新浪: %s", e)
            try:
                out = _pick(self._call(ak.stock_zh_a_spot), SPOT_SINA_COLS, "stock_zh_a_spot")
            except Exception as e2:  # noqa: BLE001
                raise DataError(f"实时快照东财/新浪均失败: {e2}") from e2
            out["code"] = out["code"].astype(str).str[-6:]
            out["volume"] = out["volume"] / SINA_VOLUME_DIVISOR
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out["name"].astype(str)
        return out[SPOT_COLS].reset_index(drop=True)

    def news_global(self, limit: int = 50) -> list[dict]:
        df = _pick(self._call(ak.stock_info_global_cls, symbol="全部"), CLS_COLS, "stock_info_global_cls")
        df["time"] = df["date"].astype(str) + " " + df["time"].astype(str)
        return _records(df, limit)

    def stock_news(self, code: str, limit: int = 5) -> list[dict]:
        df = _pick(self._call(ak.stock_news_em, symbol=code), STOCK_NEWS_COLS, f"stock_news_em({code})")
        return _records(df.sort_values("time", ascending=False), limit)

    def notices(self, d: date) -> pd.DataFrame:
        df = self._call(ak.stock_notice_report, symbol="全部", date=_fmt(d))
        if df is None or df.empty:
            return pd.DataFrame(columns=list(NOTICE_COLS.values()))
        out = _pick(df, NOTICE_COLS, "stock_notice_report")
        out["code"] = out["code"].astype(str).str.zfill(6)
        return out.reset_index(drop=True)
