"""SQLite 持久化：账户、净值、推荐、任务状态、LLM 用量、试验记录、策略状态、KV。"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from engine.ledger import Account
from engine.types import Pick

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nav (account TEXT, d TEXT, equity REAL, PRIMARY KEY (account, d));
CREATE TABLE IF NOT EXISTS recommendations (d TEXT, account TEXT, picks TEXT, meta TEXT, PRIMARY KEY (d, account));
CREATE TABLE IF NOT EXISTS candidates (d TEXT PRIMARY KEY, picks TEXT, context TEXT);
CREATE TABLE IF NOT EXISTS jobs (job TEXT, d TEXT, status TEXT, ts TEXT, PRIMARY KEY (job, d));
CREATE TABLE IF NOT EXISTS llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, purpose TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cost_cny REAL);
CREATE TABLE IF NOT EXISTS trials (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, strategy TEXT, params TEXT,
    train_sharpe REAL, val_sharpe REAL, gate_pass INTEGER, holdout_pass INTEGER);
CREATE TABLE IF NOT EXISTS strategy_status (strategy TEXT PRIMARY KEY, status TEXT, since TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""
STATUSES = {"candidate", "shadow", "incumbent", "retired"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class Store:
    def __init__(self, db_path: str | Path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # run_final 两个线程共用一个 Store：允许跨线程 + 锁串行化
        self.conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock, self.conn:
            self.conn.execute(sql, args)

    def _one(self, sql: str, args: tuple = ()):
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    # ---- accounts ----
    def save_account(self, acc: Account) -> None:
        self._exec("INSERT OR REPLACE INTO accounts VALUES (?, ?)", (acc.name, _dump(acc.to_dict())))

    def load_account(self, name: str) -> Account | None:
        row = self._one("SELECT data FROM accounts WHERE name=?", (name,))
        return Account.from_dict(json.loads(row[0])) if row else None

    def list_accounts(self) -> list[str]:
        return [r[0] for r in self._all("SELECT name FROM accounts ORDER BY name")]

    # ---- nav ----
    def record_nav(self, account: str, d: date, equity: float) -> None:
        equity = float(equity)
        if not math.isfinite(equity):  # SQLite 会把 NaN 存成 NULL，之后 nav_series 永远读不出来
            raise ValueError(f"{account} {d}: equity must be finite, got {equity!r}")
        self._exec("INSERT OR REPLACE INTO nav VALUES (?, ?, ?)", (account, d.isoformat(), equity))

    def nav_series(self, account: str) -> pd.Series:
        rows = self._all("SELECT d, equity FROM nav WHERE account=? ORDER BY d", (account,))
        idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
        return pd.Series([float(r[1]) for r in rows], index=idx, dtype=float, name=account)

    def last_nav_date(self, account: str) -> date | None:
        row = self._one("SELECT MAX(d) FROM nav WHERE account=?", (account,))
        return date.fromisoformat(row[0]) if row and row[0] else None

    # ---- recommendations / candidates ----
    def save_recommendation(self, d: date, account: str, picks: list[Pick], meta: dict | None = None) -> None:
        self._exec("INSERT OR REPLACE INTO recommendations VALUES (?, ?, ?, ?)",
                   (d.isoformat(), account, _dump([p.to_dict() for p in picks]), _dump(meta or {})))

    def load_recommendation(self, d: date, account: str) -> tuple[list[Pick], dict] | None:
        row = self._one("SELECT picks, meta FROM recommendations WHERE d=? AND account=?", (d.isoformat(), account))
        return ([Pick.from_dict(x) for x in json.loads(row[0])], json.loads(row[1])) if row else None

    def save_candidates(self, d: date, picks: list[Pick], context: dict) -> None:
        self._exec("INSERT OR REPLACE INTO candidates VALUES (?, ?, ?)",
                   (d.isoformat(), _dump([p.to_dict() for p in picks]), _dump(context)))

    def load_candidates(self, d: date) -> tuple[list[Pick], dict] | None:
        row = self._one("SELECT picks, context FROM candidates WHERE d=?", (d.isoformat(),))
        return ([Pick.from_dict(x) for x in json.loads(row[0])], json.loads(row[1])) if row else None

    # ---- jobs ----
    def mark_job(self, job: str, d: date, status: str) -> None:
        self._exec("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)", (job, d.isoformat(), status, _now()))

    def job_done(self, job: str, d: date) -> bool:
        row = self._one("SELECT status FROM jobs WHERE job=? AND d=?", (job, d.isoformat()))
        return bool(row and row[0] == "ok")

    # ---- llm usage ----
    def add_llm_usage(self, purpose: str, input_tokens: int, output_tokens: int, cost_cny: float) -> None:
        self._exec("INSERT INTO llm_usage (ts, purpose, input_tokens, output_tokens, cost_cny) VALUES (?, ?, ?, ?, ?)",
                   (_now(), purpose, int(input_tokens), int(output_tokens), float(cost_cny)))

    def month_llm_cost(self, year: int, month: int) -> float:
        row = self._one("SELECT COALESCE(SUM(cost_cny), 0) FROM llm_usage WHERE substr(ts, 1, 7)=?",
                        (f"{year:04d}-{month:02d}",))
        return float(row[0])

    # ---- trials ----
    def add_trial(self, strategy: str, params: dict, train_sharpe: float | None, val_sharpe: float | None,
                  gate_pass: bool, holdout_pass: bool | None) -> int:
        """记录一次试验，返回其 id。回测中途失败时夏普可以为 None。"""
        def num(x):
            return None if x is None or not math.isfinite(float(x)) else float(x)

        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO trials (ts, strategy, params, train_sharpe, val_sharpe, gate_pass, holdout_pass) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), strategy, _dump(params), num(train_sharpe), num(val_sharpe), int(bool(gate_pass)),
                 None if holdout_pass is None else int(bool(holdout_pass))))
            return int(cur.lastrowid)

    def set_trial_holdout(self, trial_id: int, holdout_pass: bool) -> None:
        self._exec("UPDATE trials SET holdout_pass=? WHERE id=?", (int(bool(holdout_pass)), int(trial_id)))

    def void_trial(self, trial_id: int) -> None:
        """作废一次试验（评估规则在评估期间被改动，或研究会话中 engine/ 哈希校验不符）：仍计入 n_trials，
        但门槛和留出期都记为未通过。"""
        self._exec("UPDATE trials SET gate_pass=0, holdout_pass=0 WHERE id=?", (int(trial_id),))

    def trial_count(self, strategy: str | None = None) -> int:
        if strategy is None:
            return int(self._one("SELECT COUNT(*) FROM trials")[0])
        return int(self._one("SELECT COUNT(*) FROM trials WHERE strategy=?", (strategy,))[0])

    _TRIAL_COLS = "id, ts, strategy, params, train_sharpe, val_sharpe, gate_pass, holdout_pass"

    @staticmethod
    def _trial(r) -> dict:
        return {"id": r[0], "ts": r[1], "strategy": r[2], "params": json.loads(r[3]), "train_sharpe": r[4],
                "val_sharpe": r[5], "gate_pass": bool(r[6]), "holdout_pass": None if r[7] is None else bool(r[7])}

    def recent_trials(self, n: int = 20) -> list[dict]:
        rows = self._all(f"SELECT {self._TRIAL_COLS} FROM trials ORDER BY id DESC LIMIT ?", (n,))
        return [self._trial(r) for r in rows]

    def last_trial(self, strategy: str) -> dict | None:
        row = self._one(f"SELECT {self._TRIAL_COLS} FROM trials WHERE strategy=? ORDER BY id DESC LIMIT 1",
                        (strategy,))
        return self._trial(row) if row else None

    # ---- strategy status ----
    def set_status(self, strategy: str, status: str, d: date) -> None:
        if status not in STATUSES:
            raise ValueError(f"bad status: {status}")
        self._exec("INSERT OR REPLACE INTO strategy_status VALUES (?, ?, ?)", (strategy, status, d.isoformat()))

    def get_status(self, strategy: str) -> str | None:
        row = self._one("SELECT status FROM strategy_status WHERE strategy=?", (strategy,))
        return row[0] if row else None

    def strategies_with_status(self, status: str) -> list[str]:
        return [r[0] for r in self._all(
            "SELECT strategy FROM strategy_status WHERE status=? ORDER BY strategy", (status,))]

    def status_since(self, strategy: str) -> date | None:
        row = self._one("SELECT since FROM strategy_status WHERE strategy=?", (strategy,))
        return date.fromisoformat(row[0]) if row else None

    # ---- kv ----
    def kv_get(self, key: str, default=None):
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row[0]) if row else default

    def kv_set(self, key: str, value) -> None:
        self._exec("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, _dump(value)))
