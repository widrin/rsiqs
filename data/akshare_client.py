# data/akshare_client.py
"""AKShare 统一封装：所有调用带重试，返回英文列名。

列名映射是模块级字典，AKShare 升级改列名时只改一行；用 scripts/smoke_akshare.py 实网核实。
"""
import json
import logging
import random
import socket
import time
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

log = logging.getLogger(__name__)

DAILY_COLS = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
              "收盘": "close", "成交量": "volume", "成交额": "amount"}
RAW_COLS = {**DAILY_COLS, "涨跌额": "change"}
SPOT_EM_COLS = {"代码": "code", "名称": "name", "今开": "open", "昨收": "prev_close",
                "最新价": "last", "涨跌幅": "pct", "成交量": "volume", "成交额": "amount", "最高": "high"}
SPOT_SINA_COLS = {"代码": "code", "名称": "name", "今开": "open", "昨收": "prev_close",
                  "最新价": "last", "涨跌幅": "pct", "成交量": "volume", "成交额": "amount", "最高": "high"}
SINA_VOLUME_DIVISOR = 100.0  # 新浪成交量单位为股，东财为手
# 新浪日线（stock_zh_a_daily）返回英文列，只取与东财日线对应的 7 列
SINA_DAILY_COLS = {"date": "date", "open": "open", "high": "high", "low": "low",
                   "close": "close", "volume": "volume", "amount": "amount"}
CONS_CODE_COL = "成分券代码"
CLS_COLS = {"标题": "title", "内容": "content", "发布日期": "date", "发布时间": "time"}
STOCK_NEWS_COLS = {"新闻标题": "title", "新闻内容": "content", "发布时间": "time"}
NOTICE_COLS = {"代码": "code", "名称": "name", "公告标题": "title", "公告类型": "type"}
SPOT_COLS = ["code", "name", "open", "prev_close", "last", "pct", "volume", "amount", "high"]
NUMERIC = ["open", "high", "low", "close", "volume", "amount", "prev_close", "last", "pct", "change"]
# 东财个股报价（stock_bid_ask_em，item/value 两列）：盘前定稿和盘中监控只需要少数代码，不必翻全市场
BID_ASK_COLS = {"今开": "open", "昨收": "prev_close", "最新": "last", "涨幅": "pct", "总手": "volume",
                "金额": "amount", "最高": "high"}
SOCKET_TIMEOUT = 30.0  # 兜底：akshare 多数接口内部用不带 timeout 的 requests.get，连接卡住时会无限阻塞
CONNECT_TIMEOUT = 5.0
# 东财日线熔断：连续失败这么多次后冷却一段时间不再请求东财（反复打限流中的接口会延长封禁），日线直走新浪
EM_FAIL_LIMIT = 3
EM_COOLDOWN = 1800.0
EM_MIN_INTERVAL = 1.0  # 东财请求最小间隔（秒）：实测 >5 次/秒、1 分钟 >200 次会触发风控
EM_JITTER = 0.3        # 间隔上的随机抖动上限（秒），避免多个任务/进程的请求节拍对齐
RAW_PREV_BUFFER = 7  # 新浪不复权回退时向前多取的自然日数：保证增量窗口也有前一根收盘来推算参考昨收
# 腾讯批量行情：东财限流时按批复用，一次最多 60 只（实测上限），GBK 编码，字段位置见 TENCENT_FIELDS
TENCENT_URL = "https://qt.gtimg.cn/q="
TENCENT_BATCH = 60
TENCENT_FIELDS = {"name": 1, "code": 2, "last": 3, "prev_close": 4, "open": 5, "volume": 6,
                  "pct": 32, "high": 33, "amount": 37}  # amount 单位为万元
TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _install_requests_default_timeout() -> None:
    """给不带 timeout 的 requests 请求补上默认超时（只装一次）。

    socket.setdefaulttimeout 管不到 requests：timeout=None 时 urllib3 会对新建的 socket 显式 settimeout(None)。
    已经显式传了 timeout 的调用不受影响。"""
    send = requests.adapters.HTTPAdapter.send
    if getattr(send, "_umberfell_default_timeout", False):
        return

    def send_with_timeout(self, request, *args, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (CONNECT_TIMEOUT, SOCKET_TIMEOUT)
        return send(self, request, *args, **kwargs)

    send_with_timeout._umberfell_default_timeout = True
    requests.adapters.HTTPAdapter.send = send_with_timeout


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


def _sina_symbol(code: str) -> str:
    """新浪日线接口的代码带交易所前缀：6 开头沪市，0/3 开头深市，其余（43/83/92 等）按北交所处理。"""
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return f"bj{code}"


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

    构造时给不带 timeout 的 requests 请求补上默认超时，并设置进程级的 socket 默认超时（兜住走 urllib 的接口）：
    akshare 多数接口不设超时，连接卡住时重试和告警都不会触发，任务会一直挂住。"""

    def __init__(self, attempts: int = 3, wait: float = 2.0, min_interval: float = 0.0,
                 jitter: float = EM_JITTER, ban_path: Path | str | None = None):
        self.attempts = attempts
        self.wait = wait
        self.min_interval = min_interval
        self.jitter = jitter
        self.ban_path = Path(ban_path) if ban_path is not None else None
        self._em_last = 0.0
        self._em_fail_streak = 0
        self._em_skip_until = 0.0
        self._read_ban_state()
        _install_requests_default_timeout()
        if socket.getdefaulttimeout() is None:
            socket.setdefaulttimeout(SOCKET_TIMEOUT)

    def _call(self, fn, *args, **kwargs):
        return with_retry(fn, *args, attempts=self.attempts, wait=self.wait, **kwargs)

    def _em_get(self, fn, *args, **kwargs):
        """东财请求统一入口：串行限流 + 抖动。akshare 内部没有节流，逐个股票串行打过去就会被风控。"""
        self._em_throttle()
        return self._call(fn, *args, **kwargs)

    def _em_throttle(self) -> None:
        """距上一次东财请求不足 min_interval（再加随机抖动）时先等待。min_interval=0 时不限流（测试）。"""
        if self.min_interval <= 0:
            return
        target = self._em_last + self.min_interval
        if self.jitter > 0:
            target += random.uniform(0, self.jitter)
        delay = target - time.time()
        if delay > 0:
            time.sleep(delay)
        self._em_last = time.time()

    def _read_ban_state(self) -> None:
        """从 ban_path 读取跨进程的熔断状态（文件是唯一事实来源）；未配置或文件损坏时保持内存状态。"""
        if self.ban_path is None:
            return
        try:
            data = json.loads(self.ban_path.read_text(encoding="utf-8"))
            self._em_skip_until = float(data.get("until", 0.0))
            self._em_fail_streak = int(data.get("fails", 0))
        except (OSError, ValueError, TypeError):
            pass

    def _write_ban_state(self) -> None:
        if self.ban_path is None:
            return
        try:
            self.ban_path.parent.mkdir(parents=True, exist_ok=True)
            self.ban_path.write_text(json.dumps({"until": self._em_skip_until,
                                                 "fails": self._em_fail_streak}),
                                     encoding="utf-8")
        except OSError as e:
            log.warning("东财熔断状态写入失败(%s): %s", self.ban_path, e)

    def _em_allow(self) -> bool:
        """熔断冷却期已过才允许请求东财；配置了 ban_path 时每次都同步其他进程写入的封禁状态。"""
        if self.ban_path is not None:
            self._read_ban_state()
        return time.time() >= self._em_skip_until

    def _em_note(self, ok: bool) -> None:
        """记录一次东财调用结果：成功清零计数；失败按底层请求计（with_retry 已重试 attempts 次），
        达到 EM_FAIL_LIMIT 后熔断 EM_COOLDOWN——反复打限流中的接口会把封禁越打越长。"""
        if ok:
            if self._em_fail_streak or self._em_skip_until:
                self._em_fail_streak = 0
                self._em_skip_until = 0.0
                self._write_ban_state()
            return
        self._em_fail_streak += self.attempts
        if self._em_fail_streak >= EM_FAIL_LIMIT:
            self._em_skip_until = time.time() + EM_COOLDOWN
            log.warning("东财连续 %d 次请求失败，%.0f 分钟内日线直接走新浪（避免延长限流）",
                        self._em_fail_streak, EM_COOLDOWN / 60)
        self._write_ban_state()

    def trade_calendar(self) -> list[date]:
        df = self._call(ak.tool_trade_date_hist_sina)
        return sorted({pd.Timestamp(x).date() for x in df["trade_date"]})

    def daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        """前复权日线：东财 stock_zh_a_hist，限流/异常时切新浪 stock_zh_a_daily。

        东财连续失败达到 EM_FAIL_LIMIT 后熔断 EM_COOLDOWN：冷却期内不再请求东财，
        日线全部直走新浪（全量回填 800 股时每只都重试东财会把限流越打越长）。
        冷却结束后第一次尝试若仍失败会立即重新熔断；一次成功即清零计数。

        新浪列名为英文、成交量单位是股（东财为手），除以 SINA_VOLUME_DIVISOR 拉齐。两家的前复权
        算法略有差异，回测评估只要求同一只股票的序列内部一致，混合来源可接受。"""
        sina = True
        if self._em_allow():
            try:
                df = self._em_get(ak.stock_zh_a_hist, symbol=code, period="daily",
                                  start_date=_fmt(start), end_date=_fmt(end), adjust="qfq")
                sina = False
                self._em_note(True)
            except Exception as e:  # noqa: BLE001 - 东财限流/接口异常时都走新浪回退
                self._em_note(False)
                log.warning("东财日线失败(%s)，切换新浪: %s", code, e)
        if sina:
            df = self._call(ak.stock_zh_a_daily, symbol=_sina_symbol(code),
                            start_date=_fmt(start), end_date=_fmt(end), adjust="qfq")
        cols = ["date", "open", "high", "low", "close", "volume", "amount"]
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        out = _pick(df, SINA_DAILY_COLS if sina else DAILY_COLS, f"daily_bars({code})")
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        if sina:
            out["volume"] = out["volume"] / SINA_VOLUME_DIVISOR
        return out[cols].sort_values("date").reset_index(drop=True)

    def raw_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        """不复权日线，供模拟盘结算撮合与估值：date open high low close volume amount prev_close。

        不复权价格不会因为之后的除权而改变。东财为主：prev_close = 收盘 - 涨跌额，即交易所当天的参考
        昨收；除权除息日就是除权参考价，涨跌停区间据此计算才与交易所一致。涨跌额精确到分；涨跌幅只有
        两位小数，高价股反推的误差可达几角。

        东财限流/异常时切新浪 stock_zh_a_daily（默认不复权）：新浪没有涨跌额，参考昨收用前一根收盘
        推算——向前多取 RAW_PREV_BUFFER 个自然日，保证增量窗口（可能只有一天）也有前一根；停牌多日
        的股票参考昨收就是停牌前收盘，语义一致。推算的参考昨收在除权日会偏差（偏高），结算的除权检查
        会漏检：东财恢复后重跑结算增量即可修正，依赖它的场景只有东财不可用时的结算兜底。"""
        cols = ["date", "open", "high", "low", "close", "volume", "amount", "prev_close"]
        sina = True
        if self._em_allow():
            try:
                df = self._em_get(ak.stock_zh_a_hist, symbol=code, period="daily",
                                  start_date=_fmt(start), end_date=_fmt(end), adjust="")
                sina = False
                self._em_note(True)
            except Exception as e:  # noqa: BLE001
                self._em_note(False)
                log.warning("东财不复权日线失败(%s)，切换新浪: %s", code, e)
        if sina:
            df = self._call(ak.stock_zh_a_daily, symbol=_sina_symbol(code),
                            start_date=_fmt(start - timedelta(days=RAW_PREV_BUFFER)),
                            end_date=_fmt(end))
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        out = _pick(df, SINA_DAILY_COLS if sina else RAW_COLS, f"raw_bars({code})")
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        if sina:
            out["volume"] = out["volume"] / SINA_VOLUME_DIVISOR
            out["prev_close"] = out["close"].shift(1).round(2)
            out = out[out["date"] >= pd.Timestamp(start)]  # 去掉用于推算前收盘的前置缓冲
        else:
            out["prev_close"] = (out["close"] - out["change"]).round(2)
        return out[cols].sort_values("date").reset_index(drop=True)

    def index_daily(self, symbol: str = "sh000300") -> pd.Series:
        """新浪指数日线，失败时切东财（stock_zh_index_daily_em，symbol 同样带 sh/sz 前缀）。"""
        try:
            df = self._call(ak.stock_zh_index_daily, symbol=symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("新浪指数日线失败，切换东财: %s", e)
            try:
                df = self._em_get(ak.stock_zh_index_daily_em, symbol=symbol)
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
        """沪深 A 股的当前简称（交易所列表，含 ST/*ST 前缀）。每天刷新：新纳入的成分股和新戴帽的股票都靠它。

        上交所主板、科创板、深交所分别取，取到哪个合并哪个；股票池用不到北交所，不取。全部失败才抛 DataError。"""
        sources = [(ak.stock_info_sh_name_code, "主板A股", "证券代码", "证券简称"),
                   (ak.stock_info_sh_name_code, "科创板", "证券代码", "证券简称"),
                   (ak.stock_info_sz_name_code, "A股列表", "A股代码", "A股简称")]
        out: dict[str, str] = {}
        failed = 0
        for fn, symbol, code_col, name_col in sources:
            try:
                df = self._call(fn, symbol=symbol)
                out.update({str(c).zfill(6): str(n) for c, n in zip(df[code_col], df[name_col])})
            except Exception as e:  # noqa: BLE001
                log.warning("股票简称（%s）获取失败: %s", symbol, e)
                failed += 1
        if failed == len(sources):
            raise DataError("交易所股票简称列表全部获取失败")
        return out

    def spot(self, codes: list[str] | None = None, sink: list | None = None,
             fallback: bool = False) -> pd.DataFrame:
        """实时快照（竞价时段的"今开"即竞价价）。

        给出 codes 时逐只取东财个股报价：akshare 的全市场快照分页翻取、每页之间 sleep，要将近一分钟，09:25 定稿
        和盘中监控都等不起。取不到的代码不出现在结果里（按竞价数据缺失处理），名称为空；一只都取不到时抛
        DataError。sink 不为 None 时每取到一只就追加一行（dict），调用方超时后可以用已取到的部分。
        fallback=True 时对仍然缺报价的代码先取腾讯批量行情（快、不限频），仍缺再回退新浪全市场快照
        （一次将近一分钟）：盘中监控容忍这个耗时，09:25 定稿有 09:28 的时限，宁可当缺失处理，所以默认不开。
        不给 codes 时取全市场：东财失败切新浪。"""
        if codes is not None:
            try:
                out = self._spot_codes(codes, sink)
            except DataError:
                if not fallback:
                    raise
                out = pd.DataFrame(sink or [], columns=SPOT_COLS)
            if not fallback:
                return out
            have = {str(c).zfill(6) for c in out["code"]}
            missing = [c for c in codes if str(c).zfill(6) not in have]
            if missing:
                out = pd.concat([out, self._snapshot_rows(missing)], ignore_index=True)
            if codes and out.empty:
                raise DataError(f"个股报价全部获取失败（{len(codes)} 只，含全市场快照回退）")
            return out[SPOT_COLS]
        try:
            out = _pick(self._em_get(ak.stock_zh_a_spot_em), SPOT_EM_COLS, "stock_zh_a_spot_em")
            out["code"] = out["code"].astype(str).str.zfill(6)
            out["name"] = out["name"].astype(str)
            return out[SPOT_COLS].reset_index(drop=True)
        except Exception as e:  # noqa: BLE001
            log.warning("东财快照失败，切换新浪: %s", e)
        try:
            return self._full_snapshot_sina()
        except Exception as e2:  # noqa: BLE001
            raise DataError(f"实时快照东财/新浪均失败: {e2}") from e2

    def _full_snapshot_sina(self) -> pd.DataFrame:
        """新浪全市场快照（一次将近一分钟），东财不可用时的兜底。成交量单位为股，除以 100 拉齐成手。"""
        out = _pick(self._call(ak.stock_zh_a_spot), SPOT_SINA_COLS, "stock_zh_a_spot")
        out["code"] = out["code"].astype(str).str[-6:].str.zfill(6)
        out["name"] = out["name"].astype(str)
        out["volume"] = out["volume"] / SINA_VOLUME_DIVISOR
        return out[SPOT_COLS].reset_index(drop=True)

    def _tencent_rows(self, codes: list[str]) -> pd.DataFrame:
        """腾讯批量行情（qt.gtimg.cn）：一次最多 TENCENT_BATCH 只，GBK 编码，报价为手、成交额为万元。

        停牌/无报价的代码腾讯不返回（或字段为 "-"），按缺失处理，由调用方决定是否再走全市场快照。"""
        rows = []
        for i in range(0, len(codes), TENCENT_BATCH):
            syms = ",".join(_sina_symbol(str(c).zfill(6)) for c in codes[i:i + TENCENT_BATCH])
            resp = requests.get(TENCENT_URL + syms, timeout=(CONNECT_TIMEOUT, 10),
                                headers=TENCENT_HEADERS)
            resp.encoding = "gbk"
            resp.raise_for_status()
            for part in resp.text.split(";"):
                if '="' not in part:
                    continue
                fields = part.split('="', 1)[1].rsplit('"', 1)[0].split("~")
                if len(fields) <= max(TENCENT_FIELDS.values()):
                    continue
                if not fields[TENCENT_FIELDS["code"]].strip():
                    continue
                row = {"code": fields[TENCENT_FIELDS["code"]].strip(),
                       "name": fields[TENCENT_FIELDS["name"]].strip()}
                for k in ("open", "prev_close", "last", "pct", "high", "volume", "amount"):
                    row[k] = fields[TENCENT_FIELDS[k]]
                rows.append(row)
        if not rows:
            return pd.DataFrame(columns=SPOT_COLS)
        out = pd.DataFrame(rows, columns=SPOT_COLS)
        for c in SPOT_COLS[2:]:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
        out["amount"] = out["amount"] * 10000  # 腾讯成交额单位为万元
        out["code"] = out["code"].astype(str).str.zfill(6)
        return out

    def _snapshot_rows(self, codes: list[str]) -> pd.DataFrame:
        """补齐缺报价：先腾讯批量行情，仍缺再回退新浪全市场快照。都失败时返回空表，按缺报价处理。"""
        rows = pd.DataFrame(columns=SPOT_COLS)
        try:
            rows = self._tencent_rows(codes)
        except Exception as e:  # noqa: BLE001 - 回退失败不应掩盖原来的缺报价
            log.warning("腾讯批量行情失败: %s", e)
        have = set(rows["code"]) if not rows.empty else set()
        missing = [c for c in codes if str(c).zfill(6) not in have]
        if not missing:
            return rows[SPOT_COLS]
        try:
            full = self._full_snapshot_sina()
        except Exception as e:  # noqa: BLE001
            log.warning("个股报价回退全市场快照失败: %s", e)
            return rows[SPOT_COLS]
        wanted = {str(c).zfill(6) for c in missing}
        return pd.concat([rows, full[full["code"].isin(wanted)]], ignore_index=True)[SPOT_COLS]

    def _spot_codes(self, codes: list[str], sink: list | None = None) -> pd.DataFrame:
        rows = [] if sink is None else sink
        for code in codes:
            try:
                df = self._em_get(ak.stock_bid_ask_em, symbol=code)
            except Exception as e:  # noqa: BLE001 - 单只失败不影响其他代码
                log.warning("个股报价 %s 失败: %s", code, e)
                continue
            kv = dict(zip(df["item"], df["value"]))
            missing = [k for k in BID_ASK_COLS if k not in kv]
            if missing:  # 字段改名不能静默变成 NaN：竞价过滤和盘中监控会悄悄失效
                raise DataError(f"stock_bid_ask_em 缺少字段 {missing}（AKShare 可能改了字段名，更新 BID_ASK_COLS）")
            row = {"code": str(code).zfill(6), "name": ""}
            # 停牌时报价为 "-"：转成 NaN
            row.update({v: float(pd.to_numeric(kv.get(k), errors="coerce")) for k, v in BID_ASK_COLS.items()})
            rows.append(row)
        if codes and not rows:
            raise DataError(f"个股报价全部获取失败（{len(codes)} 只）")
        return pd.DataFrame(rows, columns=SPOT_COLS)

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
