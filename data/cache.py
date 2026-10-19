# data/cache.py
"""SQLite 日线缓存：按股票增量更新，产出 DataView 用的 panel；另存已实施的分红送转。

前复权价会在除权后整体变化。增量更新时从已存的最后 OVERLAP_ROWS 行开始取，重叠部分的价格差超过
ADJ_TOL 就说明复权基准变了，删掉这只股票重新全量下载，避免在除权日留下跳变（昨收错位）。
"""
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from engine.types import Bar

log = logging.getLogger(__name__)

BAR_COLS = ["open", "high", "low", "close", "volume", "amount"]
PRICE_COLS = ["open", "high", "low", "close"]
OVERLAP_ROWS = 5
ADJ_TOL = 0.011  # 元：一分钱以内是舍入差异，超过即视为复权基准变化
SCHEMA = """
CREATE TABLE IF NOT EXISTS bar_daily (
    code TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    PRIMARY KEY (code, date));
CREATE TABLE IF NOT EXISTS bar_names (code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS index_daily (
    symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS bar_raw (
    code TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL, prev_close REAL,
    PRIMARY KEY (code, date));
CREATE TABLE IF NOT EXISTS dividends (
    code TEXT NOT NULL, ex_date TEXT NOT NULL, cash REAL NOT NULL, ratio REAL NOT NULL,
    PRIMARY KEY (code, ex_date));
"""


class BarCache:
    def __init__(self, db_path, client):
        self.client = client
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.last_rebased: list[str] = []  # 最近一次 update 中因复权基准变化而全量重拉的代码

    def last_date(self, code: str) -> date | None:
        row = self.conn.execute("SELECT MAX(date) FROM bar_daily WHERE code=?", (code,)).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def codes(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT code FROM bar_daily ORDER BY code")]

    def _tail(self, code: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT date, open, high, low, close FROM bar_daily WHERE code=? ORDER BY date DESC LIMIT ?",
            self.conn, params=(code, OVERLAP_ROWS))

    def _rebased(self, tail: pd.DataFrame, df: pd.DataFrame) -> bool:
        """重叠的日期上任一价格相差超过 ADJ_TOL，或数据源不再返回这些日期，都视为复权基准变化。"""
        new = df.assign(date=[pd.Timestamp(x).date().isoformat() for x in df["date"]])
        both = tail.merge(new[["date", *PRICE_COLS]], on="date", suffixes=("_old", ""))
        if len(both) < len(tail):
            return True
        diff = max(float((both[c] - both[f"{c}_old"]).abs().max()) for c in PRICE_COLS)
        return not diff <= ADJ_TOL  # NaN 也算变化

    def _delete(self, code: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM bar_daily WHERE code=?", (code,))

    def update(self, codes: list[str], end: date, start_default: date = date(2015, 6, 1),
               full: bool = False) -> list[str]:
        """逐只增量更新，返回失败的代码列表（单只失败不影响其他）。复权基准变化的代码会全量重拉，
        记在 self.last_rebased。"""
        failed = []
        self.last_rebased = []
        for code in codes:
            try:
                if full:
                    self._delete(code)
                last = self.last_date(code)
                if last and last >= end:
                    continue
                tail = self._tail(code) if last else None
                start = date.fromisoformat(tail["date"].iloc[-1]) if last else start_default
                df = self.client.daily_bars(code, start, end)
                if last and not df.empty and self._rebased(tail, df):
                    log.info("%s 复权基准变化，全量重新下载", code)
                    full_df = self.client.daily_bars(code, start_default, end)
                    if full_df.empty:  # 先拿到完整数据再删旧数据，避免一次接口故障清空历史
                        raise RuntimeError("全量重新下载返回空表")
                    self._delete(code)
                    self.last_rebased.append(code)
                    df, start = full_df, start_default
                if df.empty:
                    continue
                rows = [(code, pd.Timestamp(r.date).date().isoformat(), r.open, r.high, r.low,
                         r.close, r.volume, r.amount)
                        for r in df.itertuples(index=False) if pd.Timestamp(r.date).date() >= start]
                with self.conn:
                    self.conn.executemany(
                        "INSERT OR REPLACE INTO bar_daily VALUES (?,?,?,?,?,?,?,?)", rows)
            except Exception as e:  # noqa: BLE001
                log.warning("更新 %s 失败: %s", code, e)
                failed.append(code)
        return failed

    def set_names(self, names: dict) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO bar_names VALUES (?,?)",
                                  [(str(k), str(v)) for k, v in names.items()])

    def load_panel(self, codes: list[str], start: date, end: date
                   ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """panel[code]：DatetimeIndex 升序，列 open high low close volume amount prev_close。"""
        panel: dict[str, pd.DataFrame] = {}
        for code in codes:
            df = pd.read_sql_query(
                "SELECT date, open, high, low, close, volume, amount FROM bar_daily "
                "WHERE code=? AND date<=? ORDER BY date", self.conn, params=(code, end.isoformat()))
            if df.empty:
                continue
            df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("date"))).normalize()
            df = df.astype(float)
            df["prev_close"] = df["close"].shift(1).fillna(df["open"])
            df = df.loc[pd.Timestamp(start):]
            if not df.empty:
                panel[code] = df
        known = dict(self.conn.execute("SELECT code, name FROM bar_names"))
        names = {code: known.get(code, "") for code in panel}
        return panel, names

    def update_raw(self, codes: list[str], end: date, start_default: date) -> list[str]:
        """不复权日线增量更新（只供模拟盘结算）；不复权价格不会因除权而变，不需要重叠比对。返回失败的代码。"""
        failed = []
        for code in codes:
            try:
                row = self.conn.execute("SELECT MAX(date) FROM bar_raw WHERE code=?", (code,)).fetchone()
                start = date.fromisoformat(row[0]) + timedelta(days=1) if row[0] else start_default
                if start > end:
                    continue
                df = self.client.raw_bars(code, start, end)
                rows = [(code, pd.Timestamp(r.date).date().isoformat(), r.open, r.high, r.low, r.close,
                         r.volume, r.amount, r.prev_close) for r in df.itertuples(index=False)]
                with self.conn:
                    self.conn.executemany("INSERT OR REPLACE INTO bar_raw VALUES (?,?,?,?,?,?,?,?,?)", rows)
            except Exception as e:  # noqa: BLE001
                log.warning("更新 %s 不复权日线失败: %s", code, e)
                failed.append(code)
        return failed

    def raw_bars(self, codes: list[str], d: date, names: dict | None = None) -> dict[str, Bar]:
        """d 日的不复权 Bar；缺失的数值记为 NaN（撮合会按停牌处理）。"""
        def num(x):
            return float("nan") if x is None else float(x)

        out: dict[str, Bar] = {}
        for code in codes:
            r = self.conn.execute("SELECT open, high, low, close, volume, amount, prev_close FROM bar_raw "
                                  "WHERE code=? AND date=?", (code, d.isoformat())).fetchone()
            if r:
                o, h, lo, c, v, a, pc = (num(x) for x in r)
                out[code] = Bar(code=code, date=d, open=o, high=h, low=lo, close=c, prev_close=pc, volume=v,
                                amount=a, name=(names or {}).get(code, ""))
        return out

    def last_raw_close(self, code: str, d: date) -> tuple[date, float] | None:
        """d 日（含）之前最近一个有效的不复权收盘价。"""
        r = self.conn.execute("SELECT date, close FROM bar_raw WHERE code=? AND date<=? AND close IS NOT NULL "
                              "ORDER BY date DESC LIMIT 1", (code, d.isoformat())).fetchone()
        return (date.fromisoformat(r[0]), float(r[1])) if r else None

    def save_dividends(self, code: str, df: pd.DataFrame) -> None:
        """写入 AkClient.dividends 的结果（按除权日覆盖）。空表不清除已有记录：接口偶尔返回空表。"""
        rows = [(code, pd.Timestamp(r.ex_date).date().isoformat(), float(r.cash), float(r.ratio))
                for r in df.itertuples(index=False)]
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO dividends VALUES (?,?,?,?)", rows)

    def dividends(self, code: str) -> list[tuple[date, float, float]]:
        """[(除权除息日, 每股税前派息, 每股送转)]，按日期升序。"""
        rows = self.conn.execute("SELECT ex_date, cash, ratio FROM dividends WHERE code=? ORDER BY ex_date",
                                 (code,)).fetchall()
        return [(date.fromisoformat(d), float(c), float(r)) for d, c, r in rows]

    def save_index(self, symbol: str, series: pd.Series) -> None:
        rows = [(symbol, pd.Timestamp(d).date().isoformat(), float(v)) for d, v in series.items()]
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO index_daily VALUES (?,?,?)", rows)

    def load_index(self, symbol: str) -> pd.Series:
        df = pd.read_sql_query("SELECT date, close FROM index_daily WHERE symbol=? ORDER BY date",
                               self.conn, params=(symbol,))
        idx = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
        return pd.Series(df["close"].astype(float).to_numpy(), index=idx, name=symbol)
