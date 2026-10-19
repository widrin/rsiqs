# data/akshare_client.py
"""AKShare 统一封装：所有调用带重试，返回英文列名。

列名映射是模块级字典，AKShare 升级改列名时只改一行；用 scripts/smoke_akshare.py 实网核实。
"""
import logging
import socket
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
# 东财个股报价（stock_bid_ask_em，item/value 两列）：盘前定稿和盘中监控只需要少数代码，不必翻全市场
BID_ASK_COLS = {"今开": "open", "昨收": "prev_close", "最新": "last", "涨幅": "pct", "总手": "volume",
                "金额": "amount", "最高": "high"}
HTTP_TIMEOUT = 15.0  # 支持 timeout 参数的接口（stock_zh_a_hist）单次请求的超时，秒
SOCKET_TIMEOUT = 30.0  # 兜底：akshare 多数接口内部用不带 timeout 的 requests.get，连接卡住时会无限阻塞


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


def _empty_on_key_error(fn, *args, **kwargs):
    """akshare 在没有结果时对空表取列，抛 KeyError（如 stock_notice_report 遇到没有公告的日子）：视为空结果。"""
    try:
        return fn(*args, **kwargs)
    except KeyError:
        return pd.DataFrame()


class AkClient:
    """所有方法都经 with_retry；快照与指数日线有备用源。

    构造时设置进程级的 socket 默认超时（SOCKET_TIMEOUT）：akshare 多数接口不设超时，连接卡住时重试和告警都
    不会触发，任务会一直挂住。"""

    def __init__(self, attempts: int = 3, wait: float = 2.0):
        self.attempts = attempts
        self.wait = wait
        if socket.getdefaulttimeout() is None:
            socket.setdefaulttimeout(SOCKET_TIMEOUT)

    def _call(self, fn, *args, **kwargs):
        return with_retry(fn, *args, attempts=self.attempts, wait=self.wait, **kwargs)

    def trade_calendar(self) -> list[date]:
        df = self._call(ak.tool_trade_date_hist_sina)
        return sorted({pd.Timestamp(x).date() for x in df["trade_date"]})

    def daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        df = self._call(ak.stock_zh_a_hist, symbol=code, period="daily",
                        start_date=_fmt(start), end_date=_fmt(end), adjust="qfq", timeout=HTTP_TIMEOUT)
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
                        start_date=_fmt(start), end_date=_fmt(end), adjust="", timeout=HTTP_TIMEOUT)
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        out = _pick(df, RAW_COLS, f"stock_zh_a_hist({code}, 不复权)")
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        out["prev_close"] = (out["close"] - out["change"]).round(2)
        return out[cols].sort_values("date").reset_index(drop=True)

    def index_daily(self, symbol: str = "sh000300") -> pd.Series:
        """新浪指数日线，失败时切东财（stock_zh_index_daily_em，symbol 同样带 sh/sz 前缀）。"""
        try:
            df = self._call(ak.stock_zh_index_daily, symbol=symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("新浪指数日线失败，切换东财: %s", e)
            try:
                df = self._call(ak.stock_zh_index_daily_em, symbol=symbol)
            except Exception as e2:  # noqa: BLE001
                raise DataError(f"指数日线新浪/东财均失败: {e2}") from e2
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

    def names(self) -> dict[str, str]:
        """沪深京全部 A 股的当前简称（交易所列表，含 ST/*ST 前缀）。每天刷新：新纳入的成分股和新戴帽的股票都靠它。"""
        df = self._call(ak.stock_info_a_code_name)
        return {str(c).zfill(6): str(n) for c, n in zip(df["code"], df["name"])}

    def spot(self, codes: list[str] | None = None) -> pd.DataFrame:
        """实时快照（竞价时段的"今开"即竞价价）。

        给出 codes 时逐只取东财个股报价：akshare 的全市场快照分页翻取、每页之间 sleep，要将近一分钟，09:25 定稿
        和盘中监控都等不起。取不到的代码不出现在结果里（按竞价数据缺失处理），名称为空。
        不给 codes 时取全市场：东财失败切新浪。"""
        if codes is not None:
            return self._spot_codes(codes)
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

    def _spot_codes(self, codes: list[str]) -> pd.DataFrame:
        rows = []
        for code in codes:
            try:
                df = self._call(ak.stock_bid_ask_em, symbol=code)
            except Exception as e:  # noqa: BLE001 - 单只失败不影响其他代码
                log.warning("个股报价 %s 失败: %s", code, e)
                continue
            kv = dict(zip(df["item"], df["value"]))
            row = {"code": str(code).zfill(6), "name": ""}
            row.update({v: kv.get(k) for k, v in BID_ASK_COLS.items()})
            rows.append(row)
        out = pd.DataFrame(rows, columns=SPOT_COLS)
        for c in SPOT_COLS[2:]:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)  # 停牌时报价为 "-"
        return out

    def news_global(self, limit: int = 50) -> list[dict]:
        """财联社电报，最新的在前。接口只返回最近 20 条（按时间升序），limit 超过 20 也只有 20 条。"""
        df = _pick(self._call(ak.stock_info_global_cls, symbol="全部"), CLS_COLS, "stock_info_global_cls")
        df["time"] = df["date"].astype(str) + " " + df["time"].astype(str)
        return _records(df.sort_values("time", ascending=False), limit)

    def stock_news(self, code: str, limit: int = 5) -> list[dict]:
        df = self._call(_empty_on_key_error, ak.stock_news_em, symbol=code)
        if df is None or df.empty:
            return []
        df = _pick(df, STOCK_NEWS_COLS, f"stock_news_em({code})")
        return _records(df.sort_values("time", ascending=False), limit)

    def notices(self, d: date) -> pd.DataFrame:
        df = self._call(_empty_on_key_error, ak.stock_notice_report, symbol="全部", date=_fmt(d))
        if df is None or df.empty:
            return pd.DataFrame(columns=list(NOTICE_COLS.values()))
        out = _pick(df, NOTICE_COLS, "stock_notice_report")
        out["code"] = out["code"].astype(str).str.zfill(6)
        return out.reset_index(drop=True)
