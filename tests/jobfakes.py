"""jobs 测试共享假对象：不联网、不启动 claude.exe、不推送企业微信。"""
from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from config import Settings
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Bar

CODES = [f"6000{i:02d}" for i in range(1, 11)]
NAMES = {c: f"测试{c[-2:]}" for c in CODES}


def make_dates(n: int = 130, start: str = "2026-01-05") -> list[date]:
    return [ts.date() for ts in pd.bdate_range(start, periods=n)]


def next_saturday(d: date) -> date:
    return d + timedelta(days=(5 - d.weekday()) % 7 or 7)


def make_panel(codes: list[str], dates: list[date], seed: int = 7) -> dict[str, pd.DataFrame]:
    """确定性的合成日线：日涨跌 ≤3%，开盘贴近昨收，成交额约 3 亿，不触及涨跌停。"""
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    n = len(idx)
    panel = {}
    for i, code in enumerate(codes):
        rets = rng.normal(0.0004 * (i - len(codes) / 2), 0.012, n).clip(-0.03, 0.03)
        close = 10.0 * np.cumprod(1 + rets)
        prev = np.concatenate([[close[0]], close[:-1]])
        open_ = prev * (1 + rng.normal(0, 0.003, n).clip(-0.01, 0.01))
        high = np.maximum(open_, close) * 1.005
        low = np.minimum(open_, close) * 0.995
        vol = np.full(n, 3e7)
        panel[code] = pd.DataFrame(
            {"open": open_.round(2), "high": high.round(2), "low": low.round(2),
             "close": close.round(2), "volume": vol, "amount": vol * close,
             "prev_close": prev.round(2)},
            index=idx,
        )
    return panel


def make_index(dates: list[date]) -> pd.Series:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.Series(3500.0 * np.cumprod(np.full(len(idx), 1.001)), index=idx, name="close")


def make_settings(root: Path) -> Settings:
    """claude_exe 指向一个空文件：万一某个测试漏打桩 common.make_llm，真实 LLM 在启动
    子进程时立即报错，不会联网，也不会用到本机真实的 Claude Code。"""
    stub = root / "bin" / "claude.exe"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(b"")
    return Settings(anthropic_api_key="test-key", wecom_webhook_url="", root=root,
                    claude_exe=str(stub))


REPO_ROOT = Path(__file__).resolve().parent.parent


def prepare_root(tmp: Path, monkeypatch) -> Path:
    """临时项目根：复制仓库 playbook/*.md 提示词，并把 playbook 目录、git 提交指向临时根。"""
    import agents.playbook as pb

    (tmp / "playbook").mkdir(parents=True, exist_ok=True)
    for md in (REPO_ROOT / "playbook").glob("*.md"):
        shutil.copy(md, tmp / "playbook" / md.name)
    monkeypatch.setattr(pb, "PLAYBOOK_DIR", tmp / "playbook")
    monkeypatch.setattr(pb, "git_commit", lambda *a, **k: False)
    import agents.research as rs  # research 用 from-import 绑定了 git_commit，要单独打桩
    monkeypatch.setattr(rs, "git_commit", lambda *a, **k: False)
    return tmp


class FakeClient:
    """AkClient 替身。current 指定"今天"，spot() 按该日开盘价返回竞价/盘中快照。"""

    def __init__(self, dates, panel, names, index):
        self.dates, self.panel, self.name_map, self.index = list(dates), panel, names, index
        self.current: date | None = None
        self.calls: dict[str, int] = {}
        self.spot_df: pd.DataFrame | None = None  # 若设置则 spot() 直接返回它

    def _hit(self, k):
        self.calls[k] = self.calls.get(k, 0) + 1

    def trade_calendar(self):
        self._hit("trade_calendar")
        return list(self.dates)

    def universe(self):
        return list(self.panel)

    def index_daily(self, symbol="sh000300"):
        return self.index.copy()

    def names(self):
        self._hit("names")
        return dict(self.name_map)

    def spot(self, codes=None, sink=None):
        self._hit("spot")
        if self.spot_df is not None:
            df = self.spot_df.copy()
            return df if codes is None else df[df["code"].isin(list(codes))].reset_index(drop=True)
        ts = pd.Timestamp(self.current)
        rows = []
        for c, df in self.panel.items():
            if (codes is None or c in codes) and ts in df.index:
                r = df.loc[ts]
                rows.append({"code": c, "name": self.name_map[c], "open": r["open"],
                             "prev_close": r["prev_close"], "last": r["open"],
                             "pct": (r["open"] / r["prev_close"] - 1) * 100,
                             "volume": r["volume"] / 50, "amount": r["amount"] / 50,
                             "high": r["open"]})
        return pd.DataFrame(rows)

    def news_global(self, limit=50):
        return [{"title": "测试新闻", "content": "市场平稳", "time": "08:00:00"}]

    def stock_news(self, code, limit=5):
        return []

    def notices(self, d):
        return pd.DataFrame(columns=["code", "name", "title", "type"])

    def dividends(self, code):
        """dividend_data[code] = [(除权日, 每股派息, 每股送转)]；"FAIL" 模拟接口故障。"""
        self._hit("dividends")
        rows = getattr(self, "dividend_data", {}).get(code, [])
        if rows == "FAIL":
            raise ConnectionError("新浪分红接口不可用")
        return pd.DataFrame(rows, columns=["ex_date", "cash", "ratio"])


class FakeCache:
    """BarCache 替身：内存面板，按日期切片。"""

    def __init__(self, panel, names, index):
        self.panel, self.names, self.index = panel, dict(names), index
        self.updates: list[date] = []
        self.update_codes: list[list[str]] = []
        self.saved_index: dict[str, pd.Series] = {}
        self.divs: dict[str, dict] = {}
        # 不复权日线：构造时复制一份；测试改 panel 模拟前复权重拉，改 raw 模拟真实成交价
        self.raw = {c: df.copy() for c, df in panel.items()}
        self.raw_updates: list[tuple[list[str], date]] = []
        self.raw_fail: set[str] = set()

    def update_raw(self, codes, end, start_default):
        self.raw_updates.append((sorted(codes), end))
        return sorted(set(codes) & self.raw_fail)

    def raw_bars(self, codes, d, names=None):
        ts, out = pd.Timestamp(d), {}
        for c in codes:
            df = self.raw.get(c)
            if c not in self.raw_fail and df is not None and ts in df.index:
                r = df.loc[ts]
                out[c] = Bar(code=c, date=d, open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                             close=float(r["close"]), prev_close=float(r["prev_close"]), volume=float(r["volume"]),
                             amount=float(r["amount"]), name=(names or {}).get(c, ""))
        return out

    def last_raw_close(self, code, d):
        df = self.raw.get(code)
        if df is None:
            return None
        closes = df.loc[:pd.Timestamp(d), "close"].dropna()
        return (closes.index[-1].date(), float(closes.iloc[-1])) if len(closes) else None

    def save_dividends(self, code, df):
        self.divs.setdefault(code, {}).update(
            {pd.Timestamp(r.ex_date).date(): (float(r.cash), float(r.ratio)) for r in df.itertuples(index=False)})

    def dividends(self, code):
        return [(d, c, r) for d, (c, r) in sorted(self.divs.get(code, {}).items())]

    def update(self, codes, end, start_default=date(2015, 6, 1)):
        self.updates.append(end)
        self.update_codes.append(sorted(codes))
        return []

    def set_names(self, names):
        self.names.update(names)

    def load_panel(self, codes, start, end):
        a, b = pd.Timestamp(start), pd.Timestamp(end)
        panel = {c: self.panel[c].loc[a:b].copy() for c in codes if c in self.panel}
        return panel, {c: self.names.get(c, "") for c in panel}

    def save_index(self, symbol, series):
        self.saved_index[symbol] = series

    def load_index(self, symbol):
        return self.saved_index.get(symbol, self.index).copy()


class FakeLLM:
    """agents.llm.LLM 替身：parse 同签名，按 schema 分派；不联网、不启动 claude.exe、不计费。
    fail = {purpose 前缀: 异常}：命中的调用先记录再抛出（模拟中转失效、超时、拒答）。"""

    def __init__(self, store: Store | None = None, fail: dict | None = None):
        self.store = store
        self.day: date | None = None  # 当天交易日：PremarketPlan/AuctionCheck 覆盖这天的全部候选
        self.fail = dict(fail or {})
        self.calls: list[dict] = []

    @property
    def purposes(self) -> list[str]:
        return [c["purpose"] for c in self.calls]

    def check_budget(self, essential):
        return None

    def month_cost(self) -> float:
        return 0.0

    def _candidates(self) -> list:
        loaded = self.store.load_candidates(self.day) if self.store is not None and self.day else None
        return loaded[0] if loaded else []

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0,
              max_budget_usd=1.0, essential=False):
        from agents.premarket import AuctionCheck, AuctionDecision, PlannedPick, PremarketPlan
        from agents.research import ResearchRound
        from agents.review import Lesson, ReviewOutput

        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema,
                               effort=effort, timeout=timeout, max_budget_usd=max_budget_usd,
                               essential=essential))
        for prefix, exc in self.fail.items():
            if purpose.startswith(prefix):
                raise exc
        if schema is PremarketPlan:
            return PremarketPlan(market_view="震荡", picks=[PlannedPick(
                code=p.code, action="keep", confidence=round(1.0 - i * 0.05, 2),
                entry_max=p.entry_max, stop=p.stop, target=p.target, min_gap_pct=-5.0,
                max_gap_pct=5.0, reason="测试保留：竞价跳空在 ±5% 内可买")
                for i, p in enumerate(self._candidates())])
        if schema is AuctionCheck:
            return AuctionCheck(note="竞价符合盘前计划", picks=[
                AuctionDecision(code=p.code, action="keep", reason="测试确认")
                for p in self._candidates()])
        if schema is ReviewOutput:
            return ReviewOutput(summary="复盘：测试摘要", lessons=[Lesson(
                action="add", rule_id="", text="高开超过 3% 的候选放弃",
                evidence=f"{purpose} 测试证据 600001 高开回落")])
        if schema is ResearchRound:
            return ResearchRound(analysis="测试：本轮不提交新策略", drafts=[], done=True)
        raise AssertionError(f"unexpected schema {schema}")


def install_context(monkeypatch, settings, store, client, cache, calendar, notifier):
    """让所有 jobs.* 入口的 common.context() 返回这些假依赖。"""
    import jobs.common as common

    monkeypatch.setattr(common, "context",
                        lambda: (settings, store, client, cache, calendar, notifier))


def new_store() -> Store:
    return Store(":memory:")


def cal(dates) -> TradingCalendar:
    return TradingCalendar(dates)
