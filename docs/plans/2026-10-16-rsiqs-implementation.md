# RSIQS 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** 构建 A 股盘前推荐 + 模拟交易 + 递归自我提升系统：每个交易日 9:28 前经企业微信推送 ≤5 只推荐，模拟撮合结算，并由 Claude 迭代量化策略与 LLM playbook。

**Architecture:** 混合两层。量化层（`strategies/`，可回测，研究循环迭代）出候选池；LLM 层（`agents/premarket.py` + `playbook/`，仅前向验证）结合新闻、公告、竞价做否决与调权。`engine/` 为锁定区（撮合、账本、评估器、留出集），哈希校验；多个影子账户并行模拟，晋升规则以数据决定现任。设计见 `docs/plans/2026-10-16-rsiqs-design.md`。

**Tech Stack:** Python 3.12 + uv；akshare 1.19.1、pandas 3.0.6、numpy 2.5.4、anthropic 1.15.0（`claude-opus-5-5`，结构化输出，`fallbacks: "default"`）、requests、pydantic、sqlite3；pytest 9.1.1；Windows 任务计划。

**执行顺序：** Part 1 → 2 → 3 → 4 → 5 → 6，各 Part 内按 Task 编号。测试一律不访问网络与真实 API；需要真实环境的验证均为显式的 "Manual smoke step"。

**交叉审查：** 全部代码块已在临时工程中合并运行，280 个测试通过（含修复：最低佣金导致透支、两处字符串转义）。

---

## Part 1: 工程脚手架与撮合核心

> 契约歧义说明：(1) `TradingCalendar.offset` 越过日历两端时夹到首/末交易日而不报错，保证回测末段买入仍有合法 exit_date（d 非交易日仍抛 ValueError）。(2) `Fill` 额外提供 `to_dict/from_dict` 供 `Account` 持久化。(3) `Account.buy` 对已持仓代码做加仓并摊薄成本（SimBroker 从不加仓，此为最简一致行为）。(4) 滑点价不做四舍五入，保持浮点。

本部分在 `e:\workspace\rsiqs` 下建立 uv 工程与 `engine/` 撮合核心（types / rules / calendar / ledger / broker），全部纯函数式可测，无网络依赖。所有代码已在隔离环境用 pytest 9.1.1 实测通过（57 个用例）。

### Task 1.1: 工程脚手架（uv + config）

**Files:**
- Create: `pyproject.toml`, `.env.example`, `.gitignore`, `config.py`
- Create: `engine/__init__.py`, `data/__init__.py`, `notify/__init__.py`, `strategies/__init__.py`, `agents/__init__.py`, `jobs/__init__.py`（均为空文件）
- Test: `tests/test_config.py`

**Step 0: 写 pyproject.toml 并安装依赖**

`pyproject.toml`（`package = false`：项目以根目录模块方式运行，不打包）：

```toml
[project]
name = "rsiqs"
version = "0.1.0"
description = "A-share pre-open recommendation, simulated trading and recursive self-improvement"
requires-python = ">=3.12,<3.13"
dependencies = [
    "akshare==1.19.1",
    "pandas==3.0.6",
    "numpy==2.5.4",
    "anthropic==1.15.0",
    "requests==2.34.2",
    "python-dotenv==1.2.4",
    "pydantic==2.14.1",
]

[dependency-groups]
dev = ["pytest==9.1.1"]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`.env.example`（只放占位符，绝不写真实 webhook key）：

```ini
# Copy to .env and fill in real values. Never commit .env.
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR-KEY-HERE
MONTHLY_BUDGET_CNY=300
USD_CNY=7.2
DB_PATH=data/rsiqs.sqlite
LLM_MODEL=claude-opus-5-5
CAPITAL=500000
MAX_PICKS=5
```

`.gitignore`：

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
data/*.sqlite
logs/
```

创建包目录与空 `__init__.py`，然后同步依赖：

```powershell
foreach ($p in 'engine','data','notify','strategies','agents','jobs','tests') { New-Item -ItemType Directory -Force $p | Out-Null }
foreach ($p in 'engine','data','notify','strategies','agents','jobs') { if (-not (Test-Path "$p/__init__.py")) { New-Item -ItemType File "$p/__init__.py" | Out-Null } }
uv sync
```
预期：生成 `uv.lock` 与 `.venv`，`uv run python -c "import pandas, akshare, anthropic; print(pandas.__version__)"` 输出 `3.0.6`。


**Step 1: 写失败测试** `tests/test_config.py`（monkeypatch 掉 `load_dotenv`，避免读到本机真实 `.env`）

```python
import config

ENV_NAMES = ["ANTHROPIC_API_KEY", "WECOM_WEBHOOK_URL", "MONTHLY_BUDGET_CNY", "USD_CNY",
             "DB_PATH", "LLM_MODEL", "CAPITAL", "MAX_PICKS"]


def _isolate(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)


def test_defaults(monkeypatch):
    _isolate(monkeypatch)
    s = config.get_settings()
    assert s.anthropic_api_key == "" and s.wecom_webhook_url == ""
    assert s.monthly_budget_cny == 300.0 and s.usd_cny == 7.2
    assert s.db_path == "data/rsiqs.sqlite" and s.llm_model == "claude-opus-5-5"
    assert s.capital == 500_000.0 and s.max_picks == 5
    assert (s.root / "config.py").exists()


def test_env_overrides(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv("MONTHLY_BUDGET_CNY", "150")
    monkeypatch.setenv("USD_CNY", "7.0")
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("CAPITAL", "1000000")
    monkeypatch.setenv("MAX_PICKS", "3")
    s = config.get_settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.wecom_webhook_url == "https://example.invalid/hook"
    assert s.monthly_budget_cny == 150.0 and s.usd_cny == 7.0
    assert s.db_path == ":memory:" and s.capital == 1_000_000.0 and s.max_picks == 3


def test_pricing_has_default_model():
    assert config.PRICING_USD_PER_MTOK["claude-opus-5-5"] == (4.0, 20.0)
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_config.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'config'`。


**Step 3: 最小实现** `config.py`

```python
"""Runtime settings loaded from .env / environment."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

# (input, output) USD per million tokens
PRICING_USD_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0)}


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    wecom_webhook_url: str
    monthly_budget_cny: float = 300.0
    usd_cny: float = 7.2
    db_path: str = "data/rsiqs.sqlite"
    llm_model: str = "claude-opus-5-5"
    capital: float = 500_000.0
    max_picks: int = 5
    root: Path = field(default=ROOT)


def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    env = os.environ.get
    return Settings(
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        wecom_webhook_url=env("WECOM_WEBHOOK_URL", ""),
        monthly_budget_cny=float(env("MONTHLY_BUDGET_CNY", "300")),
        usd_cny=float(env("USD_CNY", "7.2")),
        db_path=env("DB_PATH", "data/rsiqs.sqlite"),
        llm_model=env("LLM_MODEL", "claude-opus-5-5"),
        capital=float(env("CAPITAL", "500000")),
        max_picks=int(env("MAX_PICKS", "5")),
        root=ROOT,
    )
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_config.py -v
```
预期：3 passed。


**Step 5: Commit**

```powershell
git add pyproject.toml uv.lock .env.example .gitignore config.py engine/__init__.py data/__init__.py notify/__init__.py strategies/__init__.py agents/__init__.py jobs/__init__.py tests/test_config.py
git commit -m "chore: scaffold uv project and settings"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`）

### Task 1.2: engine/types.py

**Files:**
- Create: `engine/types.py`
- Test: `tests/test_types.py`

**Step 1: 写失败测试** `tests/test_types.py`

```python
import dataclasses
from datetime import date

import pytest

from engine.types import Bar, Fill, Pick


def test_pick_roundtrip():
    p = Pick("600000", "浦发银行", 1.5, 10.3, 9.3, 11.0, 5, "动量")
    assert Pick.from_dict(p.to_dict()) == p


def test_pick_from_dict_default_reason():
    d = {"code": "000001", "name": "平安银行", "score": 0.2, "entry_max": 12.0,
         "stop": 11.0, "target": 13.0, "hold_days": 3}
    assert Pick.from_dict(d).reason == ""


def test_fill_roundtrip():
    f = Fill(date(2026, 10, 16), "600000", "BUY", 1000, 10.01, 5.1, "买入")
    d = f.to_dict()
    assert d["date"] == "2026-10-16"
    assert Fill.from_dict(d) == f


def test_bar_is_frozen():
    b = Bar("600000", date(2026, 10, 16), 10, 11, 9.5, 10.5, 10, 1e6, 1e7)
    assert b.name == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.open = 1.0
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_types.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.types'`。


**Step 3: 最小实现** `engine/types.py`

```python
"""Core value types shared across the engine."""
import datetime
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class Bar:
    code: str
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: float
    amount: float
    name: str = ""


@dataclass(frozen=True)
class Pick:
    code: str
    name: str
    score: float
    entry_max: float
    stop: float
    target: float
    hold_days: int
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Pick":
        return cls(**{f.name: d[f.name] for f in fields(cls) if f.name in d})


@dataclass(frozen=True)
class Fill:
    date: datetime.date
    code: str
    side: str  # "BUY" | "SELL"
    qty: int
    price: float
    fee: float
    reason: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fill":
        return cls(date=datetime.date.fromisoformat(d["date"]), code=d["code"], side=d["side"],
                   qty=int(d["qty"]), price=float(d["price"]), fee=float(d["fee"]), reason=d["reason"])
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_types.py -v
```
预期：4 passed。


**Step 5: Commit**

```powershell
git add engine/types.py tests/test_types.py
git commit -m "feat(engine): add Bar, Pick, Fill value types"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`）

### Task 1.3: engine/rules.py（板块、涨跌停、整手、费用）

**Files:**
- Create: `engine/rules.py`
- Test: `tests/test_rules.py`

要点：涨跌停价用 `Decimal(str(prev_close))` + `ROUND_HALF_UP`。例：10.05×1.1=11.055→**11.06**，10.05×0.9=9.045→**9.05**；直接用 float 会得到 11.05/9.04（错误）。ST 只对主板生效（创业板/科创板 ST 仍为 20%）。整手统一按 100 股向下取整（科创板 200 股起买未建模，已在 docstring 注明）。

**Step 1: 写失败测试** `tests/test_rules.py`

```python
import pytest

from engine.rules import Board, board, calc_fee, is_st, limit_pct, limit_prices, round_lot


@pytest.mark.parametrize("code,expected", [
    ("600000", Board.MAIN), ("000001", Board.MAIN), ("002594", Board.MAIN),
    ("300750", Board.CHINEXT), ("301269", Board.CHINEXT),
    ("688981", Board.STAR), ("689009", Board.STAR),
    ("830799", Board.BSE), ("430047", Board.BSE), ("920001", Board.BSE),
])
def test_board(code, expected):
    assert board(code) is expected


def test_is_st():
    assert is_st("*ST康美") and is_st("ST海越") and is_st("st测试")
    assert not is_st("平安银行")


@pytest.mark.parametrize("code,name,pct", [
    ("600000", "浦发银行", 0.10), ("600000", "*ST浦发", 0.05),
    ("300750", "宁德时代", 0.20), ("300750", "ST宁德", 0.20),
    ("688981", "中芯国际", 0.20), ("830799", "艾融软件", 0.30),
])
def test_limit_pct(code, name, pct):
    assert limit_pct(code, name) == pct


def test_limit_prices_half_up():
    # 10.05*1.1 = 11.055 -> 11.06 ; 10.05*0.9 = 9.045 -> 9.05 (float math would give 11.05/9.04)
    assert limit_prices("600000", "浦发银行", 10.05) == (11.06, 9.05)


def test_limit_prices_boards():
    assert limit_prices("300750", "宁德时代", 10.0) == (12.0, 8.0)
    assert limit_prices("600000", "ST浦发", 3.33) == (3.50, 3.16)  # 3.4965->3.50, 3.1635->3.16
    assert limit_prices("830799", "", 10.0) == (13.0, 7.0)


def test_round_lot():
    assert round_lot(1999.9) == 1900
    assert round_lot(100) == 100
    assert round_lot(99.9) == 0
    assert round_lot(-5) == 0


def test_fee_min_commission_buy():
    # 10000*0.00025 = 2.5 -> min 5 ; transfer 0.1
    assert calc_fee("BUY", 10_000) == 5.10


def test_fee_buy_large_no_stamp():
    # 100000: commission 25 + transfer 1
    assert calc_fee("BUY", 100_000) == 26.0


def test_fee_sell_has_stamp():
    # 100000: 25 + 1 + 50
    assert calc_fee("SELL", 100_000) == 76.0
    assert calc_fee("SELL", 10_000) == 10.10
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_rules.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.rules'`。


**Step 3: 最小实现** `engine/rules.py`

```python
"""A-share trading rules: boards, price limits, lots, fees."""
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Board(Enum):
    MAIN = "main"
    CHINEXT = "chinext"
    STAR = "star"
    BSE = "bse"


def board(code: str) -> Board:
    if code.startswith(("300", "301")):
        return Board.CHINEXT
    if code.startswith(("688", "689")):
        return Board.STAR
    if code.startswith(("8", "4", "92")):
        return Board.BSE
    return Board.MAIN


def is_st(name: str) -> bool:
    return "ST" in name.upper()


def limit_pct(code: str, name: str = "") -> float:
    b = board(code)
    if b is Board.BSE:
        return 0.30
    if b in (Board.CHINEXT, Board.STAR):
        return 0.20
    return 0.05 if is_st(name) else 0.10


def limit_prices(code: str, name: str, prev_close: float) -> tuple[float, float]:
    """(limit_up, limit_down) rounded half-up to 0.01, computed in Decimal to avoid float drift."""
    pc = Decimal(str(prev_close))
    pct = Decimal(str(limit_pct(code, name)))
    cent = Decimal("0.01")
    up = (pc * (1 + pct)).quantize(cent, rounding=ROUND_HALF_UP)
    down = (pc * (1 - pct)).quantize(cent, rounding=ROUND_HALF_UP)
    return float(up), float(down)


def round_lot(qty: float) -> int:
    """Floor to a multiple of 100 shares. Simplification: STAR's 200-share minimum
    and 1-share increments are not modelled; all boards use 100-share lots."""
    if qty <= 0:
        return 0
    return int(qty // 100) * 100


COMMISSION_RATE = 0.00025
COMMISSION_MIN = 5.0
TRANSFER_RATE = 0.00001
STAMP_RATE = 0.0005


def calc_fee(side: str, amount: float) -> float:
    fee = max(amount * COMMISSION_RATE, COMMISSION_MIN) + amount * TRANSFER_RATE
    if side == "SELL":
        fee += amount * STAMP_RATE
    return round(fee, 2)
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_rules.py -v
```
预期：23 passed。


**Step 5: Commit**

```powershell
git add engine/rules.py tests/test_rules.py
git commit -m "feat(engine): add A-share board, limit price and fee rules"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`）

### Task 1.4: engine/calendar.py

**Files:**
- Create: `engine/calendar.py`
- Test: `tests/test_calendar.py`

**Step 1: 写失败测试** `tests/test_calendar.py`

```python
from datetime import date

import pytest

from engine.calendar import TradingCalendar

# 2026-10-01..07 National Day holiday; 10-10/11 weekend
DAYS = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8), date(2026, 10, 9),
        date(2026, 10, 12), date(2026, 10, 13)]


@pytest.fixture
def cal():
    return TradingCalendar(reversed(DAYS + [DAYS[0]]))  # unsorted + duplicate


def test_is_trading_day(cal):
    assert cal.is_trading_day(date(2026, 10, 8))
    assert not cal.is_trading_day(date(2026, 10, 1))


def test_next_prev(cal):
    assert cal.next_day(date(2026, 9, 30)) == date(2026, 10, 8)
    assert cal.next_day(date(2026, 10, 3)) == date(2026, 10, 8)
    assert cal.prev_day(date(2026, 10, 8)) == date(2026, 9, 30)
    assert cal.prev_day(date(2026, 10, 11)) == date(2026, 10, 9)
    with pytest.raises(ValueError):
        cal.next_day(date(2026, 10, 13))
    with pytest.raises(ValueError):
        cal.prev_day(date(2026, 9, 29))


def test_between_inclusive(cal):
    assert cal.between(date(2026, 9, 30), date(2026, 10, 9)) == DAYS[1:4]
    assert cal.between(date(2026, 10, 1), date(2026, 10, 7)) == []


def test_offset_and_last(cal):
    assert cal.offset(date(2026, 9, 30), 2) == date(2026, 10, 9)
    assert cal.offset(date(2026, 10, 9), 0) == date(2026, 10, 9)
    assert cal.offset(date(2026, 10, 9), -1) == date(2026, 10, 8)
    with pytest.raises(ValueError):
        cal.offset(date(2026, 10, 1), 1)
    assert cal.offset(date(2026, 10, 12), 5) == date(2026, 10, 13)  # clamped to last()
    assert cal.last() == date(2026, 10, 13)
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_calendar.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.calendar'`。


**Step 3: 最小实现** `engine/calendar.py`

```python
"""Trading calendar over a fixed set of trading dates."""
from bisect import bisect_left, bisect_right
from datetime import date
from typing import Iterable


class TradingCalendar:
    def __init__(self, dates: Iterable[date]):
        self._days = sorted(set(dates))
        self._set = set(self._days)
        if not self._days:
            raise ValueError("empty trading calendar")

    def is_trading_day(self, d: date) -> bool:
        return d in self._set

    def next_day(self, d: date) -> date:
        i = bisect_right(self._days, d)
        if i >= len(self._days):
            raise ValueError(f"no trading day after {d}")
        return self._days[i]

    def prev_day(self, d: date) -> date:
        i = bisect_left(self._days, d)
        if i == 0:
            raise ValueError(f"no trading day before {d}")
        return self._days[i - 1]

    def between(self, a: date, b: date) -> list[date]:
        return self._days[bisect_left(self._days, a):bisect_right(self._days, b)]

    def offset(self, d: date, n: int) -> date:
        if d not in self._set:
            raise ValueError(f"{d} is not a trading day")
        # Clamped to the calendar ends so a hold period running past the data end
        # (e.g. last days of a backtest) still yields a valid exit date.
        i = min(max(self._days.index(d) + n, 0), len(self._days) - 1)
        return self._days[i]

    def last(self) -> date:
        return self._days[-1]
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_calendar.py -v
```
预期：4 passed。


**Step 5: Commit**

```powershell
git add engine/calendar.py tests/test_calendar.py
git commit -m "feat(engine): add trading calendar"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`）

### Task 1.5: engine/ledger.py（账户、T+1、现金与费用）

**Files:**
- Create: `engine/ledger.py`
- Test: `tests/test_ledger.py`

`Position.cost` 为含买入费用的每股均价；`equity()` 对无报价的持仓按 cost 估值。

**Step 1: 写失败测试** `tests/test_ledger.py`

```python
import json
from datetime import date

import pytest

from engine.ledger import Account, InsufficientCash, NotAvailable

D1, D2, EXIT = date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 22)


def _bought():
    acc = Account("t", 100_000.0)
    acc.buy(D1, "600000", "浦发银行", 10.0, 1000, 9.3, 11.0, EXIT, "test")
    return acc


def test_buy_cash_and_position():
    acc = _bought()
    # amount 10000, fee max(2.5,5)+0.1 = 5.1
    assert acc.cash == pytest.approx(100_000 - 10_005.1)
    p = acc.positions["600000"]
    assert (p.qty, p.available, p.entry_date, p.exit_date) == (1000, 0, D1, EXIT)
    assert p.cost == pytest.approx(10.0051)
    assert acc.trades[-1].side == "BUY" and acc.trades[-1].fee == 5.1


def test_sell_same_day_raises_t_plus_1():
    acc = _bought()
    with pytest.raises(NotAvailable):
        acc.sell(D1, "600000", 10.5, 1000, "same day")


def test_sell_next_day_after_start_day():
    acc = _bought()
    acc.start_day(D2)
    assert acc.positions["600000"].available == 1000
    f = acc.sell(D2, "600000", 11.0, 1000, "止盈")
    # amount 11000: commission 5 + transfer 0.11 + stamp 5.5 = 10.61
    assert f.fee == 10.61
    assert acc.cash == pytest.approx(100_000 - 10_005.1 + 11_000 - 10.61)
    assert "600000" not in acc.positions


def test_partial_sell_keeps_position():
    acc = _bought()
    acc.start_day(D2)
    acc.sell(D2, "600000", 10.0, 400, "部分")
    p = acc.positions["600000"]
    assert (p.qty, p.available) == (600, 600)
    with pytest.raises(NotAvailable):
        acc.sell(D2, "600000", 10.0, 700, "超量")


def test_sell_unknown_code_raises():
    with pytest.raises(NotAvailable):
        Account("t", 1000.0).sell(D1, "000001", 10.0, 100, "x")


def test_insufficient_cash_and_bad_qty():
    acc = Account("t", 10_000.0)
    with pytest.raises(InsufficientCash):
        acc.buy(D1, "600000", "浦发银行", 10.0, 1000, 9, 11, EXIT)  # needs 10005.1
    assert acc.cash == 10_000.0 and not acc.positions
    with pytest.raises(ValueError):
        acc.buy(D1, "600000", "浦发银行", 10.0, 150, 9, 11, EXIT)


def test_equity_uses_price_or_cost():
    acc = _bought()
    assert acc.equity({"600000": 12.0}) == pytest.approx(acc.cash + 12_000)
    assert acc.equity({}) == pytest.approx(acc.cash + 10_005.1)


def test_roundtrip_json():
    acc = _bought()
    restored = Account.from_dict(json.loads(json.dumps(acc.to_dict())))
    assert restored == acc
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_ledger.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.ledger'`。


**Step 3: 最小实现** `engine/ledger.py`

```python
"""Simulated account ledger with A-share T+1 settlement."""
from dataclasses import asdict, dataclass, field
from datetime import date

from engine.rules import calc_fee
from engine.types import Fill


class InsufficientCash(Exception):
    pass


class NotAvailable(Exception):
    pass


@dataclass
class Position:
    code: str
    name: str
    qty: int
    available: int
    cost: float  # average cost per share, fees included
    entry_date: date
    stop: float
    target: float
    exit_date: date


@dataclass
class Account:
    name: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Fill] = field(default_factory=list)

    def start_day(self, d: date) -> None:
        """T+1 unlock: everything held before today becomes sellable."""
        for p in self.positions.values():
            p.available = p.qty

    def buy(self, d: date, code: str, name: str, price: float, qty: int, stop: float,
            target: float, exit_date: date, reason: str = "") -> Fill:
        if qty <= 0 or qty % 100:
            raise ValueError(f"qty must be a positive multiple of 100, got {qty}")
        amount = price * qty
        fee = calc_fee("BUY", amount)
        if amount + fee > self.cash + 1e-9:
            raise InsufficientCash(f"{self.name}: need {amount + fee:.2f}, have {self.cash:.2f}")
        self.cash -= amount + fee
        pos = self.positions.get(code)
        if pos is None:
            self.positions[code] = Position(code, name, qty, 0, (amount + fee) / qty, d,
                                            stop, target, exit_date)
        else:  # add-on: new shares locked until tomorrow, cost averaged
            pos.cost = (pos.cost * pos.qty + amount + fee) / (pos.qty + qty)
            pos.qty += qty
            pos.stop, pos.target, pos.exit_date = stop, target, exit_date
        fill = Fill(d, code, "BUY", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def sell(self, d: date, code: str, price: float, qty: int, reason: str) -> Fill:
        pos = self.positions.get(code)
        if pos is None or qty <= 0 or qty > pos.available:
            avail = pos.available if pos else 0
            raise NotAvailable(f"{self.name}: sell {code} x{qty}, available {avail}")
        amount = price * qty
        fee = calc_fee("SELL", amount)
        self.cash += amount - fee
        pos.qty -= qty
        pos.available -= qty
        if pos.qty == 0:
            del self.positions[code]
        fill = Fill(d, code, "SELL", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(c, p.cost) for c, p in self.positions.items())

    def to_dict(self) -> dict:
        positions = {}
        for c, p in self.positions.items():
            d = asdict(p)
            d["entry_date"] = p.entry_date.isoformat()
            d["exit_date"] = p.exit_date.isoformat()
            positions[c] = d
        return {"name": self.name, "cash": self.cash, "positions": positions,
                "trades": [t.to_dict() for t in self.trades]}

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        positions = {}
        for c, p in d.get("positions", {}).items():
            positions[c] = Position(
                code=p["code"], name=p["name"], qty=int(p["qty"]), available=int(p["available"]),
                cost=float(p["cost"]), entry_date=date.fromisoformat(p["entry_date"]),
                stop=float(p["stop"]), target=float(p["target"]),
                exit_date=date.fromisoformat(p["exit_date"]))
        return cls(name=d["name"], cash=float(d["cash"]), positions=positions,
                   trades=[Fill.from_dict(t) for t in d.get("trades", [])])
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_ledger.py -v
```
预期：8 passed。


**Step 5: Commit**

```powershell
git add engine/ledger.py tests/test_ledger.py
git commit -m "feat(engine): add T+1 account ledger"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`）

### Task 1.6: engine/broker.py（开盘撮合）

**Files:**
- Create: `engine/broker.py`
- Test: `tests/test_broker.py`

执行顺序严格按契约：`start_day` → 先卖（到期 > 止损 > 止盈；停牌/全天一字跌停跳过）→ 再买（按 picks 顺序；开盘涨停、高开超过 entry_max、停牌、不足一手均跳过）。当天买入的仓位 `available=0`，当日不会被卖出。卖出先于买入，释放的资金当天可用。

**Step 1: 写失败测试** `tests/test_broker.py`

```python
from datetime import date

import pytest

from engine.broker import SimBroker
from engine.calendar import TradingCalendar
from engine.ledger import Account
from engine.types import Bar, Pick

DAYS = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]
CAL = TradingCalendar(DAYS)
D0, D1, D2 = DAYS[0], DAYS[1], DAYS[2]
SLIP = 0.001  # 10 bps


def bar(code="600000", d=D1, o=10.0, h=10.5, l=9.8, c=10.2, pc=10.0, vol=1e6, name="浦发银行"):
    return Bar(code, d, o, h, l, c, pc, vol, vol * c, name)


def pick(code="600000", entry_max=10.5, stop=9.3, target=11.0, hold=5, name="浦发银行"):
    return Pick(code, name, 1.0, entry_max, stop, target, hold, "测试")


def held(stop=9.3, target=11.0, exit_date=DAYS[5]):
    """Account holding 1000 sh of 600000 bought on D0."""
    acc = Account("t", 100_000.0)
    acc.buy(D0, "600000", "浦发银行", 10.0, 1000, stop, target, exit_date, "seed")
    return acc


def run(acc, bars, picks=(), d=D1, cap=20_000.0):
    return SimBroker(10.0).execute_day(acc, d, {b.code: b for b in bars}, list(picks), CAL, cap)


def test_buy_at_open_with_slippage_and_lot_rounding():
    acc = Account("t", 100_000.0)
    fills = run(acc, [bar(o=10.0)], [pick()])
    assert len(fills) == 1
    f = fills[0]
    assert f.side == "BUY" and f.price == pytest.approx(10.01)
    assert f.qty == 1900  # 20000/10.01 = 1998.0 -> 1900
    p = acc.positions["600000"]
    assert p.available == 0 and p.exit_date == CAL.offset(D1, 5) == DAYS[6]


def test_limit_up_open_blocks_buy():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=11.0, h=11.0, l=11.0, c=11.0)], [pick(entry_max=12.0)]) == []
    assert not acc.positions


def test_open_above_entry_max_skipped():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.6)], [pick(entry_max=10.5)]) == []


def test_suspended_pick_skipped():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(vol=0)], [pick()]) == []
    assert run(acc, [], [pick()]) == []


def test_qty_below_one_lot_skipped():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.0)], [pick()], cap=900.0) == []


def test_cash_caps_quantity():
    acc = Account("t", 5_000.0)
    f = run(acc, [bar(o=10.0)], [pick()], cap=20_000.0)[0]
    assert f.qty == 400  # 4995/10.01 = 499 -> 400


def test_min_commission_does_not_overdraw_cash():
    acc = Account("t", 2_000.0)
    # 1_005 cash: 1004/10.01 -> 100 shares, but 1001 + 5 (min commission) > 1005 -> skip, not raise
    acc2 = Account("t2", 1_005.0)
    assert run(acc, [bar(o=10.0)], [pick()], cap=20_000.0)[0].qty == 100
    assert run(acc2, [bar(o=10.0)], [pick()], cap=20_000.0) == []
    assert acc2.cash == 1_005.0


def test_already_held_not_rebought():
    acc = held()
    fills = run(acc, [bar(o=10.0, h=10.3, l=9.9)], [pick()])
    assert fills == [] and acc.positions["600000"].qty == 1000


def test_stop_loss_gap_down_fills_at_open():
    acc = held(stop=9.3)
    f = run(acc, [bar(o=9.0, h=9.2, l=8.9, c=9.1)])[0]
    assert f.side == "SELL" and f.reason == "止损" and f.qty == 1000
    assert f.price == pytest.approx(9.0 * (1 - SLIP))
    assert "600000" not in acc.positions


def test_stop_loss_intraday_fills_at_stop():
    acc = held(stop=9.3)
    f = run(acc, [bar(o=9.8, h=9.9, l=9.2)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(9.3 * (1 - SLIP))


def test_take_profit():
    acc = held(target=11.0)
    f = run(acc, [bar(o=10.5, h=11.2, l=10.4)])[0]
    assert f.reason == "止盈" and f.price == pytest.approx(11.0 * (1 - SLIP))
    acc2 = held(target=11.0)
    f2 = run(acc2, [bar(o=11.05, h=11.1, l=10.8, pc=10.1)])[0]  # gap above target
    assert f2.reason == "止盈" and f2.price == pytest.approx(11.05 * (1 - SLIP))


def test_expiry_exit_on_exit_date():
    acc = held(exit_date=D2)
    assert run(acc, [bar(d=D1)], d=D1) == []  # quiet day before expiry
    f = run(acc, [bar(d=D2, o=10.1)], d=D2)[0]
    assert f.reason == "到期" and f.price == pytest.approx(10.1 * (1 - SLIP))


def test_all_day_limit_down_blocks_sell():
    acc = held(stop=9.3)
    assert run(acc, [bar(o=9.0, h=9.0, l=9.0, c=9.0, pc=10.0)]) == []
    assert acc.positions["600000"].qty == 1000


def test_suspended_position_not_sold():
    acc = held(exit_date=D1)
    assert run(acc, [bar(vol=0)]) == []
    assert run(acc, []) == []


def test_bought_today_not_exited_today():
    acc = Account("t", 100_000.0)
    # opens 10.0 (buy), then low 9.0 pierces stop 9.3 — but T+1 forbids selling today
    fills = run(acc, [bar(o=10.0, l=9.0)], [pick(stop=9.3)])
    assert [f.side for f in fills] == ["BUY"]
    assert acc.positions["600000"].qty == 1900


def test_exit_then_enter_same_day_uses_freed_cash():
    acc = held(exit_date=D1)
    fills = run(acc, [bar(), bar(code="000001", name="平安银行", o=12.0, h=12.3, l=11.9, pc=12.0)],
                [pick(code="000001", name="平安银行", entry_max=12.5, stop=11.0, target=13.5)])
    assert [(f.side, f.code) for f in fills] == [("SELL", "600000"), ("BUY", "000001")]
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_broker.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.broker'`。


**Step 3: 最小实现** `engine/broker.py`

```python
"""Simulated open-auction broker applying A-share execution rules."""
from datetime import date

from engine.calendar import TradingCalendar
from engine.ledger import Account
from engine.rules import calc_fee, limit_prices, round_lot
from engine.types import Bar, Fill, Pick


class SimBroker:
    def __init__(self, slippage_bps: float = 10.0):
        self.slip = slippage_bps / 10_000

    def execute_day(self, account: Account, d: date, bars: dict[str, Bar], picks: list[Pick],
                    calendar: TradingCalendar, capital_per_pick: float) -> list[Fill]:
        fills: list[Fill] = []
        account.start_day(d)
        # 1) exits: only positions unlocked today (bought before d)
        for code, pos in list(account.positions.items()):
            bar = bars.get(code)
            if pos.available <= 0 or bar is None or bar.volume <= 0:
                continue  # locked by T+1, or suspended
            _, down = limit_prices(bar.code, bar.name, bar.prev_close)
            if bar.high <= down:
                continue  # sealed at limit-down all day: cannot sell
            if d >= pos.exit_date:
                price, reason = bar.open, "到期"
            elif bar.low <= pos.stop:
                price, reason = min(bar.open, pos.stop), "止损"
            elif bar.high >= pos.target:
                price, reason = max(bar.open, pos.target), "止盈"
            else:
                continue
            fills.append(account.sell(d, code, price * (1 - self.slip),
                                      pos.available, reason))
        # 2) entries at the open
        for pick in picks:
            if pick.code in account.positions:
                continue
            bar = bars.get(pick.code)
            if bar is None or bar.volume <= 0:
                continue
            up, _ = limit_prices(bar.code, bar.name, bar.prev_close)
            if bar.open >= up:
                continue  # 开盘涨停, cannot buy
            if bar.open > pick.entry_max:
                continue  # gapped above the buy zone
            price = bar.open * (1 + self.slip)
            qty = round_lot(min(capital_per_pick, account.cash * 0.999) / price)
            # 0.1% buffer may not cover the 5-yuan minimum commission: shrink until affordable
            while qty >= 100 and price * qty + calc_fee("BUY", price * qty) > account.cash:
                qty -= 100
            if qty < 100:
                continue
            fills.append(account.buy(d, pick.code, pick.name or bar.name, price, qty, pick.stop,
                                     pick.target, calendar.offset(d, pick.hold_days), pick.reason))
        return fills
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_broker.py -v
```
预期：16 passed。


**Step 4b: 全量回归**

```powershell
uv run pytest -v
```
预期：Part 1 共 57 个用例（config 3 + types 4 + rules 23 + calendar 4 + ledger 8 + broker 15）全部通过。


**Step 5: Commit**

```powershell
git add engine/broker.py tests/test_broker.py
git commit -m "feat(engine): add open-auction SimBroker"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`）


---

## Part 2: 存储、指标、回测与评估器

> 契约歧义的取舍（每条一行）：
> - `summarize` 的 `excess_ann` 与 `promotion.compare` 的年化超额 = 逐日超额收益（inner join 对齐）均值 × 252（算术年化）。
> - `deflated_sharpe` 使用每期（日）SR；各试验 SR 的方差近似为 `1/n_obs`（零假设下 SR 估计量方差），`n_trials<=1` 时 SR0=0；逆正态用标准库 `statistics.NormalDist().inv_cdf`，CDF 用 `math.erf`。
> - `sharpe` 把 `std <= 1e-12`（含 pandas 浮点噪声，如 `[0.1]*3` 的 std≈1.7e-17）按 `std==0` 处理，返回 0。
> - `DataView.asof` 为只读 property；私有状态用 `__slots__` + 双下划线名字改编（沙箱禁止 `__` 属性访问，策略拿不到完整面板）；`bar_on` 当日无数据抛 `KeyError`。
> - `run_backtest`：净值按"最近收盘价"估值（停牌沿用上次收盘）；持有期越过日历末尾的 pick 当日丢弃；`benchmark_nav` 索引先 `pd.to_datetime` 归一（兼容 date 对象索引）。
> - `evaluate` 增加仅供测试的可选关键字参数 `splits: dict|None=None`（默认 `SPLITS`）；holdout 的 `None` 解析为 `min(calendar.last(), 面板最后有数据日)`，因为新浪交易日历含未来日期。
> - `evaluate` 的 DSR 用训练+验证两段日收益拼接计算，`n_trials` 含本次；所有门槛比较为 `>=`；留出期 `n_days==0` 视为不通过。
> - `to_agent_dict` 返回 `strategy, train, val, dsr, gate_pass, holdout_pass, n_trials`（dsr/n_trials 只来自训练+验证）；`EvalReport` 本身不保存任何留出期数值。
> - `Store` 用一把 `RLock` 串行化同一 sqlite 连接（`check_same_thread=False`），供 `agents/premarket.run_final` 双线程共用；`set_status` 对非法状态抛 `ValueError`。
> - `promotion.decide`：无现任时以 `bench:csi300` 净值为基准；晋升取年化超额最大者，旧现任全部 `retired`；降级 = 无晋升时，若某影子已有 ≥`DEMOTE_WINDOW` 个影子期净值点且现任最近 `DEMOTE_WINDOW` 点相对它年化超额 ≤`DEMOTE_EXCESS_ANN`，现任降为 `shadow`、落后最多的那个影子接任（算作本次唯一的晋升）。
> - `integrity` 计算哈希前把 `\r\n` 归一为 `\n`（防 Windows git autocrlf 误报）；清单缺失时 `verify` 返回 `[MANIFEST_PATH]`（视为不通过）。

本部分依赖 Part 1 的 `engine/types.py`（Bar/Pick/Fill）、`engine/rules.py`、`engine/calendar.py`（TradingCalendar）、`engine/ledger.py`（Account）、`engine/broker.py`（SimBroker）。按顺序执行：2.1 → 2.2 → 2.3 → 2.4 → 2.5（依赖 2.1–2.4）→ 2.6 → 2.7（依赖 2.1）→ 2.8。

本部分全部测试离线运行：不访问网络、不调用 Claude API、不推送企业微信，因此没有手工冒烟步骤。

所有 commit 命令都用第二个 `-m` 追加 trailer：`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`。

### Task 2.1: SQLite 存储层

**Files:**
- Create: `engine/store.py`
- Test: `tests/test_store.py`

所有方法基于单个 sqlite 文件；日期以 ISO 字符串存储；`:memory:` 用于测试。`check_same_thread=False` + `RLock`，供 `agents/premarket.run_final` 的两个线程共用同一 Store。

**Step 1: 写失败测试** `tests/test_store.py`

```python
from datetime import date

import pandas as pd
import pytest

from engine.ledger import Account
from engine.store import Store
from engine.types import Pick


@pytest.fixture
def store():
    return Store(":memory:")


def _pick(code="600000"):
    return Pick(code=code, name="浦发银行", score=1.5, entry_max=10.3, stop=9.3, target=11.0, hold_days=5, reason="测试")


def test_account_roundtrip(store):
    acc = Account(name="q:baseline_momo", cash=500_000.0)
    acc.buy(date(2026, 1, 5), "600000", "浦发银行", 10.0, 1000, 9.3, 11.0, date(2026, 1, 12))
    store.save_account(acc)
    got = store.load_account("q:baseline_momo")
    assert got.cash == pytest.approx(acc.cash)
    assert got.positions["600000"].qty == 1000
    assert got.positions["600000"].exit_date == date(2026, 1, 12)
    assert len(got.trades) == 1
    assert store.load_account("nope") is None
    store.save_account(Account(name="h:active", cash=1.0))
    assert store.list_accounts() == ["h:active", "q:baseline_momo"]


def test_nav_series_sorted_and_last_date(store):
    assert store.last_nav_date("a") is None
    assert store.nav_series("a").empty
    store.record_nav("a", date(2026, 1, 6), 101.0)
    store.record_nav("a", date(2026, 1, 5), 100.0)
    store.record_nav("a", date(2026, 1, 6), 102.0)  # 同日覆盖
    s = store.nav_series("a")
    assert isinstance(s.index, pd.DatetimeIndex)
    assert list(s.index) == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
    assert s.dtype == float and s.iloc[-1] == 102.0
    assert store.last_nav_date("a") == date(2026, 1, 6)


def test_recommendation_and_candidates(store):
    d = date(2026, 1, 5)
    assert store.load_recommendation(d, "h:active") is None
    store.save_recommendation(d, "h:active", [_pick()], {"llm_reviewed": True})
    picks, meta = store.load_recommendation(d, "h:active")
    assert picks == [_pick()] and meta == {"llm_reviewed": True}
    store.save_recommendation(d, "h:candidate", [])
    assert store.load_recommendation(d, "h:candidate") == ([], {})
    assert store.load_candidates(d) is None
    store.save_candidates(d, [_pick("000001")], {"news": [{"title": "利好"}]})
    picks, ctx = store.load_candidates(d)
    assert picks[0].code == "000001" and ctx["news"][0]["title"] == "利好"


def test_jobs(store):
    d = date(2026, 1, 5)
    assert not store.job_done("settle", d)
    store.mark_job("settle", d, "error")
    assert not store.job_done("settle", d)
    store.mark_job("settle", d, "ok")
    assert store.job_done("settle", d)
    assert not store.job_done("settle", date(2026, 1, 6))

def test_llm_usage_month_cost(store):
    store.add_llm_usage("premarket:active", 1000, 200, 1.5)
    store.add_llm_usage("review", 1000, 200, 2.0)
    today = date.today()
    assert store.month_llm_cost(today.year, today.month) == pytest.approx(3.5)
    assert store.month_llm_cost(2000, 1) == 0.0


def test_trials(store):
    assert store.trial_count() == 0
    store.add_trial("a", {"k": 1}, 0.6, 0.9, True, None)
    store.add_trial("b", {}, 0.1, 0.2, False, None)
    store.add_trial("a", {"k": 2}, 0.7, 1.0, True, False)
    assert store.trial_count() == 3
    assert store.trial_count("a") == 2
    recent = store.recent_trials(2)
    assert [t["strategy"] for t in recent] == ["a", "b"]
    assert recent[0]["params"] == {"k": 2} and recent[0]["holdout_pass"] is False
    assert recent[1]["holdout_pass"] is None and recent[1]["gate_pass"] is False


def test_status(store):
    assert store.get_status("x") is None and store.status_since("x") is None
    store.set_status("x", "candidate", date(2026, 1, 5))
    store.set_status("x", "shadow", date(2026, 1, 6))
    store.set_status("y", "shadow", date(2026, 1, 7))
    assert store.get_status("x") == "shadow"
    assert store.status_since("x") == date(2026, 1, 6)
    assert store.strategies_with_status("shadow") == ["x", "y"]
    with pytest.raises(ValueError):
        store.set_status("x", "bogus", date(2026, 1, 6))


def test_kv(store):
    assert store.kv_get("k") is None
    assert store.kv_get("k", 5) == 5
    store.kv_set("k", {"a": [1, 2]})
    assert store.kv_get("k") == {"a": [1, 2]}


def test_store_shared_across_threads(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda i: store.add_llm_usage(f"p{i}", 1, 1, 1.0), range(50)))
    today = date.today()
    assert store.month_llm_cost(today.year, today.month) == pytest.approx(50.0)


def test_file_db_persists(tmp_path):
    p = tmp_path / "sub" / "x.sqlite"
    Store(p).kv_set("a", 1)
    assert Store(p).kv_get("a") == 1
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_store.py -v
```

预期 FAIL：`ModuleNotFoundError: No module named 'engine.store'`。

**Step 3: 最小实现** `engine/store.py`

```python
"""SQLite 持久化：账户、净值、推荐、任务状态、LLM 用量、试验记录、策略状态、KV。"""
from __future__ import annotations

import json
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
        self._exec("INSERT OR REPLACE INTO nav VALUES (?, ?, ?)", (account, d.isoformat(), float(equity)))

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
    def add_trial(self, strategy: str, params: dict, train_sharpe: float, val_sharpe: float,
                  gate_pass: bool, holdout_pass: bool | None) -> None:
        self._exec("INSERT INTO trials (ts, strategy, params, train_sharpe, val_sharpe, gate_pass, holdout_pass) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (_now(), strategy, _dump(params), float(train_sharpe), float(val_sharpe), int(bool(gate_pass)),
                    None if holdout_pass is None else int(bool(holdout_pass))))

    def trial_count(self, strategy: str | None = None) -> int:
        if strategy is None:
            return int(self._one("SELECT COUNT(*) FROM trials")[0])
        return int(self._one("SELECT COUNT(*) FROM trials WHERE strategy=?", (strategy,))[0])

    def recent_trials(self, n: int = 20) -> list[dict]:
        rows = self._all("SELECT ts, strategy, params, train_sharpe, val_sharpe, gate_pass, holdout_pass "
                         "FROM trials ORDER BY id DESC LIMIT ?", (n,))
        return [{"ts": r[0], "strategy": r[1], "params": json.loads(r[2]), "train_sharpe": r[3],
                 "val_sharpe": r[4], "gate_pass": bool(r[5]), "holdout_pass": None if r[6] is None else bool(r[6])}
                for r in rows]

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
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_store.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/store.py tests/test_store.py
git commit -m "feat: add sqlite Store for accounts, nav, recs, trials, status, kv" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.2: 绩效指标与 Deflated Sharpe

**Files:**
- Create: `engine/metrics.py`
- Test: `tests/test_metrics.py`

测试数据全部手算；DSR 合理性检查：试验次数越多，DSR 越低；另含 pandas 浮点噪声 std 的回归用例。

**Step 1: 写失败测试** `tests/test_metrics.py`

```python
import math

import pandas as pd
import pytest

from engine import metrics as m


def _s(vals, start="2026-01-05"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def test_daily_returns():
    r = m.daily_returns(_s([100, 110, 99]))
    assert list(r.round(10)) == [0.1, -0.1]


def test_annualized_return():
    r = _s([0.01] * 252)
    assert m.annualized_return(r) == pytest.approx(1.01 ** 252 - 1)
    assert m.annualized_return(_s([])) == 0.0


def test_sharpe_hand_computed():
    r = _s([0.01, -0.01, 0.02, 0.0])
    mean, std = 0.005, math.sqrt((0.005**2 + 0.015**2 + 0.015**2 + 0.005**2) / 3)
    assert m.sharpe(r) == pytest.approx(mean / std * math.sqrt(252))
    assert m.sharpe(_s([0.01, 0.01, 0.01])) == 0.0
    assert m.sharpe(_s([0.1, 0.1, 0.1])) == 0.0  # pandas 给出 ~1.7e-17 的浮点噪声 std


def test_max_drawdown():
    assert m.max_drawdown(_s([100, 120, 90, 130, 117])) == pytest.approx(-0.25)
    assert m.max_drawdown(_s([1, 2, 3])) == 0.0


def test_excess_returns_inner_join():
    a = _s([0.02, 0.01, 0.03])
    b = _s([0.01, 0.01], start="2026-01-06")
    ex = m.excess_returns(a, b)
    assert list(ex.index) == list(b.index)
    assert list(ex.round(10)) == [0.0, 0.02]


def test_deflated_sharpe_sanity():
    sr = 0.1  # 日夏普 ≈ 年化 1.59
    one = m.deflated_sharpe(sr, n_trials=1, n_obs=500)
    many = m.deflated_sharpe(sr, n_trials=100, n_obs=500)
    assert 0.0 <= many < one <= 1.0
    assert one > 0.95  # 单次试验、500 个观测，0.1 的日夏普高度显著
    assert m.deflated_sharpe(0.0, 1, 500) == pytest.approx(0.5)
    assert m.deflated_sharpe(0.01, 1000, 500) < 0.5  # 海量试验下弱信号不可信


def test_summarize():
    nav = _s([100, 110, 99, 108.9])
    bench = _s([100, 100, 100, 100])
    s = m.summarize(nav, bench)
    assert set(s) == {"total_return", "ann_return", "sharpe", "max_dd", "excess_ann", "n_days"}
    assert s["total_return"] == pytest.approx(0.089)
    assert s["max_dd"] == pytest.approx(-0.1)
    assert s["n_days"] == 4
    assert s["excess_ann"] == pytest.approx((0.1 - 0.1 + 0.1) / 3 * 252)
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_metrics.py -v
```

预期 FAIL：`ImportError: cannot import name 'metrics' from 'engine'`。

**Step 3: 最小实现** `engine/metrics.py`

```python
"""绩效指标：收益、夏普、回撤、超额、Deflated Sharpe（无 scipy）。"""
from __future__ import annotations

import math
from statistics import NormalDist

import pandas as pd

EULER_GAMMA = 0.5772156649015329


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.astype(float).pct_change().dropna()


def annualized_return(r: pd.Series) -> float:
    if len(r) == 0:
        return 0.0
    growth = float((1.0 + r).prod())
    return growth ** (252.0 / len(r)) - 1.0 if growth > 0 else -1.0


def sharpe(r: pd.Series, periods: int = 252) -> float:
    if len(r) < 2:
        return 0.0
    std = float(r.std(ddof=1))
    if not std > 1e-12:  # 0、浮点噪声或 NaN
        return 0.0
    return float(r.mean()) / std * math.sqrt(periods)


def max_drawdown(nav: pd.Series) -> float:
    if len(nav) == 0:
        return 0.0
    nav = nav.astype(float)
    return float((nav / nav.cummax() - 1.0).min())


def excess_returns(r: pd.Series, bench_r: pd.Series) -> pd.Series:
    a, b = r.align(bench_r, join="inner")
    return a - b


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe(sr: float, n_trials: int, n_obs: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Bailey & Lopez de Prado (2014)。sr 为每期（日）夏普，非年化。
    SR0 = sqrt(1/n_obs) * ((1-g)*Z^-1(1-1/N) + g*Z^-1(1-1/(N e)))，N=1 时 SR0=0。"""
    if n_obs < 2:
        return 0.0
    n = max(int(n_trials), 1)
    if n == 1:
        sr0 = 0.0
    else:
        z = NormalDist().inv_cdf
        sr0 = math.sqrt(1.0 / n_obs) * ((1 - EULER_GAMMA) * z(1 - 1.0 / n) + EULER_GAMMA * z(1 - 1.0 / (n * math.e)))
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    return _norm_cdf((sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def summarize(nav: pd.Series, bench_nav: pd.Series) -> dict:
    r = daily_returns(nav)
    ex = excess_returns(r, daily_returns(bench_nav))
    return {
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1.0) if len(nav) else 0.0,
        "ann_return": annualized_return(r),
        "sharpe": sharpe(r),
        "max_dd": max_drawdown(nav),
        "excess_ann": float(ex.mean() * 252) if len(ex) else 0.0,
        "n_days": int(len(nav)),
    }
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_metrics.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/metrics.py tests/test_metrics.py
git commit -m "feat: add performance metrics and deflated sharpe" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.3: 防未来函数的 DataView

**Files:**
- Create: `engine/dataview.py`
- Test: `tests/test_dataview.py`

策略只能通过 DataView 取数；`history` 只返回 `index <= asof` 的行（副本），`bar_on` 访问未来日期抛 `LookaheadError`。

**Step 1: 写失败测试** `tests/test_dataview.py`

```python
from datetime import date

import pandas as pd
import pytest

from engine.dataview import DataView, LookaheadError


def _frame(n=10, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=n)
    close = [10.0 + i for i in range(n)]
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "volume": [1e6] * n, "amount": [1e7] * n,
                         "prev_close": [float("nan")] + close[:-1]}, index=idx)


@pytest.fixture
def view():
    panel = {"600000": _frame(), "000001": _frame()}
    return DataView(panel, {"600000": "浦发银行"}, asof=date(2026, 1, 9))


def test_history_never_after_asof(view):
    h = view.history("600000")
    assert h.index.max() == pd.Timestamp("2026-01-09")
    assert len(h) == 5
    assert (h.index <= pd.Timestamp(view.asof)).all()


def test_history_last_n_and_copy(view):
    h = view.history("600000", 2)
    assert list(h["close"]) == [13.0, 14.0]
    h.loc[h.index[0], "close"] = -1.0
    assert view.history("600000", 2)["close"].iloc[0] == 13.0


def test_codes_and_name(view):
    assert view.codes() == ["000001", "600000"]
    assert view.name("600000") == "浦发银行"
    assert view.name("000001") == ""


def test_bar_on(view):
    b = view.bar_on("600000", date(2026, 1, 9))
    assert b.close == 14.0 and b.prev_close == 13.0 and b.name == "浦发银行"
    with pytest.raises(LookaheadError):
        view.bar_on("600000", date(2026, 1, 12))


def test_asof_readonly_and_state_sealed(view):
    with pytest.raises(AttributeError):
        view.asof = date(2030, 1, 1)
    assert not hasattr(view, "_panel") and not hasattr(view, "panel")
    with pytest.raises(TypeError):
        vars(view)  # 无 __dict__


def test_history_zero_and_unknown(view):
    assert view.history("600000", 0).empty
    with pytest.raises(KeyError):
        view.history("999999")
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_dataview.py -v
```

预期 FAIL：`ModuleNotFoundError: No module named 'engine.dataview'`。

**Step 3: 最小实现** `engine/dataview.py`

```python
"""策略唯一的数据入口：只暴露 asof 及以前的行，杜绝未来函数。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engine.types import Bar


class LookaheadError(Exception):
    pass


class DataView:
    # 私有状态用双下划线（名字改编为 _DataView__xxx）且无 __dict__：
    # 沙箱禁止含 "__" 的属性访问，策略代码因此拿不到完整面板。
    __slots__ = ("__panel", "__names", "__asof", "__ts")

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str], asof: date):
        self.__panel = panel
        self.__names = names
        self.__asof = asof
        self.__ts = pd.Timestamp(asof)

    @property
    def asof(self) -> date:
        return self.__asof

    def codes(self) -> list[str]:
        return sorted(self.__panel)

    def name(self, code: str) -> str:
        return self.__names.get(code, "")

    def history(self, code: str, n: int | None = None) -> pd.DataFrame:
        df = self.__panel[code]
        end = int(df.index.searchsorted(self.__ts, side="right"))  # 索引升序
        start = 0 if n is None else max(end - n, 0)
        return df.iloc[start:end].copy()

    def bar_on(self, code: str, d: date) -> Bar:
        if d > self.__asof:
            raise LookaheadError(f"{code} {d} > asof {self.__asof}")
        row = self.__panel[code].loc[pd.Timestamp(d)]  # 无数据 -> KeyError
        return Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                   close=float(row["close"]), prev_close=float(row["prev_close"]), volume=float(row["volume"]),
                   amount=float(row["amount"]), name=self.name(code))
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_dataview.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/dataview.py tests/test_dataview.py
git commit -m "feat: add lookahead-safe DataView" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.4: 回测引擎与 bars_for_day

**Files:**
- Create: `engine/backtest.py`
- Test: `tests/test_backtest.py`

合成 3 只股票的面板 + 简单策略；验证 select 收到的 asof 恰为 `calendar.prev_day(t)`；偷看未来的策略在回测中直接抛出 `LookaheadError`。`bars_for_day` 跳过 `prev_close` 为 NaN 的行（上市首日/面板首行），也供 `jobs.settle` 使用。

**Step 1: 写失败测试** `tests/test_backtest.py`

```python
from datetime import date

import pandas as pd
import pytest

from engine.backtest import BacktestResult, bars_for_day, run_backtest
from engine.calendar import TradingCalendar
from engine.dataview import LookaheadError
from engine.types import Pick

IDX = pd.bdate_range("2026-01-05", periods=30)
DAYS = [ts.date() for ts in IDX]


def _frame(closes, opens=None):
    opens = opens or closes
    return pd.DataFrame({"open": opens, "high": [max(o, c) for o, c in zip(opens, closes)],
                         "low": [min(o, c) for o, c in zip(opens, closes)], "close": closes,
                         "volume": [1e6] * len(closes), "amount": [1e8] * len(closes),
                         "prev_close": [float("nan")] + closes[:-1]}, index=IDX)


def _panel():
    up = [10.0 * 1.01 ** i for i in range(30)]
    flat = [20.0] * 30
    down = [30.0 * 0.995 ** i for i in range(30)]
    return {"600001": _frame(up), "600002": _frame(flat), "000003": _frame(down)}


NAMES = {"600001": "上涨股", "600002": "横盘股", "000003": "下跌股"}
CAL = TradingCalendar(DAYS)
BENCH = pd.Series(100.0, index=IDX)


class Recorder:
    """记录每次 select 收到的 asof，并始终买最近涨幅最大的股票。"""
    name = "recorder"

    def __init__(self):
        self.asofs = []

    def select(self, view):
        self.asofs.append(view.asof)
        scored = []
        for code in view.codes():
            h = view.history(code, 2)
            if len(h) == 2:
                last = float(h["close"].iloc[-1])
                scored.append(Pick(code, view.name(code), float(h["close"].pct_change().iloc[-1]),
                                   last * 1.05, last * 0.9, last * 1.5, 3))
        return sorted(scored, key=lambda p: p.score, reverse=True)


class Peeker:
    name = "peeker"

    def select(self, view):
        view.bar_on("600001", DAYS[-1])  # 偷看未来
        return []


def test_bars_for_day():
    bars = bars_for_day(_panel(), NAMES, DAYS[1])
    assert set(bars) == {"600001", "600002", "000003"}
    b = bars["600001"]
    assert b.date == DAYS[1] and b.name == "上涨股"
    assert b.prev_close == pytest.approx(10.0) and b.close == pytest.approx(10.1)
    assert bars_for_day(_panel(), NAMES, DAYS[0]) == {}  # 首日无昨收
    assert bars_for_day(_panel(), NAMES, date(2030, 1, 1)) == {}


def test_picks_use_prev_day_asof():
    s = Recorder()
    run_backtest(s, _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH)
    assert s.asofs == [CAL.prev_day(t) for t in CAL.between(DAYS[2], DAYS[10])]


def test_peeking_strategy_raises():
    with pytest.raises(LookaheadError):
        run_backtest(Peeker(), _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH)


def test_backtest_result_shape_and_trading():
    res = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], BENCH, capital=100_000, max_picks=1)
    assert isinstance(res, BacktestResult)
    assert list(res.nav.index) == [pd.Timestamp(d) for d in CAL.between(DAYS[2], DAYS[20])]
    assert res.nav.iloc[0] < 100_000  # 首日买入扣了费用和滑点
    buys = [f for f in res.fills if f.side == "BUY"]
    assert buys and all(f.code == "600001" for f in buys)
    assert any(f.side == "SELL" and f.reason == "到期" for f in res.fills)
    assert res.metrics["n_days"] == len(res.nav)
    assert res.metrics["total_return"] > 0  # 买的是上涨股


def test_benchmark_with_date_index_is_normalized():
    a = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=IDX, dtype=float))
    b = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=DAYS, dtype=float))
    assert a.metrics["excess_ann"] != 0.0
    assert a.metrics == b.metrics


def test_picks_beyond_calendar_end_dropped():
    res = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[-2], DAYS[-1], BENCH)
    assert res.fills == []
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_backtest.py -v
```

预期 FAIL：`ModuleNotFoundError: No module named 'engine.backtest'`。

**Step 3: 最小实现** `engine/backtest.py`

```python
"""逐日回放：t 日用 asof=t-1 的 DataView 选股，用 t 日 bar 撮合。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.broker import SimBroker
from engine.calendar import TradingCalendar
from engine.dataview import DataView
from engine.ledger import Account
from engine.metrics import summarize
from engine.types import Bar, Fill


@dataclass
class BacktestResult:
    nav: pd.Series
    fills: list[Fill]
    metrics: dict


def bars_for_day(panel: dict[str, pd.DataFrame], names: dict[str, str], d: date) -> dict[str, Bar]:
    ts = pd.Timestamp(d)
    out: dict[str, Bar] = {}
    for code, df in panel.items():
        if ts not in df.index:
            continue
        row = df.loc[ts]
        prev = float(row["prev_close"])
        if prev != prev:  # NaN：上市首日无昨收，跳过
            continue
        out[code] = Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]), prev_close=prev,
                        volume=float(row["volume"]), amount=float(row["amount"]), name=names.get(code, ""))
    return out


def run_backtest(strategy, panel, names, calendar: TradingCalendar, start: date, end: date,
                 benchmark_nav: pd.Series, capital: float = 500_000, max_picks: int = 5,
                 slippage_bps: float = 10.0) -> BacktestResult:
    acct = Account(name=f"bt:{getattr(strategy, 'name', 'strategy')}", cash=float(capital))
    broker = SimBroker(slippage_bps=slippage_bps)
    last_close: dict[str, float] = {}
    days = calendar.between(start, end)
    horizon = len(calendar.between(start, calendar.last()))
    navs: list[float] = []
    for i, t in enumerate(days):
        view = DataView(panel, names, asof=calendar.prev_day(t))
        picks = list(strategy.select(view))[:max_picks]
        # 持有期越过日历末尾的 pick 无法确定到期日，丢弃（t 之后还剩 horizon-1-i 个交易日）
        picks = [p for p in picks if p.hold_days <= horizon - 1 - i]
        bars = bars_for_day(panel, names, t)
        broker.execute_day(acct, t, bars, picks, calendar, capital / max_picks)
        last_close.update({c: b.close for c, b in bars.items()})
        navs.append(acct.equity(last_close))
    nav = pd.Series(navs, index=pd.DatetimeIndex([pd.Timestamp(t) for t in days]), dtype=float)
    bench = benchmark_nav.copy()
    bench.index = pd.to_datetime(bench.index)  # 兼容 date 对象索引
    bench = bench.sort_index().loc[pd.Timestamp(start):pd.Timestamp(end)]
    return BacktestResult(nav=nav, fills=list(acct.trades), metrics=summarize(nav, bench))
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_backtest.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/backtest.py tests/test_backtest.py
git commit -m "feat: add day-by-day backtest and bars_for_day" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.5: 评估器（门槛、DSR、留出集）

**Files:**
- Create: `engine/evaluator.py`
- Test: `tests/test_evaluator.py`

测试用 120 个交易日的合成面板与自定义 `splits`，并 monkeypatch `engine.evaluator.run_backtest` 做调用监视与留出期数值哨兵。`EvalReport` 不保存任何留出期数值，`to_agent_dict` 只暴露 `holdout_pass` 布尔值。

**Step 1: 写失败测试** `tests/test_evaluator.py`

```python
import json
from datetime import date

import pandas as pd

import engine.evaluator as ev
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick

IDX = pd.bdate_range("2026-01-05", periods=120)
DAYS = [ts.date() for ts in IDX]
SPL = {"train": (DAYS[2], DAYS[40]), "val": (DAYS[41], DAYS[80]), "holdout": (DAYS[81], None)}
CAL = TradingCalendar(DAYS)
BENCH = pd.Series(100.0, index=IDX)


def _panel():
    closes = [10.0 * 1.01 ** i for i in range(120)]
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                       "volume": [1e6] * 120, "amount": [1e8] * 120,
                       "prev_close": [float("nan")] + closes[:-1]}, index=IDX)
    return {"600001": df}


NAMES = {"600001": "上涨股"}


class Winner:
    name = "winner"

    def select(self, view):
        last = float(view.history("600001", 1)["close"].iloc[-1])
        return [Pick("600001", "上涨股", 1.0, last * 1.05, last * 0.9, last * 1.5, 3)]


class Idle:
    name = "idle"

    def select(self, view):
        return []


def test_gate_pass_runs_holdout_and_records_trial():
    store = Store(":memory:")
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, {"p": 1}, splits=SPL)
    assert rep.gate_pass is True
    assert rep.holdout_pass is True
    assert rep.n_trials == 1 == store.trial_count()
    t = store.recent_trials(1)[0]
    assert t["strategy"] == "winner" and t["params"] == {"p": 1} and t["holdout_pass"] is True


def test_gate_fail_skips_holdout(monkeypatch):
    store = Store(":memory:")
    calls = []
    real = ev.run_backtest

    def spy(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        calls.append(start)
        return real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)

    monkeypatch.setattr(ev, "run_backtest", spy)
    rep = ev.evaluate(Idle(), "idle", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    assert rep.gate_pass is False and rep.holdout_pass is None
    assert calls == [DAYS[2], DAYS[41]]  # 留出期未运行
    assert store.recent_trials(1)[0]["holdout_pass"] is None


def test_holdout_end_clamped_to_last_data_day(monkeypatch):
    future = [d.date() for d in pd.bdate_range(IDX[-1] + pd.Timedelta(days=1), periods=10)]
    cal = TradingCalendar(DAYS + future)  # 新浪日历含未来日期
    calls = []
    real = ev.run_backtest

    def spy(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        calls.append((start, end))
        return real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)

    monkeypatch.setattr(ev, "run_backtest", spy)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, cal, BENCH, Store(":memory:"), splits=SPL)
    assert rep.gate_pass is True
    assert calls[-1] == (DAYS[81], DAYS[-1])


def test_n_trials_counts_all_strategies():
    store = Store(":memory:")
    store.add_trial("other", {}, 0.0, 0.0, False, None)
    rep = ev.evaluate(Idle(), "idle", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    assert rep.n_trials == 2


def test_to_agent_dict_never_contains_holdout_numbers(monkeypatch):
    store = Store(":memory:")
    real = ev.run_backtest
    sentinel = 123.456

    def tagged(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        res = real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)
        if start == SPL["holdout"][0]:
            res.metrics.update(sharpe=sentinel, excess_ann=sentinel)
        return res

    monkeypatch.setattr(ev, "run_backtest", tagged)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    d = rep.to_agent_dict()
    assert set(d) == {"strategy", "train", "val", "dsr", "gate_pass", "holdout_pass", "n_trials"}
    assert isinstance(d["holdout_pass"], bool)
    assert str(sentinel) not in json.dumps(d)
    assert str(sentinel) not in repr(rep)
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_evaluator.py -v
```

预期 FAIL：`ModuleNotFoundError: No module named 'engine.evaluator'`。

**Step 3: 最小实现** `engine/evaluator.py`

```python
"""锁定的评估器：训练/验证/留出三段回测 + 门槛 + DSR。留出期数值永不外传。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.backtest import run_backtest
from engine.calendar import TradingCalendar
from engine.metrics import deflated_sharpe, sharpe
from engine.store import Store

SPLITS = {"train": (date(2016, 1, 1), date(2024, 12, 31)),
          "val": (date(2025, 1, 1), date(2026, 6, 30)),
          "holdout": (date(2026, 7, 1), None)}  # None = calendar.last()
GATE = {"train_sharpe": 0.5, "val_sharpe": 0.8, "val_excess_ann": 0.03, "val_max_dd": -0.25, "dsr": 0.90}
HOLDOUT_GATE = {"excess_ann": 0.0, "sharpe": 0.5}


@dataclass
class EvalReport:
    strategy: str
    train: dict
    val: dict
    dsr: float
    gate_pass: bool
    holdout_pass: bool | None
    n_trials: int

    def to_agent_dict(self) -> dict:
        # 只含训练/验证指标与留出期的通过与否；本类本身也不保存任何留出期数值
        return {"strategy": self.strategy, "train": dict(self.train), "val": dict(self.val),
                "dsr": self.dsr, "gate_pass": self.gate_pass, "holdout_pass": self.holdout_pass,
                "n_trials": self.n_trials}


def _passes_gate(train: dict, val: dict, dsr: float) -> bool:
    return (train["sharpe"] >= GATE["train_sharpe"] and val["sharpe"] >= GATE["val_sharpe"]
            and val["excess_ann"] >= GATE["val_excess_ann"] and val["max_dd"] >= GATE["val_max_dd"]
            and dsr >= GATE["dsr"])


def _data_end(panel, calendar: TradingCalendar) -> date:
    # 新浪交易日历含未来日期；留出期只回放到面板最后一个有数据的交易日
    last = max((df.index.max() for df in panel.values() if len(df)), default=None)
    return calendar.last() if last is None else min(calendar.last(), last.date())


def evaluate(strategy, name: str, panel, names, calendar: TradingCalendar, benchmark_nav: pd.Series,
             store: Store, params: dict | None = None, splits: dict | None = None) -> EvalReport:
    splits = splits or SPLITS

    def bt(key: str):
        start, end = splits[key]
        return run_backtest(strategy, panel, names, calendar, start, end or _data_end(panel, calendar), benchmark_nav)

    tr, va = bt("train"), bt("val")
    r = pd.concat([tr.nav.pct_change().dropna(), va.nav.pct_change().dropna()])
    n_trials = store.trial_count() + 1  # 含本次
    dsr = deflated_sharpe(sharpe(r, periods=1), n_trials, len(r), float(r.skew()) if len(r) > 2 else 0.0,
                          float(r.kurt()) + 3.0 if len(r) > 3 else 3.0)
    gate_pass = _passes_gate(tr.metrics, va.metrics, dsr)
    holdout_pass = None
    if gate_pass:
        ho = bt("holdout").metrics
        holdout_pass = (ho["n_days"] > 0 and ho["excess_ann"] >= HOLDOUT_GATE["excess_ann"]
                        and ho["sharpe"] >= HOLDOUT_GATE["sharpe"])
    store.add_trial(name, params or {}, tr.metrics["sharpe"], va.metrics["sharpe"], gate_pass, holdout_pass)
    return EvalReport(strategy=name, train=tr.metrics, val=va.metrics, dsr=dsr, gate_pass=gate_pass,
                      holdout_pass=holdout_pass, n_trials=store.trial_count())
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_evaluator.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/evaluator.py tests/test_evaluator.py
git commit -m "feat: add locked evaluator with gate, DSR and blind holdout" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.6: 锁定区哈希校验

**Files:**
- Create: `engine/integrity.py`
- Test: `tests/test_integrity.py`

只校验 `LOCKED_DIRS` 下的 `*.py`；清单缺失时 `verify` 返回 `[MANIFEST_PATH]`（视为不通过）。

**Step 1: 写失败测试** `tests/test_integrity.py`

```python
from pathlib import Path

from engine import integrity


def _tree(root: Path):
    (root / "engine" / "sub").mkdir(parents=True)
    (root / "engine" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "engine" / "sub" / "b.py").write_text("y = 2\n", encoding="utf-8")
    (root / "engine" / "notes.txt").write_text("ignored", encoding="utf-8")
    (root / "strategies").mkdir()
    (root / "strategies" / "s.py").write_text("z = 3\n", encoding="utf-8")


def test_manifest_covers_only_locked_py(tmp_path):
    _tree(tmp_path)
    m = integrity.compute_manifest(tmp_path)
    assert set(m) == {"engine/a.py", "engine/sub/b.py"}
    assert all(len(v) == 64 for v in m.values())


def test_verify_ok_then_detects_changes(tmp_path):
    _tree(tmp_path)
    assert integrity.verify(tmp_path) == [integrity.MANIFEST_PATH]  # 尚无清单
    integrity.write_manifest(tmp_path)
    assert (tmp_path / ".integrity" / "manifest.json").exists()
    assert integrity.verify(tmp_path) == []
    (tmp_path / "strategies" / "s.py").write_text("z = 4\n", encoding="utf-8")
    assert integrity.verify(tmp_path) == []  # 可改区不受约束
    (tmp_path / "engine" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "engine" / "new.py").write_text("", encoding="utf-8")
    (tmp_path / "engine" / "sub" / "b.py").unlink()
    assert integrity.verify(tmp_path) == ["engine/a.py", "engine/new.py", "engine/sub/b.py"]


def test_crlf_does_not_trigger(tmp_path):
    _tree(tmp_path)
    integrity.write_manifest(tmp_path)
    (tmp_path / "engine" / "a.py").write_bytes(b"x = 1\r\n")
    assert integrity.verify(tmp_path) == []
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_integrity.py -v
```

预期 FAIL：`ImportError: cannot import name 'integrity' from 'engine'`。

**Step 3: 最小实现** `engine/integrity.py`

```python
"""锁定区哈希校验：engine/ 下所有 .py 的 sha256 清单。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCKED_DIRS = ["engine"]
MANIFEST_PATH = ".integrity/manifest.json"


def _digest(path: Path) -> str:
    # 归一换行，避免 Windows autocrlf 造成误报
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def compute_manifest(root: Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for d in LOCKED_DIRS:
        for p in sorted((root / d).rglob("*.py")):
            out[p.relative_to(root).as_posix()] = _digest(p)
    return out


def write_manifest(root: Path) -> None:
    path = Path(root) / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compute_manifest(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify(root: Path) -> list[str]:
    path = Path(root) / MANIFEST_PATH
    if not path.exists():
        return [MANIFEST_PATH]
    expected: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    actual = compute_manifest(root)
    return sorted(k for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k))
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_integrity.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/integrity.py tests/test_integrity.py
git commit -m "feat: add engine integrity manifest and verify" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.7: 晋升与降级

**Files:**
- Create: `engine/promotion.py`
- Test: `tests/test_promotion.py`

影子期净值 = `q:<name>` 账户在 `status_since` 至 today 间的净值点；不足 `MIN_SHADOW_DAYS` 个点不参与。晋升：年化超额 >= `PROMOTE_EXCESS_ANN` 且 t >= `PROMOTE_T`，取超额最大者，旧现任 `retired`。降级：无晋升时，若某影子已满 `DEMOTE_WINDOW` 个点且现任最近 `DEMOTE_WINDOW` 点相对它年化超额 <= `DEMOTE_EXCESS_ANN`，现任降为 `shadow`，落后最多的那个影子接任。

**Step 1: 写失败测试** `tests/test_promotion.py`

```python
import pandas as pd
import pytest

from engine import promotion as pr
from engine.calendar import TradingCalendar
from engine.store import Store

IDX = pd.bdate_range("2026-01-05", periods=60)
DAYS = [ts.date() for ts in IDX]
CAL = TradingCalendar(DAYS)


def _nav(daily, noise=0.002):
    rets = [daily + (noise if i % 2 else -noise) for i in range(len(DAYS))]
    vals, v = [], 100.0
    for r in rets:
        v *= 1 + r
        vals.append(v)
    return vals


def _record(store, account, vals, days=DAYS):
    for d, v in zip(days, vals):
        store.record_nav(account, d, v)


def _setup(cand_daily, shadow_from=0, inc_daily=0.0):
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status("new", "shadow", DAYS[shadow_from])
    _record(store, "q:old", _nav(inc_daily, noise=0.003))
    _record(store, "q:new", _nav(cand_daily, noise=0.001))
    return store


def test_compare():
    base = pd.Series(_nav(0.0), index=IDX)
    cand = pd.Series(_nav(0.001), index=IDX)
    ann, t = pr.compare(base, cand)
    assert ann > 0.2 and t > 3
    assert pr.compare(base, base) == (0.0, 0.0)


def test_promote_after_20_days_retires_old():
    store = _setup(cand_daily=0.002, shadow_from=30)
    events = pr.decide(store, DAYS[-1], CAL)
    assert [e.kind for e in events] == ["promote", "retire"]
    assert events[0].strategy == "new" and events[1].strategy == "old"
    assert store.get_status("new") == "incumbent"
    assert store.get_status("old") == "retired"
    assert store.status_since("new") == DAYS[-1]


def test_no_promotion_with_fewer_than_20_days():
    store = _setup(cand_daily=0.002, shadow_from=45)  # 15 个交易日
    assert pr.decide(store, DAYS[-1], CAL) == []
    assert store.get_status("new") == "shadow" and store.get_status("old") == "incumbent"


def test_no_promotion_when_excess_weak():
    store = _setup(cand_daily=0.00005, shadow_from=0)
    assert pr.decide(store, DAYS[-1], CAL) == []


def test_at_most_one_promotion():
    store = _setup(cand_daily=0.002)
    store.set_status("new2", "shadow", DAYS[0])
    _record(store, "q:new2", _nav(0.003, noise=0.001))
    events = pr.decide(store, DAYS[-1], CAL)
    assert [e.strategy for e in events if e.kind == "promote"] == ["new2"]
    assert store.get_status("new") == "shadow"


def test_demotion_when_incumbent_lags(monkeypatch):
    # 晋升门槛设为不可达，单独验证降级规则
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = _setup(cand_daily=0.0005, inc_daily=0.0)
    events = pr.decide(store, DAYS[-1], CAL)
    assert [(e.kind, e.strategy) for e in events] == [("demote", "old"), ("promote", "new")]
    assert store.get_status("old") == "shadow" and store.get_status("new") == "incumbent"


def test_no_demotion_before_full_window(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = _setup(cand_daily=0.0005, shadow_from=30)  # 影子 30 日 < DEMOTE_WINDOW
    assert pr.decide(store, DAYS[-1], CAL) == []
    assert store.get_status("old") == "incumbent"


def test_no_incumbent_compares_to_benchmark():
    store = Store(":memory:")
    store.set_status("new", "shadow", DAYS[0])
    _record(store, "bench:csi300", _nav(0.0, noise=0.003))
    _record(store, "q:new", _nav(0.002, noise=0.001))
    events = pr.decide(store, DAYS[-1], CAL)
    assert [(e.kind, e.strategy) for e in events] == [("promote", "new")]
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_promotion.py -v
```

预期 FAIL：`ImportError: cannot import name 'promotion' from 'engine'`。

**Step 3: 最小实现** `engine/promotion.py`

```python
"""量化策略晋升/降级：影子盘 >= 20 日且显著跑赢现任 -> 晋升；现任近 40 日落后影子 >= 5% -> 降级。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine.calendar import TradingCalendar
from engine.store import Store

MIN_SHADOW_DAYS = 20
PROMOTE_EXCESS_ANN = 0.05
PROMOTE_T = 1.5
DEMOTE_WINDOW = 40
DEMOTE_EXCESS_ANN = -0.05
BENCH_ACCOUNT = "bench:csi300"


@dataclass
class Event:
    kind: str  # "promote" | "demote" | "retire"
    strategy: str
    detail: str


def compare(base_nav: pd.Series, cand_nav: pd.Series) -> tuple[float, float]:
    b, c = base_nav.pct_change().dropna().align(cand_nav.pct_change().dropna(), join="inner")
    ex = c - b
    if len(ex) < 2:
        return 0.0, 0.0
    ann = float(ex.mean() * 252)
    std = float(ex.std(ddof=1))
    t = float(ex.mean()) / (std / math.sqrt(len(ex))) if std > 0 else 0.0  # 恒定超额 -> 极大 t，合理
    return ann, t


def _shadow_nav(store: Store, s: str, today: date, calendar: TradingCalendar) -> pd.Series:
    since = store.status_since(s)
    nav = store.nav_series(f"q:{s}")
    days = pd.DatetimeIndex([pd.Timestamp(d) for d in calendar.between(since, today)])
    return nav[nav.index.isin(days)]


def decide(store: Store, today: date, calendar: TradingCalendar) -> list[Event]:
    incs = store.strategies_with_status("incumbent")
    inc = incs[0] if incs else None
    base = store.nav_series(f"q:{inc}" if inc else BENCH_ACCOUNT)
    base = base[base.index <= pd.Timestamp(today)]
    shadows = {s: _shadow_nav(store, s, today, calendar) for s in store.strategies_with_status("shadow")}
    shadows = {s: nav for s, nav in shadows.items() if len(nav) >= MIN_SHADOW_DAYS}

    # 1) 晋升：影子期内年化超额 >= 5% 且 t >= 1.5，取超额最大者
    best: tuple[float, float, str] | None = None
    for s, nav in shadows.items():
        ann, t = compare(base, nav)
        if ann >= PROMOTE_EXCESS_ANN and t >= PROMOTE_T and (best is None or ann > best[0]):
            best = (ann, t, s)
    if best:
        ann, t, s = best
        events = [Event("promote", s, f"影子 {len(shadows[s])} 日，年化超额 {ann:.2%}，t={t:.2f}")]
        for old in incs:
            store.set_status(old, "retired", today)
            events.append(Event("retire", old, f"被 {s} 取代"))
        store.set_status(s, "incumbent", today)
        return events

    # 2) 降级：影子已满 DEMOTE_WINDOW 日，且现任在这一窗口内相对该影子年化超额 <= -5%
    if inc:
        worst: tuple[float, str] | None = None
        for s, nav in shadows.items():
            if len(nav) < DEMOTE_WINDOW:
                continue
            ann, _ = compare(nav.iloc[-DEMOTE_WINDOW:], base.iloc[-DEMOTE_WINDOW:])
            if ann <= DEMOTE_EXCESS_ANN and (worst is None or ann < worst[0]):
                worst = (ann, s)
        if worst:
            ann, s = worst
            store.set_status(inc, "shadow", today)
            store.set_status(s, "incumbent", today)
            return [Event("demote", inc, f"近 {DEMOTE_WINDOW} 日落后 {s} 年化 {-ann:.2%}，降为影子"),
                    Event("promote", s, f"接替被降级的 {inc}")]
    return []
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_promotion.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/promotion.py tests/test_promotion.py
git commit -m "feat: add quant strategy promotion/demotion rules" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2.8: Part 2 收尾检查

**Step 1: 全量运行本部分测试**

```
uv run pytest tests/test_store.py tests/test_metrics.py tests/test_dataview.py tests/test_backtest.py tests/test_evaluator.py tests/test_integrity.py tests/test_promotion.py -v
```

预期：全部 PASS（无网络、无 Claude 调用）。

**Step 2: 无需提交**（各 Task 已分别提交）。注意：`.integrity/manifest.json` 由 `jobs.bootstrap` 在所有 engine 文件就绪后生成，本部分不写清单；之后任何 engine/ 改动都必须由人工重跑 `uv run python -c "from pathlib import Path; from engine.integrity import write_manifest; write_manifest(Path('.'))"` 并提交。


---

## Part 3: 数据层、推送与策略

> 合同歧义处理（本节统一采用以下最简一致选择）：
> - `BarCache.update` 每只股票只请求"最后存储日的次日"到 `end`；单只股票失败不影响其他股票，返回失败代码列表。前复权基准会在除权后变化，增量拼接会在除权日留下跳变，所以额外提供 `update(..., full=True)` 删除后全量重拉（bootstrap 和定期维护用）。这是已知局限。
> - `load_panel` 先在全部历史上算 `prev_close = close.shift(1)`，再按 `[start, end]` 截取；每只股票第一根 K 线缺失的 `prev_close` 用当日 `open` 填充。
> - `BarCache` 额外提供 `codes()`（缓存中全部代码）和 `last_date(code)`。表名加前缀（`bar_daily`/`bar_names`/`index_daily`），和 `Store` 共用同一个 sqlite 文件时不会冲突。约定 bootstrap 用 `save_index("sh000300", ...)` 保存基准。
> - `index_daily` / `load_index` 返回的 Series 使用归一化 `DatetimeIndex`（和 panel 一致），`name` 为指数代码。`daily_bars` 的 `date` 列是归一化的 `datetime64`。
> - `spot()` 新浪备用源的 `成交量` 单位为股，统一除以 100 换算成手（和东财一致），需在冒烟步骤中核实。
> - `NullNotifier.sent` 是 `list[str]`（消息原文）。`notify/wecom.py` 额外导出常量 `TEXT_LIMIT`/`MARKDOWN_LIMIT`/`FAIL_PREFIX` 和函数 `split_utf8`；失败日志会把 webhook 的 `key=` 脱敏。
> - `baseline_momo` 额外排除最后一根 K 线不在 `view.asof` 的股票（asof 当日停牌）。同分时按代码升序。策略文件不写 `from __future__ import annotations`（`__future__` 不在沙箱白名单）。
> - `check_source` 采用更严格的口径：`getattr/setattr/delattr` 一律禁止（不只针对 dunder），另禁 `locals/vars/breakpoint/input`、任何以 `__` 开头的名字，以及 pandas/numpy 的文件读写属性（`read_*`、`to_csv` 等）。允许导入白名单模块及其子模块（`numpy.linalg` 可以，`engine.store` 不行）。沙箱是尽力而为的静态检查，不是安全边界；真正的防线是 `engine/` 哈希校验和 git 审计。
> - `engine/run_eval.py` 全程不触网：交易日历取自缓存基准指数 `sh000300` 的日期，股票池为 `cache.codes()`，基准净值为指数归一化到 1。出错时也输出一行 `{"error": ...}` JSON，退出码非 0。它在 `engine/` 内，会被纳入完整性清单。
> - 依赖前面 Part 的 `config.py`、`engine/types.py`、`engine/rules.py`、`engine/dataview.py`、`engine/calendar.py`、`engine/store.py`、`engine/evaluator.py`，以及 `data/`、`notify/`、`strategies/` 各包的 `__init__.py`（若缺失，先创建空文件）。

### Task 3.1: AKShare 客户端（data/akshare_client.py）

**Files:**
- Create: `data/akshare_client.py`
- Create: `scripts/smoke_akshare.py`
- Test: `tests/test_akshare_client.py`

**Step 1: 写失败测试**

测试把 `data.akshare_client.ak` 整体替换为 `SimpleNamespace` 假模块，返回带中文列名的假 DataFrame，不触网。

```python
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
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_akshare_client.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'data.akshare_client'`。

**Step 3: 最小实现**

列名映射都是模块级字典，AKShare 改列名时只需改一行。

```python
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
NUMERIC = ["open", "high", "low", "close", "volume", "amount", "prev_close", "last", "pct"]


class DataError(Exception):
    pass


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

    def index_daily(self, symbol: str = "sh000300") -> pd.Series:
        df = self._call(ak.stock_zh_index_daily, symbol=symbol)
        idx = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
        s = pd.Series(pd.to_numeric(df["close"]).astype(float).to_numpy(), index=idx, name=symbol)
        return s.sort_index()

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
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_akshare_client.py -v
```

预期：13 passed。

**Step 5: 手工冒烟脚本（Manual smoke step，需联网）**

AKShare 列名只能实网核实。脚本逐个调用并打印形状与前几行，任一接口失败打印异常后继续。

```python
# scripts/smoke_akshare.py
"""手工冒烟：uv run python scripts/smoke_akshare.py  （需联网，不在 pytest 中运行）"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import akshare as ak  # noqa: E402

from data.akshare_client import AkClient  # noqa: E402


def main() -> int:
    c = AkClient(attempts=2, wait=1.0)
    end = date.today()
    start = end - timedelta(days=20)
    checks = {
        "trade_calendar": lambda: c.trade_calendar()[-3:],
        "daily_bars": lambda: c.daily_bars("600000", start, end).tail(3),
        "index_daily": lambda: c.index_daily("sh000300").tail(3),
        "universe": lambda: (len(u := c.universe()), u[:5]),
        "spot(em)": lambda: c.spot().head(3),
        "spot(sina raw)": lambda: ak.stock_zh_a_spot().head(3),
        "news_global": lambda: c.news_global(3),
        "stock_news": lambda: c.stock_news("600000", 2),
        "notices": lambda: c.notices(end - timedelta(days=1)).head(3),
    }
    failed = 0
    for name, fn in checks.items():
        try:
            print(f"=== {name} OK ===\n{fn()}\n")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"=== {name} FAILED: {type(e).__name__}: {e} ===\n")
    print(f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

运行：

```
uv run python scripts/smoke_akshare.py
```

预期：每项打印 `OK`，最后 `0 failed`。核对要点：
- `daily_bars` 的 `close` 与行情软件前复权收盘价一致；`volume` 单位为手。
- `spot(sina raw)` 打印的原始列名包含 `SPOT_SINA_COLS` 的全部键；比较同一只股票新浪 `成交量` 与东财 `成交量`，确认 `SINA_VOLUME_DIVISOR=100` 是否成立。
- 若某接口报 `DataError ... 缺少列`，只改 `data/akshare_client.py` 顶部对应映射字典的一行，然后重跑本测试和冒烟脚本。
- 盘后/周末 `spot` 和 `notices` 可能返回空表，这属于正常现象。

**Step 6: 提交**

```
git add data/akshare_client.py scripts/smoke_akshare.py tests/test_akshare_client.py
git commit -m "feat: add AKShare client with retry, column maps and spot fallback"
```

（提交信息末尾追加 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）

### Task 3.2: 日线缓存（data/cache.py）

**Files:**
- Create: `data/cache.py`
- Test: `tests/test_cache.py`

**Step 1: 写失败测试**

```python
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
    assert client.calls[-1] == ("600000", date(2026, 1, 8), date(2026, 1, 8))
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
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_cache.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'data.cache'`。

**Step 3: 最小实现**

```python
# data/cache.py
"""SQLite 日线缓存：按股票增量更新，产出 DataView 用的 panel。

注意：前复权价会在除权后整体变化，增量拼接会在除权日留下跳变；
需要干净历史时用 update(..., full=True) 全量重拉。
"""
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

BAR_COLS = ["open", "high", "low", "close", "volume", "amount"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS bar_daily (
    code TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    PRIMARY KEY (code, date));
CREATE TABLE IF NOT EXISTS bar_names (code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS index_daily (
    symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY (symbol, date));
"""


class BarCache:
    def __init__(self, db_path, client):
        self.client = client
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def last_date(self, code: str) -> date | None:
        row = self.conn.execute("SELECT MAX(date) FROM bar_daily WHERE code=?", (code,)).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def codes(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT code FROM bar_daily ORDER BY code")]

    def update(self, codes: list[str], end: date, start_default: date = date(2015, 6, 1),
               full: bool = False) -> list[str]:
        """逐只增量更新，返回失败的代码列表（单只失败不影响其他）。"""
        failed = []
        for code in codes:
            try:
                if full:
                    with self.conn:
                        self.conn.execute("DELETE FROM bar_daily WHERE code=?", (code,))
                last = self.last_date(code)
                start = last + timedelta(days=1) if last else start_default
                if start > end:
                    continue
                df = self.client.daily_bars(code, start, end)
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

    def save_index(self, symbol: str, series: pd.Series) -> None:
        rows = [(symbol, pd.Timestamp(d).date().isoformat(), float(v)) for d, v in series.items()]
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO index_daily VALUES (?,?,?)", rows)

    def load_index(self, symbol: str) -> pd.Series:
        df = pd.read_sql_query("SELECT date, close FROM index_daily WHERE symbol=? ORDER BY date",
                               self.conn, params=(symbol,))
        idx = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
        return pd.Series(df["close"].astype(float).to_numpy(), index=idx, name=symbol)
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_cache.py -v
```

预期：7 passed。

**Step 5: 提交**

```
git add data/cache.py tests/test_cache.py
git commit -m "feat: add incremental sqlite bar cache with panel loader"
```

（提交信息末尾追加 `Co-Authored-By` trailer。）

### Task 3.3: 企业微信推送（notify/wecom.py）

**Files:**
- Create: `notify/wecom.py`
- Test: `tests/test_wecom.py`

**Step 1: 写失败测试**

```python
# tests/test_wecom.py
import json

import pytest

import notify.wecom as wc
from notify.wecom import FAIL_PREFIX, NullNotifier, WeCom, split_utf8

URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET123"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = {"errcode": 0, "errmsg": "ok"} if body is None else body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, script=None):
        self.script = list(script or [])  # Resp 或 Exception；耗尽后返回成功
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        item = self.script.pop(0) if self.script else Resp()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(wc.time, "sleep", lambda s: None)


def make(tmp_path, script=None):
    s = FakeSession(script)
    return WeCom(URL, session=s, log_dir=tmp_path / "logs"), s


def test_split_utf8_respects_byte_limit_for_chinese():
    text = "涨" * 1000  # 3000 字节
    chunks = split_utf8(text, 2000)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-8")) <= 2000 for c in chunks)
    assert len(chunks) == 2


def test_split_utf8_prefers_newlines():
    text = "\n".join(["行" * 300] * 5)  # 每行 900 字节
    chunks = split_utf8(text, 2000)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-8")) <= 2000 for c in chunks)
    assert chunks[0].endswith("\n") and len(chunks) == 3
    assert split_utf8("", 10) == [""]


def test_send_text_payload_and_success(tmp_path):
    w, s = make(tmp_path)
    assert w.send_text("RSIQS 连通测试") is True
    assert s.posts == [{"msgtype": "text", "text": {"content": "RSIQS 连通测试"}}]


def test_send_text_chunks_long_chinese(tmp_path):
    w, s = make(tmp_path)
    assert w.send_text("股" * 1500) is True  # 4500 字节 -> 3 段
    assert len(s.posts) == 3
    assert all(len(p["text"]["content"].encode("utf-8")) <= 2000 for p in s.posts)


def test_send_markdown_payload_and_4000_limit(tmp_path):
    w, s = make(tmp_path)
    assert w.send_markdown("**粗**" + "字" * 2000) is True
    assert s.posts[0]["msgtype"] == "markdown"
    assert len(s.posts) == 2
    assert all(len(p["markdown"]["content"].encode("utf-8")) <= 4000 for p in s.posts)


def test_retry_then_success(tmp_path):
    w, s = make(tmp_path, [ConnectionError("x"), Resp(body={"errcode": 45009, "errmsg": "freq"})])
    assert w.send_text("hi") is True
    assert len(s.posts) == 3
    assert not (tmp_path / "logs" / "notify_failed.jsonl").exists()


def test_final_failure_logs_and_prefixes_next_message_once(tmp_path):
    w, s = make(tmp_path, [Resp(500), Resp(500), ConnectionError(f"bad {URL}")])
    assert w.send_text("第一条") is False
    log = tmp_path / "logs" / "notify_failed.jsonl"
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["msgtype"] == "text" and rec["content"] == "第一条"
    assert "SECRET123" not in log.read_text(encoding="utf-8")

    assert w.send_text("第二条") is True
    assert s.posts[-1]["text"]["content"] == FAIL_PREFIX + "第二条"
    assert w.send_text("第三条") is True
    assert s.posts[-1]["text"]["content"] == "第三条"


def test_null_notifier_records():
    n = NullNotifier()
    assert n.send_text("a") is True
    assert n.send_markdown("**b**") is True
    assert n.sent == ["a", "**b**"]
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_wecom.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'notify.wecom'`。

**Step 3: 最小实现**

一条消息超长时按段发送；任一段 3 次都失败就记日志、置标志、返回 False，剩余段不再发送。前缀加在整条消息前面，然后再分段，保证首段也不超限。

```python
# notify/wecom.py
"""企业微信群机器人推送：按 UTF-8 字节分段、重试、失败落盘并在下一条补报。"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TEXT_LIMIT = 2000
MARKDOWN_LIMIT = 4000
ATTEMPTS = 3
WAIT = 2.0
FAIL_PREFIX = "⚠️ 上条推送失败，详见本地日志\n"


def _nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def split_utf8(text: str, limit: int) -> list[str]:
    """按行贪心装箱，每段 <= limit 字节；单行超长时按字符硬切（不会切断多字节字符）。"""
    pieces: list[str] = []
    for line in text.splitlines(keepends=True):
        while _nbytes(line) > limit:
            cut, size = 0, 0
            while size + _nbytes(line[cut]) <= limit:
                size += _nbytes(line[cut])
                cut += 1
            pieces.append(line[:cut])
            line = line[cut:]
        if line:
            pieces.append(line)
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and _nbytes(cur) + _nbytes(p) > limit:
            chunks.append(cur)
            cur = ""
        cur += p
    chunks.append(cur)
    return chunks


def _redact(s: str) -> str:
    return re.sub(r"key=[^&\s'\"]+", "key=***", s)


class WeCom:
    def __init__(self, url: str, session=None, log_dir: Path = Path("logs")):
        self.url = url
        self.session = session or requests.Session()
        self.log_dir = Path(log_dir)
        self.failed = False  # 上一条是否最终失败

    def send_text(self, content: str) -> bool:
        return self._send("text", content, TEXT_LIMIT)

    def send_markdown(self, content: str) -> bool:
        return self._send("markdown", content, MARKDOWN_LIMIT)

    def _send(self, msgtype: str, content: str, limit: int) -> bool:
        body = (FAIL_PREFIX + content) if self.failed else content
        for chunk in split_utf8(body, limit):
            err = self._post({"msgtype": msgtype, msgtype: {"content": chunk}})
            if err is not None:
                self._log_failure(msgtype, content, err)
                return False
        self.failed = False
        return True

    def _post(self, payload: dict) -> str | None:
        """成功返回 None，否则返回最后一次的错误描述。"""
        err = "unknown"
        for i in range(ATTEMPTS):
            try:
                r = self.session.post(self.url, json=payload, timeout=10)
                if r.status_code == 200 and r.json().get("errcode") == 0:
                    return None
                err = f"HTTP {r.status_code}: {r.json() if r.status_code == 200 else ''}"
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            log.warning("推送第 %d/%d 次失败: %s", i + 1, ATTEMPTS, _redact(err))
            if i < ATTEMPTS - 1:
                time.sleep(WAIT)
        return err

    def _log_failure(self, msgtype: str, content: str, err: str) -> None:
        self.failed = True
        self.log_dir.mkdir(parents=True, exist_ok=True)
        rec = {"time": datetime.now().isoformat(timespec="seconds"), "msgtype": msgtype,
               "content": content, "error": _redact(err)}
        with open(self.log_dir / "notify_failed.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class NullNotifier:
    """测试与 dry-run 用：不发网络请求，只记录消息。"""

    def __init__(self):
        self.sent: list[str] = []

    def send_text(self, content: str) -> bool:
        self.sent.append(content)
        return True

    def send_markdown(self, content: str) -> bool:
        self.sent.append(content)
        return True
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_wecom.py -v
```

预期：8 passed。

**Step 5: 手工冒烟（Manual smoke step，需联网和真实 webhook）**

先确认 `.env` 中已填写 `WECOM_WEBHOOK_URL`（`.env` 不进 git），然后执行：

```
uv run python -c "from config import get_settings; from notify.wecom import WeCom; print(WeCom(get_settings().wecom_webhook_url).send_text('RSIQS 连通测试'))"
```

预期：终端打印 `True`，企业微信群收到"RSIQS 连通测试"。打印 `False` 时查看 `logs/notify_failed.jsonl`（webhook key 已脱敏）。

**Step 6: 提交**

```
git add notify/wecom.py tests/test_wecom.py
git commit -m "feat: add WeCom webhook notifier with byte chunking, retry and failure log"
```

（提交信息末尾追加 `Co-Authored-By` trailer。）

### Task 3.4: 消息格式化（notify/format.py）

**Files:**
- Create: `notify/format.py`
- Test: `tests/test_format.py`

**Step 1: 写失败测试**

```python
# tests/test_format.py
from datetime import date

from engine.types import Pick
from notify.format import format_alert, format_daily_report, format_recommendation

P = Pick(code="600000", name="浦发银行", score=1.23, entry_max=10.30,
         stop=9.31, target=11.00, hold_days=5, reason="20日涨幅12.0%")


def test_recommendation_reviewed():
    s = format_recommendation(date(2026, 10, 16), [P], llm_reviewed=True, market_view="震荡偏强")
    assert "2026-10-16" in s
    assert "600000 浦发银行" in s
    assert "≤10.30" in s and "止损 9.31" in s and "止盈 11.00" in s and "持有 5 日" in s
    assert "20日涨幅12.0%" in s
    assert "震荡偏强" in s
    assert "未经 LLM 审核" not in s
    assert "不构成投资建议" in s


def test_recommendation_unreviewed_and_empty():
    s = format_recommendation(date(2026, 10, 16), [], llm_reviewed=False)
    assert "未经 LLM 审核" in s
    assert "今日无推荐" in s


def test_daily_report():
    rows = [{"account": "h:active", "equity": 512345.678, "day_ret": 0.0123, "total_ret": 0.0247},
            {"account": "bench:csi300", "equity": 498000.0, "day_ret": -0.004, "total_ret": -0.004}]
    s = format_daily_report(date(2026, 10, 16), rows, "今日复盘要点")
    assert "2026-10-16" in s
    assert "h:active" in s and "512,345.68" in s and "+1.23%" in s and "+2.47%" in s
    assert "-0.40%" in s
    assert "今日复盘要点" in s
    assert "复盘" in format_daily_report(date(2026, 10, 16), rows, "")


def test_alert():
    s = format_alert("触发止损", "600000 浦发银行 现价 9.30")
    assert s.startswith("【RSIQS 告警】触发止损")
    assert "600000 浦发银行 现价 9.30" in s
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_format.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'notify.format'`。

**Step 3: 最小实现**

`format_alert` 返回纯文本（`jobs` 用 `send_text` 发送）；另外两个返回企业微信 markdown。

```python
# notify/format.py
"""推送消息模板。"""
from datetime import date

from engine.types import Pick

DISCLAIMER = "> 模拟盘信号，仅供研究，不构成投资建议。"


def format_recommendation(d: date, picks: list[Pick], llm_reviewed: bool, market_view: str = "") -> str:
    lines = [f"## RSIQS 盘前推荐 {d.isoformat()}"]
    if not llm_reviewed:
        lines.append("<font color=\"warning\">未经 LLM 审核（已退回纯量化推荐）</font>")
    if market_view:
        lines.append(f"**市场观点**：{market_view}")
    if not picks:
        lines.append("今日无推荐。")
    for i, p in enumerate(picks, 1):
        lines.append(f"**{i}. {p.code} {p.name}**  买入 ≤{p.entry_max:.2f} | 止损 {p.stop:.2f} | "
                     f"止盈 {p.target:.2f} | 持有 {p.hold_days} 日")
        if p.reason:
            lines.append(f"> {p.reason}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_daily_report(d: date, rows: list[dict], review_summary: str) -> str:
    lines = [f"## RSIQS 日报 {d.isoformat()}", "| 账户 | 净值 | 当日 | 累计 |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['account']} | {r['equity']:,.2f} | {r['day_ret']:+.2%} | {r['total_ret']:+.2%} |")
    lines.append("**复盘**：" + (review_summary or "（本日无 LLM 复盘）"))
    return "\n".join(lines)


def format_alert(title: str, body: str) -> str:
    return f"【RSIQS 告警】{title}\n{body}"
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_format.py -v
```

预期：4 passed。

**Step 5: 提交**

```
git add notify/format.py tests/test_format.py
git commit -m "feat: add notification message formatters"
```

（提交信息末尾追加 `Co-Authored-By` trailer。）

### Task 3.5: 策略接口与基线动量策略（strategies/base.py, strategies/baseline_momo.py）

**Files:**
- Create: `strategies/base.py`
- Create: `strategies/baseline_momo.py`
- Test: `tests/test_strategies.py`

**Step 1: 写失败测试**

合成 DataView：80 个交易日，构造 4 只正常股票（不同的 20 日、5 日涨幅）、1 只 ST、1 只成交额不足、1 只历史不足、1 只 asof 收于涨停。

```python
# tests/test_strategies.py
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.dataview import DataView
from engine.types import Pick
from strategies.base import list_strategies, load_strategy
from strategies.baseline_momo import STRATEGY, BaselineMomo

DATES = pd.bdate_range("2026-01-05", periods=80)
ASOF = DATES[-1].date()


def series(closes, amount=2e8, dates=DATES):
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes,
                       "volume": 1e5, "amount": amount}, index=dates[-len(closes):])
    df["prev_close"] = df["close"].shift(1).fillna(df["open"])
    return df


def path(r20, r5, n=80):
    """收盘价路径：前段平，最后 20 天总涨幅 r20，其中最后 5 天涨幅 r5。"""
    c = np.full(n, 10.0)
    mid = 10.0 * (1 + r20) / (1 + r5)
    c[-21:-5] = np.linspace(10.0, mid, 16)
    c[-6:] = np.linspace(mid, mid * (1 + r5), 6)
    c[-21] = 10.0
    return c


def limit_up_path(n=80):
    c = np.full(n, 10.0)
    c[-1] = 11.0  # 昨收 10.00，主板涨停价 11.00
    return c


@pytest.fixture
def view():
    panel = {
        "600001": series(path(0.20, -0.02)),  # 20d 最强、5d 最弱 -> 分数最高
        "600002": series(path(0.15, 0.00)),
        "600003": series(path(0.05, 0.05)),
        "600004": series(path(0.10, 0.10)),
        "600005": series(path(0.30, -0.05)),  # ST
        "600006": series(path(0.30, -0.05), amount=5e7),  # 成交额不足
        "600007": series(path(0.30, -0.05, n=40)),  # 历史不足 60 行
        "600008": series(limit_up_path()),  # asof 收于涨停
        "600009": series(path(0.30, -0.05, n=79), dates=DATES[:-1]),  # asof 当日停牌
    }
    names = {c: "股票" + c[-1] for c in panel}
    names["600005"] = "ST某某"
    return DataView(panel, names, asof=ASOF)


def test_path_helper():
    c = path(0.20, -0.02)
    assert c[-1] / c[-21] - 1 == pytest.approx(0.20)
    assert c[-1] / c[-6] - 1 == pytest.approx(-0.02)


def test_filters_and_ordering(view):
    picks = STRATEGY.select(view)
    assert [p.code for p in picks] == ["600001", "600002", "600003", "600004"]
    assert [p.score for p in picks] == [3.0, 1.0, -2.0, -2.0]  # 同分按代码升序


def test_pick_price_fields(view):
    p = STRATEGY.select(view)[0]
    assert isinstance(p, Pick)
    assert p.name == "股票1"
    assert p.entry_max == pytest.approx(12.36)
    assert p.stop == pytest.approx(11.16)
    assert p.target == pytest.approx(13.20)
    assert p.hold_days == 5
    assert "20日涨幅20.0%" in p.reason and "5日涨幅-2.0%" in p.reason


def test_top5_cap():
    panel = {f"60010{i}": series(path(0.01 * (i + 1), 0.0)) for i in range(8)}
    v = DataView(panel, {c: "甲" for c in panel}, asof=ASOF)
    picks = BaselineMomo().select(v)
    assert len(picks) == 5
    assert picks[0].code == "600107"


def test_empty_view():
    assert STRATEGY.select(DataView({}, {}, asof=ASOF)) == []


def test_load_and_list_strategies():
    assert load_strategy("baseline_momo") is STRATEGY
    assert STRATEGY.name == "baseline_momo"
    names = list_strategies()
    assert "baseline_momo" in names
    assert not {"base", "sandbox", "__init__"} & set(names)
    for bad in ["../evil", "Base", "ab", "base", "sandbox", "x" * 50]:
        with pytest.raises(ValueError):
            load_strategy(bad)
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_strategies.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'strategies.base'`。

**Step 3: 最小实现**

```python
# strategies/base.py
"""策略统一接口与加载。策略文件 strategies/<name>.py 必须定义模块级 STRATEGY。"""
import importlib
import re
from pathlib import Path
from typing import Protocol

from engine.dataview import DataView
from engine.types import Pick

NAME_RE = re.compile(r"[a-z][a-z0-9_]{2,40}")
RESERVED = {"base", "sandbox", "__init__"}
STRATEGY_DIR = Path(__file__).resolve().parent


class Strategy(Protocol):
    name: str

    def select(self, view: DataView) -> list[Pick]:
        """返回按优先级从高到低排序的推荐。"""
        ...


def _valid(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name)) and name not in RESERVED


def load_strategy(name: str):
    if not _valid(name):
        raise ValueError(f"非法策略名: {name!r}")
    return importlib.import_module(f"strategies.{name}").STRATEGY


def list_strategies() -> list[str]:
    return sorted(p.stem for p in STRATEGY_DIR.glob("*.py") if _valid(p.stem))
```

基线策略只用沙箱白名单内的导入，`check_source` 必须对它返回 `[]`（Task 3.6 有测试）。

```python
# strategies/baseline_momo.py
"""基线：中期动量 + 短期反转。分数 = rank(20日涨幅) - rank(5日涨幅)。"""
import pandas as pd

from engine.dataview import DataView
from engine.rules import is_st, limit_prices
from engine.types import Pick


class BaselineMomo:
    name = "baseline_momo"
    top_n = 5
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code)
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            if h["amount"].iloc[-20:].mean() < self.min_amount:
                continue
            last = h.iloc[-1]
            up, _ = limit_prices(code, name, float(last["prev_close"]))
            if float(last["close"]) >= up:
                continue
            c = h["close"]
            rows.append({"code": code, "name": name, "close": float(c.iloc[-1]),
                         "r20": float(c.iloc[-1] / c.iloc[-21] - 1),
                         "r5": float(c.iloc[-1] / c.iloc[-6] - 1)})
        if not rows:
            return []
        df = pd.DataFrame(rows)
        df["score"] = df["r20"].rank() - df["r5"].rank()
        df = df.sort_values(["score", "code"], ascending=[False, True]).head(self.top_n)
        return [
            Pick(code=r.code, name=r.name, score=float(r.score),
                 entry_max=round(r.close * 1.03, 2), stop=round(r.close * 0.93, 2),
                 target=round(r.close * 1.10, 2), hold_days=5,
                 reason=f"20日涨幅{r.r20:.1%}，5日涨幅{r.r5:.1%}，中期动量+短期回调，收盘{r.close:.2f}")
            for r in df.itertuples(index=False)
        ]


STRATEGY = BaselineMomo()
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_strategies.py -v
```

预期：6 passed。

**Step 5: 提交**

```
git add strategies/base.py strategies/baseline_momo.py tests/test_strategies.py
git commit -m "feat: add strategy protocol, loader and baseline momentum strategy"
```

（提交信息末尾追加 `Co-Authored-By` trailer。）

### Task 3.6: 策略沙箱与评估 CLI（strategies/sandbox.py, engine/run_eval.py）

**Files:**
- Create: `strategies/sandbox.py`
- Create: `engine/run_eval.py`
- Test: `tests/test_sandbox.py`
- Test: `tests/test_run_eval.py`

**Step 1: 写失败测试**

```python
# tests/test_sandbox.py
from pathlib import Path

from strategies.sandbox import check_source

OK = """
import math
import numpy as np
import pandas as pd
from engine.types import Pick
from engine.rules import is_st
from strategies.base import Strategy


class S:
    name = "good_one"

    def __init__(self):
        self.k = 20

    def select(self, view):
        return []


STRATEGY = S()
"""


def bad(code: str) -> list[str]:
    return check_source(code + "\nSTRATEGY = None\n")


def test_allowed_imports_ok():
    assert check_source(OK) == []
    assert bad("import numpy.linalg") == []


def test_baseline_strategy_passes():
    src = (Path(__file__).resolve().parents[1] / "strategies" / "baseline_momo.py").read_text(encoding="utf-8")
    assert check_source(src) == []


def test_import_os_rejected():
    for code in ["import os", "from os import path", "import engine.store",
                 "from engine import ledger", "import subprocess as sp", "from . import base"]:
        v = bad(code)
        assert v and "导入" in v[0], code


def test_open_and_builtins_rejected():
    for code in ["open('x.txt')", "exec('1')", "eval('1')", "compile('1','f','exec')",
                 "__import__('os')", "globals()", "getattr(1, 'real')", "vars()"]:
        assert bad(code), code


def test_dunder_access_rejected():
    for code in ["x = ().__class__.__bases__", "y = __builtins__", "z = (1).__dict__"]:
        assert any("__" in m for m in bad(code)), code


def test_file_io_attributes_rejected():
    for code in ["import pandas as pd\npd.read_csv('a')", "import numpy as np\nnp.load('a')",
                 "df = None\ndf.to_csv('a')"]:
        assert bad(code), code


def test_missing_strategy_rejected():
    v = check_source("import math\nx = 1\n")
    assert v == ["缺少模块级 STRATEGY 赋值"]


def test_syntax_error_rejected():
    v = check_source("def (:\n")
    assert len(v) == 1 and v[0].startswith("语法错误")
```

`run_eval` 测试用临时 sqlite 预置一只股票和基准指数，替换 `get_settings`、`load_strategy`、`evaluate`，只验证组装参数和 JSON 输出。

```python
# tests/test_run_eval.py
import io
import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import engine.run_eval as run_eval
from data.cache import BarCache
from engine.store import Store

DATES = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])


class FakeClient:
    def daily_bars(self, code, start, end):
        return pd.DataFrame({"date": DATES, "open": [10.0, 10.1, 10.2], "high": [10.5] * 3,
                             "low": [9.9] * 3, "close": [10.1, 10.2, 10.3],
                             "volume": [1e5] * 3, "amount": [2e8] * 3})


class FakeReport:
    def to_agent_dict(self):
        return {"strategy": "baseline_momo", "train": {"sharpe": 1.1}, "val": {"sharpe": 0.9},
                "gate_pass": True, "holdout_pass": False}


@pytest.fixture
def captured(tmp_path, monkeypatch):
    db = tmp_path / "data" / "t.sqlite"
    cache = BarCache(db, FakeClient())
    cache.update(["600000"], date(2026, 1, 7))
    cache.set_names({"600000": "浦发银行"})
    cache.save_index("sh000300", pd.Series([4000.0, 4400.0, 3960.0], index=DATES))
    monkeypatch.setattr(run_eval, "get_settings",
                        lambda: SimpleNamespace(db_path="data/t.sqlite", root=tmp_path))
    sentinel = object()
    monkeypatch.setattr(run_eval, "load_strategy", lambda name: sentinel)
    seen = {}

    def fake_evaluate(strategy, name, panel, names, calendar, benchmark_nav, store, params=None):
        seen.update(strategy=strategy, name=name, panel=panel, names=names, calendar=calendar,
                    bench=benchmark_nav, store=store)
        return FakeReport()

    monkeypatch.setattr(run_eval, "evaluate", fake_evaluate)
    seen["sentinel"] = sentinel
    return seen


def test_main_prints_single_json_line(captured):
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == FakeReport().to_agent_dict()

    assert captured["strategy"] is captured["sentinel"]
    assert captured["name"] == "baseline_momo"
    assert list(captured["panel"]) == ["600000"]
    assert captured["names"] == {"600000": "浦发银行"}
    assert captured["calendar"].last() == date(2026, 1, 7)
    assert captured["bench"].tolist() == pytest.approx([1.0, 1.1, 0.99])
    assert isinstance(captured["store"], Store)


def test_main_reports_error_as_json(captured, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("坏了")

    monkeypatch.setattr(run_eval, "evaluate", boom)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert json.loads(out.getvalue()) == {"error": "RuntimeError: 坏了"}


def test_main_usage_error():
    out = io.StringIO()
    assert run_eval.main([], out=out) == 2
    assert "usage" in json.loads(out.getvalue())["error"]


def test_missing_benchmark(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "get_settings",
                        lambda: SimpleNamespace(db_path=str(tmp_path / "empty.sqlite"), root=tmp_path))
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert "sh000300" in json.loads(out.getvalue())["error"]
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_sandbox.py tests/test_run_eval.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'strategies.sandbox'` 和 `No module named 'engine.run_eval'`。

**Step 3: 最小实现**

沙箱只做 AST 静态检查（尽力而为，不是安全边界）。违规信息是中文短句，直接回传给研究 agent。

```python
# strategies/sandbox.py
"""策略源码静态检查：限制导入、危险内建、双下划线访问和文件读写。"""
import ast

ALLOWED_IMPORTS = {"math", "statistics", "numpy", "pandas", "dataclasses", "typing",
                   "engine.types", "engine.dataview", "engine.rules", "strategies.base"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "breakpoint", "input"}
IO_ATTRS = {"to_csv", "to_pickle", "to_parquet", "to_excel", "to_json", "to_hdf", "to_sql",
            "to_feather", "to_html", "to_clipboard", "load", "loadtxt", "save", "savez",
            "savetxt", "fromfile", "tofile", "genfromtxt", "memmap"}


def _allowed(mod: str) -> bool:
    return any(mod == a or mod.startswith(a + ".") for a in ALLOWED_IMPORTS)


def _has_strategy(tree: ast.Module) -> bool:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "STRATEGY" for t in targets):
            return True
    return False


def check_source(src: str) -> list[str]:
    """返回违规列表；空列表表示通过。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"语法错误: {e.msg} (第 {e.lineno} 行)"]
    out: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            out += [f"第 {line} 行: 禁止导入 {a.name}" for a in node.names if not _allowed(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.append(f"第 {line} 行: 禁止相对导入")
            elif not _allowed(node.module or ""):
                out.append(f"第 {line} 行: 禁止导入 {node.module}")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                out.append(f"第 {line} 行: 禁止使用 {node.id}")
            elif node.id.startswith("__"):
                out.append(f"第 {line} 行: 禁止使用双下划线名字 {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                out.append(f"第 {line} 行: 禁止访问双下划线属性 {node.attr}")
            elif node.attr.startswith("read_") or node.attr in IO_ATTRS:
                out.append(f"第 {line} 行: 禁止文件读写 {node.attr}")
    if not _has_strategy(tree):
        out.append("缺少模块级 STRATEGY 赋值")
    return out
```

`run_eval` 只读本地缓存，不触网（`BarCache` 的 client 传 `None`，不调用 `update`）。调用方必须用 `sys.executable -m engine.run_eval <name>` 且 `cwd=root`，不要加 `-I`（`-I` 会把 cwd 移出 `sys.path`，导致找不到 `engine` 包）。stdout 只有一行 JSON，日志走 stderr。

```python
# engine/run_eval.py
"""评估 CLI：python -m engine.run_eval <strategy_name>

在独立子进程中运行，研究 agent 只能看到这里打印的单行 JSON（不含留出集数值）。
"""
import json
import logging
import sys
from datetime import date
from pathlib import Path

from config import get_settings
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.evaluator import evaluate
from engine.store import Store
from strategies.base import load_strategy

BENCHMARK = "sh000300"
HISTORY_START = date(2015, 6, 1)


def run(name: str) -> dict:
    settings = get_settings()
    db = Path(settings.root) / settings.db_path
    store = Store(db)
    cache = BarCache(db, client=None)
    bench = cache.load_index(BENCHMARK)
    if bench.empty:
        raise RuntimeError(f"缓存中没有基准指数 {BENCHMARK}，请先运行 python -m jobs.bootstrap")
    calendar = TradingCalendar(ts.date() for ts in bench.index)
    panel, names = cache.load_panel(cache.codes(), HISTORY_START, calendar.last())
    benchmark_nav = bench / bench.iloc[0]
    report = evaluate(load_strategy(name), name, panel, names, calendar, benchmark_nav, store)
    return report.to_agent_dict()


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({"error": "usage: python -m engine.run_eval <strategy_name>"}), file=out)
        return 2
    try:
        result = run(argv[0])
    except Exception as e:  # noqa: BLE001 - 任何失败都要以 JSON 回传给 agent
        logging.exception("评估 %s 失败", argv[0])
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), file=out)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str), file=out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    raise SystemExit(main())
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_sandbox.py tests/test_run_eval.py -v
```

预期：12 passed（sandbox 8 + run_eval 4）。

然后跑本 Part 全部测试，确认没有互相影响：

```
uv run pytest tests/test_akshare_client.py tests/test_cache.py tests/test_wecom.py tests/test_format.py tests/test_strategies.py tests/test_sandbox.py tests/test_run_eval.py -v
```

预期：50 passed。

**Step 5: 手工冒烟（Manual smoke step，需已完成 bootstrap，不触网）**

```
uv run python -m engine.run_eval baseline_momo
```

预期：stdout 只有一行 JSON，包含 `train`、`val`、`gate_pass`、`holdout_pass`，不包含任何留出集数值。缓存为空时输出 `{"error": "RuntimeError: 缓存中没有基准指数 sh000300..."}`，退出码 1。

**Step 6: 提交**

`engine/run_eval.py` 位于锁定目录。若 `.integrity/manifest.json` 已存在（Part 中已生成过），先重写清单，再一起提交：

```
uv run python -c "from pathlib import Path; from engine.integrity import write_manifest; write_manifest(Path('.'))"
git add strategies/sandbox.py engine/run_eval.py tests/test_sandbox.py tests/test_run_eval.py .integrity/manifest.json
git commit -m "feat: add strategy source sandbox and isolated evaluation CLI"
```

（清单尚不存在时去掉第一行命令和 `git add` 中的 `.integrity/manifest.json`。提交信息末尾追加 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）


---

## Part 4: Claude 接入：盘前推荐与复盘

> 说明（合同在本部分内的取舍）：
> 1. `run_final` 在两个线程里并发调用 `finalize`，两者共享同一个 `Store`（`llm.parse` 会写 `add_llm_usage`）。这要求 Part 2 的 `Store` 用 `sqlite3.connect(..., check_same_thread=False)` 打开并用 `threading.Lock` 串行化访问；Task 4.3 的 `run_final` 测试用真实 `Store(":memory:")` 跨线程验证这一点（若失败报 `SQLite objects created in a thread...`，按此修改 `engine/store.py` 后重写 integrity manifest）。
> 2. 本部分新增的内部名字：`playbook.ROOT`、`playbook.load_prompt(name)`（读 `playbook/<name>_system.md`）、`premarket.FINAL_DEADLINE = 100.0`（run_final 对两路 finalize 的总等待上限，秒；SDK 超时重试可能使单次调用超过 2 分钟，超出上限的那一路直接退回量化，保证 09:28 前推送）。
> 3. `finalize` 中 `spot()` 失败不触发回退，只在提示里告诉 Claude 竞价数据缺失；只有 LLM 调用本身失败才退回量化。
> 4. `maybe_release` 首次调用只记录窗口起点并返回 None；候选与现行规则相同则只重开窗口；落后时重置候选并返回提示文本。
> 5. 价格合理性：`stop < entry_max < target` 且三者都在量化 `entry_max` 的 0.5~1.5 倍内，否则沿用量化价格。
> 6. `run_review` 遇到 `BudgetExceeded` 以外的 LLM 异常（拒答、超时、解析失败）也照常推送日报，摘要写明"复盘失败"，job 记为 ok，避免一次 API 故障让当天日报丢失；每次最多采纳 `review.MAX_LESSONS = 3` 条 lesson。
> 7. `agents.review` 以 `playbook.git_commit(...)` / `playbook.maybe_release(...)` 的模块属性方式调用，便于测试打桩；提交路径固定为 `playbook/active.json`、`playbook/candidate.json`。

前置：Part 1~3 已完成（`config`、`engine.types/store/calendar/ledger/dataview/backtest`、`notify.format`、`data.*`）。

### Task 4.1: agents/llm.py — Claude 调用封装

**Files:**
- Create: `agents/llm.py`
- Create: `scripts/smoke_llm.py`
- Test: `tests/test_llm.py`

**Step 1: 写失败的测试**

`tests/test_llm.py`:

```python
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agents.llm import LLM, BudgetExceeded, LLMRefusal, strict_schema
from config import Settings
from engine.store import Store


class Inner(BaseModel):
    code: str
    score: float


class Outer(BaseModel):
    view: str
    items: list[Inner]
    note: str = ""


GOOD = '{"view": "震荡", "items": [{"code": "600000", "score": 0.7}], "note": ""}'


def make_settings(**kw) -> Settings:
    base = dict(anthropic_api_key="test-key", wecom_webhook_url="", root=Path("."))
    base.update(kw)
    return Settings(**base)


def response(text=GOOD, stop_reason="end_turn", **usage):
    u = dict(input_tokens=1000, output_tokens=500)
    u.update(usage)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking="..."), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(**u),
    )


class FakeClient:
    """模拟 anthropic.Anthropic：with_options(...).messages.create(...)"""

    def __init__(self, resp):
        self.resp = resp
        self.calls: list[dict] = []
        self.options: list[dict] = []

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.calls.append(kw)
        return self.resp


@pytest.fixture
def store():
    return Store(":memory:")


def this_month_cost(store):
    t = dt.date.today()
    return store.month_llm_cost(t.year, t.month)


def test_strict_schema_nested():
    s = strict_schema(Outer)
    assert s["additionalProperties"] is False
    assert sorted(s["required"]) == ["items", "note", "view"]
    inner = s["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert sorted(inner["required"]) == ["code", "score"]


def test_parse_request_kwargs_and_result(store):
    client = FakeClient(response())
    llm = LLM(make_settings(), store, client=client)
    out = llm.parse("unit", "SYS", "USER", Outer, effort="low", timeout=30.0, max_tokens=2000)
    assert isinstance(out, Outer) and out.items[0].code == "600000"
    assert client.options == [{"timeout": 30.0}]
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["max_tokens"] == 2000
    assert kw["system"] == "SYS"
    assert kw["messages"] == [{"role": "user", "content": "USER"}]
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": strict_schema(Outer)}
    assert kw["extra_body"] == {"fallbacks": "default"}
    assert kw["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}
    for banned in ("thinking", "temperature", "tool_choice"):
        assert banned not in kw


def test_usage_recorded(store):
    llm = LLM(make_settings(), store, client=FakeClient(response()))
    llm.parse("unit", "s", "u", Outer)
    # (1000*4 + 500*20) / 1e6 * 7.2
    assert this_month_cost(store) == pytest.approx(0.1008)


def test_cost_cny_with_cache_and_missing_attrs(store):
    llm = LLM(make_settings(), store, client=FakeClient(response()))
    u = SimpleNamespace(input_tokens=1000, output_tokens=500,
                        cache_creation_input_tokens=1000, cache_read_input_tokens=1000)
    # (1000 + 1250 + 100) * 4 / 1e6 + 500 * 20 / 1e6 = 0.0194 USD
    assert llm.cost_cny(u) == pytest.approx(0.0194 * 7.2)
    assert llm.cost_cny(SimpleNamespace()) == 0.0
    assert llm.cost_cny(SimpleNamespace(input_tokens=None, output_tokens=10)) == pytest.approx(10 * 20 / 1e6 * 7.2)


def test_refusal_raises_but_usage_recorded(store):
    llm = LLM(make_settings(), store, client=FakeClient(response(text="", stop_reason="refusal")))
    with pytest.raises(LLMRefusal):
        llm.parse("unit", "s", "u", Outer)
    assert this_month_cost(store) > 0


def test_invalid_json_raises(store):
    llm = LLM(make_settings(), store, client=FakeClient(response(text='{"view": 1}')))
    with pytest.raises(Exception):
        llm.parse("unit", "s", "u", Outer)


def test_budget_exceeded_non_essential(store):
    store.add_llm_usage("old", 0, 0, 300.0)
    client = FakeClient(response())
    llm = LLM(make_settings(monthly_budget_cny=300.0), store, client=client)
    with pytest.raises(BudgetExceeded):
        llm.parse("unit", "s", "u", Outer)
    assert client.calls == []


def test_budget_exceeded_essential_passes(store):
    store.add_llm_usage("old", 0, 0, 300.0)
    llm = LLM(make_settings(monthly_budget_cny=300.0), store, client=FakeClient(response()))
    assert llm.parse("unit", "s", "u", Outer, essential=True).view == "震荡"
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_llm.py -v
```

预期：`ModuleNotFoundError: No module named 'agents.llm'`，全部 ERROR。

**Step 3: 最小实现**

`agents/llm.py`:

```python
"""Claude 调用封装：结构化输出、记账、预算。

Opus 5.5 约束：不传 thinking / temperature / tool_choice；
用 output_config.effort 控制推理深度，output_config.format 做 JSON Schema 约束。
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import anthropic
from pydantic import BaseModel

from config import PRICING_USD_PER_MTOK, Settings
from engine.store import Store

BETA_HEADER = "server-side-fallback-2026-07-01"


class BudgetExceeded(Exception):
    pass


class LLMRefusal(Exception):
    pass


def _strictify(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for v in node.values():
            _strictify(v)
    elif isinstance(node, list):
        for v in node:
            _strictify(v)


def strict_schema(model: type[BaseModel]) -> dict:
    """model_json_schema()，并对每个 object（含 $defs）设 additionalProperties=False、required=全部字段。"""
    schema = copy.deepcopy(model.model_json_schema())
    _strictify(schema)
    return schema


def _tok(usage: Any, name: str) -> int:
    return int(getattr(usage, name, 0) or 0)


class LLM:
    def __init__(self, settings: Settings, store: Store, client=None):
        self.settings = settings
        self.store = store
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)

    def cost_cny(self, usage: Any) -> float:
        price_in, price_out = PRICING_USD_PER_MTOK.get(self.settings.llm_model, PRICING_USD_PER_MTOK["claude-opus-5-5"])
        tokens_in = (_tok(usage, "input_tokens")
                     + _tok(usage, "cache_creation_input_tokens") * 1.25
                     + _tok(usage, "cache_read_input_tokens") * 0.1)
        usd = (tokens_in * price_in + _tok(usage, "output_tokens") * price_out) / 1_000_000
        return usd * self.settings.usd_cny

    def month_cost(self) -> float:
        t = dt.date.today()
        return self.store.month_llm_cost(t.year, t.month)

    def check_budget(self, essential: bool) -> None:
        if not essential and self.month_cost() >= self.settings.monthly_budget_cny:
            raise BudgetExceeded(
                f"本月 LLM 花费 {self.month_cost():.2f} 元已达预算 {self.settings.monthly_budget_cny:.0f} 元")

    def parse(self, purpose: str, system: str, user: str, schema: type[BaseModel], effort: str = "medium",
              timeout: float = 120.0, max_tokens: int = 16000, essential: bool = False) -> BaseModel:
        self.check_budget(essential)
        resp = self.client.with_options(timeout=timeout).messages.create(
            model=self.settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": strict_schema(schema)}},
            extra_headers={"anthropic-beta": BETA_HEADER},
            extra_body={"fallbacks": "default"},
        )
        usage = getattr(resp, "usage", None)
        self.store.add_llm_usage(purpose, _tok(usage, "input_tokens"), _tok(usage, "output_tokens"),
                                 self.cost_cny(usage))
        if resp.stop_reason == "refusal":
            raise LLMRefusal(f"{purpose}: Claude 拒绝回答")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise ValueError(f"{purpose}: 响应中没有 text 块 (stop_reason={resp.stop_reason})")
        return schema.model_validate_json(text)
```

说明：`month_cost` 是内部辅助方法；`max_tokens` 截断（`stop_reason=="max_tokens"`）时 JSON 不完整，`model_validate_json` 抛 `ValidationError`，调用方按普通异常处理。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_llm.py -v
```

预期：8 passed。

**Step 5: Manual smoke step（真实调用一次 Claude，约 0.05 元）**

`scripts/smoke_llm.py`:

```python
"""手动冒烟：真实调用 Claude 一次，验证 output_config / fallbacks / beta header 被 API 接受。
运行：uv run python scripts/smoke_llm.py
"""
from pydantic import BaseModel

from agents.llm import LLM
from config import get_settings
from engine.store import Store


class Ping(BaseModel):
    answer: str
    numbers: list[int]


def main() -> None:
    s = get_settings()
    llm = LLM(s, Store(s.root / s.db_path))
    out = llm.parse("smoke", "你是测试助手，只输出要求的 JSON。", "回答'好'，并列出 1 到 3。",
                    Ping, effort="low", timeout=60.0, max_tokens=500, essential=True)
    print(out)
    assert out.numbers == [1, 2, 3], out


if __name__ == "__main__":
    main()
```

```
uv run python scripts/smoke_llm.py
```

预期打印 `answer='好' numbers=[1, 2, 3]`。若 API 报 400：先检查 `extra_body` 的 `fallbacks` 与 beta header 是否被该账号支持（可临时去掉两者确认是否为根因），再看 `output_config` 字段名，修正 `agents/llm.py` 后同步改 `test_parse_request_kwargs_and_result`。

**Step 6: 提交**

```
git add agents/llm.py tests/test_llm.py scripts/smoke_llm.py
git commit -m "feat: add Claude LLM wrapper with strict schema, usage and budget"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）

### Task 4.2: agents/playbook.py — 经验手册与系统提示词

**Files:**
- Create: `agents/playbook.py`
- Create: `playbook/premarket_system.md`
- Create: `playbook/review_system.md`
- Test: `tests/test_playbook.py`

`apply_lessons` 只依赖 lesson 的 `action/rule_id/text/evidence` 属性（鸭子类型），避免与 `agents.review` 循环导入；测试用 `SimpleNamespace` 构造。`git_commit` 返回 True 表示确实产生了提交；暂存区无变化也返回 False。

**Step 1: 写失败的测试**

`tests/test_playbook.py`:

```python
import datetime as dt
import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from agents import playbook
from engine.calendar import TradingCalendar
from engine.store import Store

TODAY = dt.date(2026, 10, 16)


def L(action, text="", evidence="2026-10-15 600000 高开 6% 后回落 -4%", rule_id=""):
    return SimpleNamespace(action=action, rule_id=rule_id, text=text, evidence=evidence)


@pytest.fixture
def pbdir(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def cal():
    return TradingCalendar(pd.bdate_range("2026-08-03", "2026-12-31").date)


def test_load_missing_returns_empty_and_roundtrip(pbdir):
    pb = playbook.load("active")
    assert pb["version"] == 1 and pb["rules"] == []
    pb["rules"].append({"id": "r1", "text": "规则", "evidence": "证据", "created": "2026-10-01"})
    playbook.save("candidate", pb)
    assert json.loads((pbdir / "candidate.json").read_text(encoding="utf-8")) == pb
    assert playbook.load("candidate") == pb
    with pytest.raises(ValueError):
        playbook.load("other")


def test_render():
    assert "暂无" in playbook.render({"version": 1, "rules": []})
    text = playbook.render({"version": 3, "rules": [
        {"id": "r1", "text": "高开超过 5% 否决", "evidence": "e1", "created": "x"},
        {"id": "r2", "text": "竞价量能不足否决", "evidence": "e2", "created": "x"}]})
    assert text.splitlines()[0].startswith("1. [r1] 高开超过 5% 否决")
    assert text.splitlines()[1].startswith("2. [r2]")


def test_apply_add_remove_merge_and_evidence_required():
    pb = {"version": 1, "created": "2026-10-01", "rules": []}
    pb = playbook.apply_lessons(pb, [L("add", "规则A"), L("add", "规则B"), L("add", "无证据", evidence="  ")], TODAY)
    assert [r["id"] for r in pb["rules"]] == ["r1", "r2"]
    assert pb["rules"][0]["created"] == TODAY.isoformat()
    pb = playbook.apply_lessons(pb, [L("merge", "规则A+B 合并", rule_id="r1"), L("remove", rule_id="r2"),
                                     L("remove", rule_id="r99"), L("remove", rule_id="r1", evidence="")], TODAY)
    assert [(r["id"], r["text"]) for r in pb["rules"]] == [("r1", "规则A+B 合并")]
    pb = playbook.apply_lessons(pb, [L("add", "规则C")], TODAY)
    assert pb["rules"][-1]["id"] == "r2"  # 新 id = 现存最大编号 + 1


def test_apply_does_not_mutate_input():
    pb = {"version": 1, "created": "2026-10-01", "rules": []}
    playbook.apply_lessons(pb, [L("add", "规则A")], TODAY)
    assert pb["rules"] == []


def test_caps_drop_oldest():
    old = [{"id": f"r{i}", "text": f"旧规则{i}", "evidence": "e", "created": f"2026-09-{i:02d}"}
           for i in range(1, 31)]
    pb = playbook.apply_lessons({"version": 1, "created": "x", "rules": old}, [L("add", "新规则")], TODAY)
    assert len(pb["rules"]) == playbook.MAX_RULES
    assert "r1" not in [r["id"] for r in pb["rules"]]
    assert pb["rules"][-1]["text"] == "新规则"

    big = [L("add", "长" * 2500, evidence="e") for _ in range(3)]
    pb = playbook.apply_lessons({"version": 1, "created": "x", "rules": []}, big, TODAY)
    assert sum(len(r["text"]) + len(r["evidence"]) for r in pb["rules"]) <= playbook.MAX_CHARS
    assert [r["id"] for r in pb["rules"]] == ["r2", "r3"]


def _rules(*texts):
    return [{"id": f"r{i}", "text": t, "evidence": "e", "created": "2026-09-01"} for i, t in enumerate(texts, 1)]


def _setup_release(pbdir, cal, cand_end, act_end, start=dt.date(2026, 9, 1)):
    store = Store(":memory:")
    playbook.save("active", {"version": 2, "created": "2026-09-01", "rules": _rules("旧")})
    playbook.save("candidate", {"version": 2, "created": "2026-09-01", "rules": _rules("旧", "新")})
    store.kv_set("playbook_released", start.isoformat())
    days = cal.between(start, TODAY)
    for i, d in enumerate(days):
        frac = i / (len(days) - 1)
        store.record_nav("h:candidate", d, 500_000 * (1 + (cand_end - 1) * frac))
        store.record_nav("h:active", d, 500_000 * (1 + (act_end - 1) * frac))
    return store


def test_maybe_release_first_call_starts_window(pbdir, cal):
    store = Store(":memory:")
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def test_maybe_release_too_early(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.10, 1.00, start=dt.date(2026, 10, 12))
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert playbook.load("active")["version"] == 2


def test_maybe_release_promotes(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    msg = playbook.maybe_release(store, TODAY, cal)
    assert msg and "v3" in msg
    act, cand = playbook.load("active"), playbook.load("candidate")
    assert act["version"] == 3 and [r["text"] for r in act["rules"]] == ["旧", "新"]
    assert cand == act
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def test_maybe_release_resets_when_worse(pbdir, cal):
    store = _setup_release(pbdir, cal, 0.98, 1.02)
    msg = playbook.maybe_release(store, TODAY, cal)
    assert msg and "重置" in msg
    act, cand = playbook.load("active"), playbook.load("candidate")
    assert act["version"] == 2 and cand == act
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def test_maybe_release_identical_rules_only_restarts_window(pbdir, cal):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    playbook.save("candidate", playbook.load("active"))
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert playbook.load("active")["version"] == 2
    assert store.kv_get("playbook_released") == TODAY.isoformat()


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def test_git_commit_in_tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "ROOT", tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "tester")
    (tmp_path / "playbook").mkdir()
    (tmp_path / "playbook" / "candidate.json").write_text("{}", encoding="utf-8")
    assert playbook.git_commit(["playbook/candidate.json"], "playbook: 复盘更新") is True
    log = _git(tmp_path, "log", "-1", "--format=%B")
    assert log.startswith("playbook: 复盘更新")
    assert "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>" in log
    assert playbook.git_commit(["playbook/candidate.json"], "nothing changed") is False


def test_git_commit_outside_repo_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "ROOT", tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    assert playbook.git_commit(["x.txt"], "msg") is False


def test_prompt_files_exist():
    for name in ("premarket", "review"):
        text = playbook.load_prompt(name)
        assert len(text) > 500 and "证据" in text
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_playbook.py -v
```

预期：`ImportError: cannot import name 'playbook' from 'agents'`（模块不存在），全部 ERROR。

**Step 3: 最小实现**

`agents/playbook.py`:

```python
"""LLM 层经验手册：active（线上）与 candidate（影子）两份，复盘只改 candidate。"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

import pandas as pd

from engine.calendar import TradingCalendar
from engine.store import Store

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK_DIR = ROOT / "playbook"
KINDS = ("active", "candidate")
MAX_RULES = 30
MAX_CHARS = 6000
MIN_DAYS_BETWEEN_RELEASES = 10
RELEASE_KEY = "playbook_released"
TRAILER = "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"


def _path(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown playbook kind: {kind}")
    return PLAYBOOK_DIR / f"{kind}.json"


def load(kind: str) -> dict:
    p = _path(kind)
    if not p.exists():
        return {"version": 1, "created": dt.date.today().isoformat(), "rules": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save(kind: str, pb: dict) -> None:
    p = _path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pb, ensure_ascii=False, indent=2), encoding="utf-8")


def load_prompt(name: str) -> str:
    return (PLAYBOOK_DIR / f"{name}_system.md").read_text(encoding="utf-8")


def render(pb: dict) -> str:
    if not pb.get("rules"):
        return "（暂无经验规则）"
    return "\n".join(f"{i}. [{r['id']}] {r['text']}（证据：{r['evidence']}）"
                     for i, r in enumerate(pb["rules"], 1))


def _next_id(rules: list[dict]) -> str:
    nums = [int(m.group(1)) for r in rules if (m := re.fullmatch(r"r(\d+)", r["id"]))]
    return f"r{max(nums, default=0) + 1}"


def _size(rules: list[dict]) -> int:
    return sum(len(r["text"]) + len(r["evidence"]) for r in rules)


def apply_lessons(pb: dict, lessons: list, today: dt.date) -> dict:
    """lesson 需有 action/rule_id/text/evidence 属性。返回新 dict，不改入参。"""
    out = copy.deepcopy(pb)
    rules: list[dict] = out.setdefault("rules", [])
    for les in lessons:
        evidence = (les.evidence or "").strip()
        text = (les.text or "").strip()
        if not evidence:
            continue
        idx = next((i for i, r in enumerate(rules) if r["id"] == les.rule_id), None)
        if les.action == "add" and text:
            rules.append({"id": _next_id(rules), "text": text, "evidence": evidence,
                          "created": today.isoformat()})
        elif les.action == "remove" and idx is not None:
            rules.pop(idx)
        elif les.action == "merge" and idx is not None and text:
            rules[idx] = {**rules[idx], "text": text, "evidence": evidence}
    while rules and (len(rules) > MAX_RULES or _size(rules) > MAX_CHARS):
        rules.pop(0)  # 列表按加入顺序排列，最前面的最旧
    return out


def _window_return(store: Store, account: str, start: dt.date, end: dt.date) -> float | None:
    nav = store.nav_series(account)
    if nav.empty:
        return None
    nav = nav.loc[pd.Timestamp(start):pd.Timestamp(end)]
    if len(nav) < 2:
        return None
    return float(nav.iloc[-1] / nav.iloc[0] - 1.0)


def maybe_release(store: Store, today: dt.date, calendar: TradingCalendar) -> str | None:
    """每 MIN_DAYS_BETWEEN_RELEASES 个交易日最多发布一次 candidate。"""
    last = store.kv_get(RELEASE_KEY)
    if last is None:
        store.kv_set(RELEASE_KEY, today.isoformat())
        return None
    start = dt.date.fromisoformat(last)
    if len(calendar.between(start, today)) - 1 < MIN_DAYS_BETWEEN_RELEASES:
        return None
    active, cand = load("active"), load("candidate")
    if cand.get("rules") == active.get("rules"):
        store.kv_set(RELEASE_KEY, today.isoformat())
        return None
    r_cand = _window_return(store, "h:candidate", start, today)
    r_act = _window_return(store, "h:active", start, today)
    if r_cand is None or r_act is None:
        return None  # 净值不足，继续观察
    store.kv_set(RELEASE_KEY, today.isoformat())
    if r_cand >= r_act:
        new = {"version": int(active.get("version", 1)) + 1, "created": today.isoformat(),
               "rules": copy.deepcopy(cand["rules"])}
        save("active", new)
        save("candidate", new)
        return (f"playbook v{new['version']} 上线：候选 {r_cand:+.2%} vs 现行 {r_act:+.2%}"
                f"（{start.isoformat()} 至 {today.isoformat()}），共 {len(new['rules'])} 条规则")
    save("candidate", copy.deepcopy(active))
    return (f"playbook 候选落后（{r_cand:+.2%} vs 现行 {r_act:+.2%}），已重置为现行 v{active.get('version', 1)}")


def git_commit(paths: list[str], message: str) -> bool:
    """git add + commit（附 trailer）。失败或无变化返回 False，不抛异常。"""
    try:
        subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True, capture_output=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *paths], cwd=ROOT,
                                capture_output=True)
        if staged.returncode == 0:
            return False
        subprocess.run(["git", "commit", "-m", f"{message}\n\n{TRAILER}", "--", *paths], cwd=ROOT,
                       check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False
```

注意：`maybe_release` 的晋升/重置都会写 `active.json`、`candidate.json`，调用方（`run_review`）负责之后 `git_commit`。`git_commit` 中 `git commit -- <paths>` 只提交这些路径，不会带上用户暂存区里的其它改动。

`playbook/premarket_system.md`（agent 可改；以下为初始版本）:

```markdown
# 角色

你是一名 A 股盘前风控审核员。量化模型已在昨日收盘后给出候选股票（中期动量 + 短期反转），你的任务是在 9:26 集合竞价结束后、开盘前，结合隔夜新闻、公司公告和竞价数据，对每只候选做出"保留（keep）"或"否决（veto）"的判断，并给出买入上限、止损和止盈价。你不是选股者：你不能新增候选之外的股票，你的价值在于剔除量化模型看不到的风险。

# A 股交易规则（必须遵守）

- T+1：今天买入的股票最早下一个交易日才能卖出，因此当天买入后无法止损，必须对开盘后当日下跌有承受力。
- 涨跌幅限制：主板 ±10%，创业板（300/301）与科创板（688/689）±20%，ST 股 ±5%，北交所 ±30%。价格精确到 0.01 元。
- 开盘即涨停（一字板）的股票模拟盘不可买入；跌停封死无法卖出。
- 成交按开盘价加滑点模拟，买入 100 股整数倍；佣金万 2.5（最低 5 元）、卖出印花税 0.05%、过户费 0.001%。持有期内到期按开盘价卖出。

# 否决标准（满足任一条即 veto，并在 reason 中写明依据）

1. 利空公告或新闻：减持计划、业绩预亏/大幅下修、立案调查、监管问询或处罚、重大诉讼、商誉减值、解禁临近、退市风险提示、控股股东质押爆仓等。
2. 高开过多：竞价高开幅度超过该板块涨跌幅限制的一半（主板约 +5%，创业板/科创板约 +10%），追高后 T+1 无法止损，赔率差。
3. 竞价量能不足：竞价成交额相对近期日均成交额明显偏低，或低开且无承接，说明资金不认可。
4. 竞价价已接近涨停（距涨停价不足 2%）：上行空间有限，且开盘可能直接封板买不进。
5. 题材已退潮：所属板块隔夜出现明确负面政策或龙头股大跌。
6. 下方"经验规则"中适用的条目。

没有明确负面证据时默认 keep。不要因为"没有新闻"而否决，也不要因为"有利好新闻"就提高信心而放松价格纪律。

# 价格纪律

- entry_max：可接受的最高买入价。模拟盘以开盘价成交，开盘价高于 entry_max 即放弃买入。通常设在竞价价上方 0~2%，一般不高于量化给出的 entry_max（如需放宽须在 reason 中说明理由）。
- stop：止损价，须低于 entry_max，一般在 entry_max 下方 5%~8%（ST 股更窄）。
- target：止盈价，须高于 entry_max，一般在上方 8%~15%，不超过持有期内合理空间。
- 必须满足 stop < entry_max < target，否则系统将忽略你的价格并使用量化价格。

# 证据与诚信

- 每条判断必须引用输入中出现的具体证据：公告标题、新闻标题与时间、竞价涨幅或成交额数字、或经验规则编号（如 [r3]）。
- 严禁编造新闻、公告、数据或你在输入中没有看到的事实。你的训练数据有截止日期，不要使用记忆中的"近期事件"判断个股；输入中没有的信息视为未知。
- 信息不足时直说"无相关新闻/公告"，并主要依据竞价数据与规则判断。

# 输出要求

- 只输出符合给定 JSON Schema 的对象，不要输出其它文字。
- market_view：两三句话的大盘判断（隔夜外盘、重大新闻、整体竞价情绪），并说明今天是否应整体降低仓位。
- picks：对每一只候选都给出一条记录，code 与候选完全一致，不得遗漏或新增。
- confidence：0 到 1 的数，表示你对该股在持有期内跑赢沪深 300 的把握，用于排序；veto 的股票填 0。
- reason：中文，60 字以内，先写结论，再写证据。
```

`playbook/review_system.md`（agent 可改；以下为初始版本）:

```markdown
# 角色

你是一名 A 股盘后复盘分析师，负责维护"盘前审核经验手册"（playbook）。每个交易日收盘后，你会拿到两套 LLM 审核账户（h:active 线上版、h:candidate 候选版）当天的推荐、实际成交、个股日线以及当前候选版经验规则。你的任务是：总结当天推荐的得失，并对候选版经验规则提出增、删、合并建议。线上版只有在候选版经过约两周前向模拟、净值不落后时才会被替换，所以你的修改都是"待验证假设"。

# 背景规则

- T+1：当日买入次日才能卖出；涨跌幅主板 ±10%，创业板/科创板 ±20%，ST ±5%，北交所 ±30%。
- 开盘一字涨停买不进、一字跌停卖不出；成交按开盘价加滑点模拟。
- 推荐若因开盘价高于 entry_max 或开盘涨停而未成交，也属于需要复盘的事件（价格设置过紧还是本就该放弃）。

# 复盘方法

1. 逐只对比：推荐理由 vs 当日实际（开盘价相对昨收、最高、最低、收盘、是否成交、是否触发止损/止盈）。
2. 区分运气与规律：单日单只的结果大多是噪声。只有当现象能用明确、可执行的条件描述（如"竞价高开超过 5% 且竞价成交额低于近 20 日均额 3% 的主板股"），并且今天的数据提供了支持时，才提出新规则。
3. 优先修正而不是堆砌：已有规则被今天的事实反驳时用 remove；两条规则重叠时用 merge（rule_id 填被保留的那条，text 写合并后的完整表述）。
4. 规则必须可在盘前执行：只能依赖盘前可得的信息（昨日及以前行情、隔夜新闻公告、竞价数据），不能依赖当日收盘后才知道的信息。

# 证据要求

- 每条 lesson 的 evidence 必须写明日期、股票代码、关键数字和结果，例如："2026-10-15 600000 竞价高开 6.2%，开盘买入后收盘 -3.1%"。没有证据的 lesson 会被系统丢弃。
- 严禁编造输入中没有的行情、新闻或成交。没有可学的内容时 lessons 返回空列表，这是正常结果。
- 每次最多提出 3 条 lesson。手册总长度有上限，超出时最旧的规则会被自动淘汰。

# 输出要求

- 只输出符合给定 JSON Schema 的对象。
- summary：中文，200 字以内，写给用户看的当日复盘：两个账户各自的表现、最大的得失、明天需要注意什么。不写空话。
- lessons：action 为 add / remove / merge；add 时 rule_id 为空字符串；remove 时 text 可为空字符串但 evidence 仍需填写理由。
```

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_playbook.py -v
```

预期：13 passed（`test_git_commit_*` 需要本机 PATH 中有 git）。

**Step 5: 提交**

```
git add agents/playbook.py playbook/premarket_system.md playbook/review_system.md tests/test_playbook.py
git commit -m "feat: add playbook store, lesson caps, release gate and system prompts"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）

### Task 4.3: agents/premarket.py — 盘前扫描与竞价定稿

**Files:**
- Create: `agents/premarket.py`
- Test: `tests/test_premarket.py`

`scan` 用 `client.universe()` 取股票池，先 `cache.update(codes, asof)` 增量补齐昨日数据，再用最近 `HISTORY_DAYS`（400）个自然日的面板构造 `DataView`。新闻、公告类的单项抓取失败时置空，不影响扫描；行情数据失败照常抛出，由 `run_job` 告警。`context` 里另加 `"stats"`（每只候选的昨收 `prev_close` 和 20 日均成交额 `avg_amount20`），供 Claude 判断竞价量能。`run_final` 的 `calendar` 参数只为与 jobs 的调用签名保持一致，目前没有用到。

**Step 1: 写失败的测试**

`tests/test_premarket.py`:

```python
import datetime as dt
import json
import threading
from pathlib import Path

import pandas as pd
import pytest

from agents import playbook, premarket
from agents.llm import BudgetExceeded, LLMRefusal
from agents.premarket import PremarketReview, ReviewedPick
from config import Settings
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick
from notify.format import format_recommendation

D = dt.date(2026, 10, 16)
PREV = dt.date(2026, 10, 15)
CODES = [f"600{i:03d}" for i in range(20)]
CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)


def settings(**kw) -> Settings:
    base = dict(anthropic_api_key="k", wecom_webhook_url="", root=Path("."), max_picks=3)
    base.update(kw)
    return Settings(**base)


def qpick(code: str, i: int = 0) -> Pick:
    return Pick(code=code, name=f"股{code}", score=100.0 - i, entry_max=10.3, stop=9.3, target=11.0,
                hold_days=5, reason=f"量化理由{code}")


def make_panel(codes, end=PREV, n=25):
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    cols = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 1e6, "amount": 2e8, "prev_close": 10.0}
    return {c: pd.DataFrame(cols, index=idx) for c in codes}, {c: f"股{c}" for c in codes}


class FakeAk:
    def __init__(self, spot_fail=False):
        self.spot_fail = spot_fail
        self.calls = []

    def universe(self):
        return list(CODES)

    def news_global(self, limit=50):
        self.calls.append(("news_global", limit))
        return [{"title": "隔夜美股收涨", "content": "纳指涨 1%", "time": pd.Timestamp("2026-10-16 06:00")}]

    def stock_news(self, code, limit=5):
        return [{"title": f"{code} 新闻{i}", "content": "", "time": "2026-10-15"} for i in range(5)]

    def notices(self, d):
        return pd.DataFrame({"code": ["600000", "999999"], "name": ["股600000", "某股"],
                             "title": ["关于股东减持计划的公告", "无关公告"], "type": ["减持", "其他"]})

    def spot(self):
        if self.spot_fail:
            raise RuntimeError("spot down")
        return pd.DataFrame({"code": CODES, "name": [f"股{c}" for c in CODES], "open": 10.2, "prev_close": 10.0,
                             "last": 10.2, "pct": 2.0, "volume": 1e4, "amount": 5e6})


class FakeCache:
    def __init__(self):
        self.updated = self.loaded = None

    def update(self, codes, end, start_default=dt.date(2015, 6, 1)):
        self.updated = (list(codes), end)

    def load_panel(self, codes, start, end):
        self.loaded = (start, end)
        return make_panel(codes, end)


class FakeStrategy:
    name = "fake"

    def __init__(self):
        self.asof = None

    def select(self, view):
        self.asof = view.asof
        return [qpick(c, i) for i, c in enumerate(sorted(view.codes()))]


class FakeLLM:
    """按 purpose 返回预设结果；值为 Exception 时抛出。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0, max_tokens=16000,
              essential=False):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, essential=essential))
        r = self.results[purpose]
        if isinstance(r, Exception):
            raise r
        return r


class Recorder:
    def __init__(self):
        self.sent = []

    def send_markdown(self, content):
        self.sent.append(content)
        return True

    def send_text(self, content):
        self.sent.append(content)
        return True


@pytest.fixture(autouse=True)
def pbdir(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", tmp_path)
    (tmp_path / "premarket_system.md").write_text("盘前系统提示", encoding="utf-8")
    playbook.save("active", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "ACTIVE规则", "evidence": "e", "created": "x"}]})
    playbook.save("candidate", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "CANDIDATE规则", "evidence": "e", "created": "x"}]})
    return tmp_path


@pytest.fixture
def store():
    s = Store(":memory:")
    s.save_candidates(D, [qpick(c, i) for i, c in enumerate(CODES[:6])], {"news": [], "notices": [],
                                                                          "stock_news": {}, "stats": {}})
    return s


def rp(code, action="keep", conf=0.5, entry=10.25, stop=9.6, target=11.2):
    return ReviewedPick(code=code, action=action, confidence=conf, entry_max=entry, stop=stop, target=target,
                        reason=f"LLM理由{code}")


def test_scan_saves_top_candidates_and_context():
    store, ak, cache, strat = Store(":memory:"), FakeAk(), FakeCache(), FakeStrategy()
    picks = premarket.scan(D, settings(), store, cache, ak, strat, CAL)
    assert len(picks) == premarket.CANDIDATE_COUNT == 15
    assert strat.asof == PREV
    assert cache.updated == (CODES, PREV) and cache.loaded[1] == PREV
    saved, ctx = store.load_candidates(D)
    assert [p.code for p in saved] == [p.code for p in picks]
    assert ("news_global", 30) in ak.calls
    assert [n["code"] for n in ctx["notices"]] == ["600000"]
    assert len(ctx["stock_news"]["600000"]) == 3
    assert ctx["stats"]["600000"] == {"prev_close": 10.0, "avg_amount20": 2e8}
    json.dumps(ctx)  # 可序列化


def test_scan_tolerates_news_failure():
    ak = FakeAk()

    def boom(*args, **kwargs):
        raise RuntimeError("news source down")

    ak.news_global = ak.notices = ak.stock_news = boom
    store = Store(":memory:")
    assert len(premarket.scan(D, settings(), store, FakeCache(), ak, FakeStrategy(), CAL)) == 15
    ctx = store.load_candidates(D)[1]
    assert ctx["news"] == [] and ctx["notices"] == [] and ctx["stock_news"]["600000"] == []


def test_finalize_veto_sort_and_cap(store):
    review = PremarketReview(market_view="偏强", picks=[
        rp("600000", "veto", 0.0), rp("600001", conf=0.4), rp("600002", conf=0.9),
        rp("600003", conf=0.7), rp("600004", conf=0.6), rp("999999", conf=1.0)])
    llm = FakeLLM({"premarket:active": review})
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, FakeAk(), "active")
    assert reviewed is True and view == "偏强"
    assert [p.code for p in picks] == ["600002", "600003", "600004"]
    p = picks[0]
    assert (p.entry_max, p.stop, p.target) == (10.25, 9.6, 11.2)
    assert p.score == 0.9 and p.hold_days == 5 and p.name == "股600002" and p.reason == "LLM理由600002"
    call = llm.calls[0]
    assert call["essential"] is True and call["effort"] == "medium" and call["timeout"] == 90.0
    assert call["schema"] is PremarketReview and call["system"] == "盘前系统提示"
    assert "ACTIVE规则" in call["user"] and "600005" in call["user"] and "+2.00%" in call["user"]


def test_finalize_price_sanity_falls_back_to_quant_prices(store):
    review = PremarketReview(market_view="", picks=[
        rp("600000", conf=0.9, entry=10.0, stop=10.5, target=11.0),   # stop > entry
        rp("600001", conf=0.8, entry=10.2, stop=9.5, target=30.0),     # target 超出 1.5 倍
        rp("600002", conf=0.7)])
    picks, reviewed, _ = premarket.finalize(D, settings(), store, FakeLLM({"premarket:active": review}),
                                            FakeAk(), "active")
    assert reviewed is True
    assert [(p.entry_max, p.stop, p.target) for p in picks[:2]] == [(10.3, 9.3, 11.0)] * 2
    assert (picks[2].entry_max, picks[2].stop, picks[2].target) == (10.25, 9.6, 11.2)
    assert picks[0].reason == "LLM理由600000"


def test_finalize_all_vetoed_returns_empty(store):
    review = PremarketReview(market_view="系统性风险", picks=[rp(c, "veto", 0.0) for c in CODES[:6]])
    picks, reviewed, view = premarket.finalize(D, settings(), store, FakeLLM({"premarket:active": review}),
                                               FakeAk(), "active")
    assert picks == [] and reviewed is True and view == "系统性风险"


@pytest.mark.parametrize("exc", [TimeoutError("t"), RuntimeError("api"), ValueError("bad json"),
                                 LLMRefusal("refusal"), BudgetExceeded("budget")])
def test_finalize_llm_failure_falls_back_to_quant(store, exc):
    picks, reviewed, view = premarket.finalize(D, settings(), store, FakeLLM({"premarket:active": exc}),
                                               FakeAk(), "active")
    assert reviewed is False and view == ""
    assert picks == [qpick(c, i) for i, c in enumerate(CODES[:3])]


def test_finalize_spot_failure_still_calls_llm(store):
    llm = FakeLLM({"premarket:active": PremarketReview(market_view="", picks=[rp("600001")])})
    picks, reviewed, _ = premarket.finalize(D, settings(), store, llm, FakeAk(spot_fail=True), "active")
    assert reviewed is True and [p.code for p in picks] == ["600001"]
    assert "竞价数据缺失" in llm.calls[0]["user"]


def test_finalize_without_candidates_raises():
    with pytest.raises(RuntimeError):
        premarket.finalize(D, settings(), Store(":memory:"), FakeLLM({}), FakeAk(), "active")


def test_run_final_saves_both_and_pushes_only_active(store):
    llm = FakeLLM({
        "premarket:active": PremarketReview(market_view="A观点", picks=[rp("600001", conf=0.9)]),
        "premarket:candidate": PremarketReview(market_view="C观点", picks=[rp("600002", conf=0.9)]),
    })
    notifier = Recorder()
    premarket.run_final(D, settings(), store, llm, FakeAk(), notifier, CAL)
    act, act_meta = store.load_recommendation(D, "h:active")
    cand, cand_meta = store.load_recommendation(D, "h:candidate")
    assert [p.code for p in act] == ["600001"] and [p.code for p in cand] == ["600002"]
    assert act_meta["llm_reviewed"] is True and act_meta["market_view"] == "A观点"
    assert cand_meta["playbook_version"] == 1
    assert "CANDIDATE规则" in next(c["user"] for c in llm.calls if c["purpose"] == "premarket:candidate")
    assert notifier.sent == [format_recommendation(D, act, True, "A观点")]


def test_run_final_candidate_failure_does_not_affect_active(store):
    llm = FakeLLM({
        "premarket:active": PremarketReview(market_view="A观点", picks=[rp("600001", conf=0.9)]),
        "premarket:candidate": RuntimeError("boom"),
    })
    premarket.run_final(D, settings(), store, llm, FakeAk(), Recorder(), CAL)
    cand, meta = store.load_recommendation(D, "h:candidate")
    assert meta["llm_reviewed"] is False and len(cand) == 3
    assert store.load_recommendation(D, "h:active")[1]["llm_reviewed"] is True


def test_run_final_deadline_falls_back_for_slow_kind(store, monkeypatch):
    monkeypatch.setattr(premarket, "FINAL_DEADLINE", 0.3)
    gate = threading.Event()

    class SlowLLM(FakeLLM):
        def parse(self, purpose, *args, **kwargs):
            if purpose == "premarket:candidate":
                gate.wait(5)
            return super().parse(purpose, *args, **kwargs)

    llm = SlowLLM({"premarket:active": PremarketReview(market_view="A观点", picks=[rp("600001")]),
                   "premarket:candidate": PremarketReview(market_view="C观点", picks=[rp("600002")])})
    notifier = Recorder()
    try:
        premarket.run_final(D, settings(), store, llm, FakeAk(), notifier, CAL)
    finally:
        gate.set()
    cand, meta = store.load_recommendation(D, "h:candidate")
    assert meta["llm_reviewed"] is False and [p.code for p in cand] == CODES[:3]
    assert store.load_recommendation(D, "h:active")[1]["llm_reviewed"] is True
    assert len(notifier.sent) == 1
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_premarket.py -v
```

预期：`ImportError: cannot import name 'premarket' from 'agents'`，全部 ERROR。

**Step 3: 最小实现**

`agents/premarket.py`:

```python
"""盘前：08:30 scan 出量化候选并抓新闻公告；09:26 finalize 结合竞价让 Claude 审核定稿。"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import logging
import time
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from agents import playbook
from agents.llm import LLM
from config import Settings
from data.akshare_client import AkClient
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.dataview import DataView
from engine.store import Store
from engine.types import Pick
from notify.format import format_recommendation

log = logging.getLogger(__name__)

CANDIDATE_COUNT = 15
NEWS_LIMIT = 30
STOCK_NEWS_LIMIT = 3
HISTORY_DAYS = 400      # 自然日，约 270 个交易日，够常见的一年期因子
PRICE_BAND = 0.5        # LLM 价位须在量化 entry_max 的 ±50% 内
FINAL_DEADLINE = 100.0  # run_final 等待两路 finalize 的总时长上限（秒）
KINDS = ("active", "candidate")


class ReviewedPick(BaseModel):
    code: str
    action: Literal["keep", "veto"]
    confidence: float
    entry_max: float
    stop: float
    target: float
    reason: str


class PremarketReview(BaseModel):
    market_view: str
    picks: list[ReviewedPick]


def _safe(fn, default):
    """资讯类抓取失败不阻断盘前流程。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("premarket fetch failed: %r", e)
        return default


def _jsonable(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _notices(client: AkClient, days: list[dt.date], codes: list[str]) -> list[dict]:
    frames = [_safe(lambda day=day: client.notices(day), None) for day in days]
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df = df[df["code"].astype(str).isin(codes)].drop_duplicates(subset=["code", "title"])
    return _jsonable(df[["code", "name", "title", "type"]].to_dict("records"))


def _stats(view: DataView, code: str) -> dict:
    h = view.history(code, 20)
    if h.empty:
        return {}
    return {"prev_close": float(h["close"].iloc[-1]), "avg_amount20": float(h["amount"].mean())}


def scan(d: dt.date, settings: Settings, store: Store, cache: BarCache, client: AkClient, strategy,
         calendar: TradingCalendar) -> list[Pick]:
    asof = calendar.prev_day(d)
    codes = client.universe()
    cache.update(codes, asof)  # 增量：已是最新的代码不发请求
    panel, names = cache.load_panel(codes, asof - dt.timedelta(days=HISTORY_DAYS), asof)
    view = DataView(panel, names, asof)
    picks = strategy.select(view)[:CANDIDATE_COUNT]
    cand = [p.code for p in picks]
    context = {
        "news": _jsonable(_safe(lambda: client.news_global(NEWS_LIMIT), [])),
        "notices": _notices(client, [asof, d], cand),  # 晚间公告可能记在次日，两天都取
        "stock_news": {c: _jsonable(_safe(lambda c=c: client.stock_news(c, limit=STOCK_NEWS_LIMIT), [])
                                    [:STOCK_NEWS_LIMIT]) for c in cand},
        "stats": {c: _stats(view, c) for c in cand},
    }
    store.save_candidates(d, picks, context)
    log.info("premarket scan %s: %d candidates", d, len(picks))
    return picks


def _num(x, scale: float = 1.0, unit: str = "") -> str:
    return "未知" if x is None or pd.isna(x) else f"{x / scale:.2f}{unit}"


def _auction(row: dict | None) -> str:
    if row is None:
        return "无竞价数据"
    op, pc, amt = row.get("open"), row.get("prev_close"), row.get("amount")
    if op is None or pc is None or pd.isna(op) or pd.isna(pc) or op <= 0 or pc <= 0:
        return "无竞价（可能停牌）"
    return f"竞价 {op:.2f}（{op / pc - 1:+.2%}），竞价额 {_num(amt, 1e4, '万')}"


def _item(n: dict, width: int = 100) -> str:
    title, content = str(n.get("title") or ""), str(n.get("content") or "")
    body = f"{title}：{content[:width]}" if title and content else (title or content[:width])
    return f"[{n.get('time', '')}] {body}"


def _build_prompt(d: dt.date, pb: dict, cands: list[Pick], ctx: dict, auction: pd.DataFrame | None) -> str:
    stats = ctx.get("stats", {})
    rows: dict[str, dict] = {}
    if auction is not None:
        sub = auction[auction["code"].astype(str).isin([p.code for p in cands])]
        rows = {str(r["code"]): r for r in sub.to_dict("records")}
    lines = [f"# 交易日 {d.isoformat()}（09:26，集合竞价已结束）", "",
             f"## 经验规则（playbook v{pb.get('version', 1)}）", playbook.render(pb), "",
             f"## 候选股票（量化排序，共 {len(cands)} 只）"]
    if auction is None:
        lines.append("（竞价数据缺失：本次未取到竞价快照，请依据公告、新闻与规则判断，并在 market_view 中注明。）")
    for p in cands:
        s = stats.get(p.code, {})
        lines.append(f"- {p.code} {p.name}｜量化分 {p.score:.3f}｜昨收 {_num(s.get('prev_close'))}"
                     f"｜20日均额 {_num(s.get('avg_amount20'), 1e8, '亿')}｜{_auction(rows.get(p.code))}"
                     f"｜量化价位 买≤{p.entry_max:.2f} 止损 {p.stop:.2f} 止盈 {p.target:.2f}"
                     f"｜持有 {p.hold_days} 日｜{p.reason}")
    notices = [f"- {n['code']} {n['name']}：{n['title']}（{n['type']}）" for n in ctx.get("notices", [])]
    stock_news = [f"- {c}：" + "；".join(_item(n, 60) for n in items)
                  for c, items in ctx.get("stock_news", {}).items() if items]
    news = [f"- {_item(n)}" for n in ctx.get("news", [])]
    lines += ["", "## 候选相关公告", *(notices or ["（无）"])]
    lines += ["", "## 候选个股新闻", *(stock_news or ["（无）"])]
    lines += ["", "## 隔夜要闻（财联社电报）", *(news or ["（无）"])]
    lines += ["", f"请对以上 {len(cands)} 只候选逐一给出 keep/veto 与价位，code 与上表完全一致。"]
    return "\n".join(lines)


def _merge(q: Pick, r: ReviewedPick) -> Pick:
    """采用 LLM 价位需满足 stop < entry_max < target 且都在量化 entry_max 的 ±PRICE_BAND 内。"""
    lo, hi = q.entry_max * (1 - PRICE_BAND), q.entry_max * (1 + PRICE_BAND)
    sane = r.stop < r.entry_max < r.target and all(lo <= x <= hi for x in (r.stop, r.entry_max, r.target))
    entry, stop, target = (r.entry_max, r.stop, r.target) if sane else (q.entry_max, q.stop, q.target)
    return Pick(code=q.code, name=q.name, score=r.confidence, entry_max=entry, stop=stop, target=target,
                hold_days=q.hold_days, reason=r.reason)


def _load_candidates(d: dt.date, store: Store) -> tuple[list[Pick], dict]:
    loaded = store.load_candidates(d)
    if loaded is None:
        raise RuntimeError(f"{d} 没有盘前候选（premarket_scan 未成功运行）")
    return loaded


def finalize(d: dt.date, settings: Settings, store: Store, llm: LLM, client: AkClient, playbook_kind: str,
             timeout: float = 90.0) -> tuple[list[Pick], bool, str]:
    cands, ctx = _load_candidates(d, store)
    fallback = cands[: settings.max_picks]
    auction = _safe(client.spot, None)
    try:
        pb = playbook.load(playbook_kind)
        review = llm.parse(purpose=f"premarket:{playbook_kind}", system=playbook.load_prompt("premarket"),
                           user=_build_prompt(d, pb, cands, ctx, auction), schema=PremarketReview,
                           effort="medium", timeout=timeout, essential=True)
    except Exception as e:  # noqa: BLE001 - 超时/拒答/预算/解析失败一律退回量化
        log.warning("premarket finalize %s fell back to quant: %r", playbook_kind, e)
        return fallback, False, ""
    by_code = {p.code: p for p in cands}
    picks: list[Pick] = []
    for r in sorted(review.picks, key=lambda r: r.confidence, reverse=True):
        code = r.code.strip()
        if r.action == "keep" and code in by_code and code not in {p.code for p in picks}:
            picks.append(_merge(by_code[code], r))
    return picks[: settings.max_picks], True, review.market_view


class _Prefetched:
    """run_final 只拉一次全市场快照，交给两路 finalize 共用。"""

    def __init__(self, frame: pd.DataFrame | None):
        self.frame = frame

    def spot(self) -> pd.DataFrame:
        if self.frame is None:
            raise RuntimeError("竞价快照获取失败")
        return self.frame


def run_final(d: dt.date, settings: Settings, store: Store, llm: LLM, client: AkClient, notifier,
              calendar: TradingCalendar) -> None:
    _load_candidates(d, store)  # 没有候选直接抛错，由 run_job 告警
    shared = _Prefetched(_safe(client.spot, None))
    deadline = time.monotonic() + FINAL_DEADLINE
    pool = cf.ThreadPoolExecutor(max_workers=len(KINDS), thread_name_prefix="premarket")
    futures = {k: pool.submit(finalize, d, settings, store, llm, shared, k) for k in KINDS}
    results: dict[str, tuple[list[Pick], bool, str]] = {}
    try:
        for kind, fut in futures.items():
            try:
                results[kind] = fut.result(timeout=max(0.0, deadline - time.monotonic()))
            except cf.TimeoutError:
                log.warning("premarket %s exceeded %.0fs, using quant picks", kind, FINAL_DEADLINE)
                results[kind] = (_load_candidates(d, store)[0][: settings.max_picks], False, "")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # 超时的线程留在后台跑完，结果丢弃
    for kind, (picks, reviewed, view) in results.items():
        meta = {"llm_reviewed": reviewed, "market_view": view,
                "playbook_version": int(playbook.load(kind).get("version", 1))}
        store.save_recommendation(d, f"h:{kind}", picks, meta)
    picks, reviewed, view = results["active"]
    notifier.send_markdown(format_recommendation(d, picks, reviewed, view))
```

说明：`finalize` 在 `try` 之外只做两件事：读取候选（缺失时抛错，由 `run_job` 告警，当天不出推荐）和用 `_safe` 拉竞价快照。其余失败都退回量化 `cands[:max_picks]`，并返回 `llm_reviewed=False`，推送文案里显示"未经 LLM 审核"（由 `format_recommendation` 负责）。全部候选都被否决时返回空列表，`llm_reviewed=True`，表示今天经审核后不推荐。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_premarket.py -v
```

预期：15 passed（参数化的 5 个失败场景各算 1 个）。若 `run_final` 相关用例报 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`，说明 Store 还不是线程安全的，按本部分开头的说明 1 处理。

**Step 5: Manual smoke step（真实竞价快照 + 真实 Claude，不推送、不写正式库）**

`scripts/smoke_premarket.py`:

```python
"""手动冒烟：手选 3 只股票作为候选，走一遍 finalize（真实 spot + 真实 Claude）。
运行：uv run python scripts/smoke_premarket.py
结果写入临时 sqlite，不影响正式库，也不推送。
"""
import datetime as dt
import json
import tempfile
from pathlib import Path

from agents import premarket
from agents.llm import LLM
from config import get_settings
from data.akshare_client import AkClient
from engine.store import Store
from engine.types import Pick


def main() -> None:
    s = get_settings()
    store = Store(Path(tempfile.mkdtemp()) / "smoke.sqlite")
    client = AkClient()
    d = dt.date.today()
    spot = client.spot().set_index("code")
    picks = []
    for code in ("600519", "000001", "300750"):
        pc = float(spot.loc[code, "prev_close"])
        picks.append(Pick(code=code, name=str(spot.loc[code, "name"]), score=1.0, entry_max=round(pc * 1.03, 2),
                          stop=round(pc * 0.93, 2), target=round(pc * 1.10, 2), hold_days=5, reason="冒烟测试候选"))
    ctx = {"news": client.news_global(10), "notices": [], "stats": {},
           "stock_news": {p.code: client.stock_news(p.code, limit=3) for p in picks}}
    store.save_candidates(d, picks, json.loads(json.dumps(ctx, ensure_ascii=False, default=str)))
    out, reviewed, view = premarket.finalize(d, s, store, LLM(s, store), client, "active")
    print("llm_reviewed:", reviewed)
    print("market_view:", view)
    for p in out:
        print(p)
    print(f"本次花费约 {store.month_llm_cost(d.year, d.month):.3f} 元")
    assert reviewed, "LLM 审核失败，stderr 中的 'fell back to quant' 日志给出原因"


if __name__ == "__main__":
    main()
```

```
uv run python scripts/smoke_premarket.py
```

预期：`llm_reviewed: True`，`market_view` 为中文大盘判断，打印 0~3 个 `Pick`，每个 `reason` 都引用了竞价数字或新闻。人工检查两点：没有引用输入中不存在的新闻；价位满足 `stop < entry_max < target`。最好在交易日 09:26~09:30 之间跑一次，确认竞价阶段 `spot()` 的 `open` 已是竞价价；非交易时段跑只能验证接口连通。

**Step 6: 提交**

```
git add agents/premarket.py tests/test_premarket.py scripts/smoke_premarket.py
git commit -m "feat: add premarket scan and LLM-reviewed final recommendation with quant fallback"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）

### Task 4.4: agents/review.py — 盘后复盘与日报

**Files:**
- Create: `agents/review.py`
- Test: `tests/test_review.py`

`run_review` 先汇总两个 LLM 账户当天的推荐、成交、持仓和个股日线。两边都没有内容时跳过 LLM 调用，省掉一次花费。LLM 给出的 lesson 只写入 candidate playbook，然后依次调用 `maybe_release`、`git_commit`，最后给 `store.list_accounts()` 中所有有净值记录的账户推送日报。

**Step 1: 写失败的测试**

`tests/test_review.py`:

```python
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from agents import playbook, review
from agents.llm import BudgetExceeded, LLMRefusal
from agents.review import Lesson, ReviewOutput
from config import Settings
from engine.calendar import TradingCalendar
from engine.ledger import Account
from engine.store import Store
from engine.types import Fill, Pick

D = dt.date(2026, 10, 16)
PREV = dt.date(2026, 10, 15)
CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)
ACCOUNTS = ["q:baseline_momo", "h:active", "h:candidate", "bench:csi300"]


def settings() -> Settings:
    return Settings(anthropic_api_key="k", wecom_webhook_url="", root=Path("."))


def pick(code):
    return Pick(code=code, name=f"股{code}", score=0.8, entry_max=10.3, stop=9.6, target=11.2, hold_days=5,
                reason=f"推荐理由{code}")


class FakeCache:
    def __init__(self):
        self.requested = None

    def load_panel(self, codes, start, end):
        self.requested = (sorted(codes), start, end)
        idx = pd.DatetimeIndex([pd.Timestamp(PREV), pd.Timestamp(D)])
        df = pd.DataFrame({"open": [10.0, 10.62], "high": [10.2, 10.8], "low": [9.9, 10.1], "close": [10.0, 10.15],
                           "volume": [1e6, 2e6], "amount": [1e7, 2e7], "prev_close": [9.9, 10.0]}, index=idx)
        return {c: df.copy() for c in codes}, {c: f"股{c}" for c in codes}


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0, max_tokens=16000,
              essential=False):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               essential=essential))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Recorder:
    def __init__(self):
        self.sent = []

    def send_markdown(self, content):
        self.sent.append(content)
        return True

    def send_text(self, content):
        self.sent.append(content)
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", tmp_path)
    (tmp_path / "review_system.md").write_text("复盘系统提示", encoding="utf-8")
    playbook.save("active", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "旧规则", "evidence": "e", "created": "2026-09-01"}]})
    playbook.save("candidate", {"version": 1, "created": "x", "rules": [
        {"id": "r1", "text": "旧规则", "evidence": "e", "created": "2026-09-01"}]})
    commits, releases = [], []
    monkeypatch.setattr(playbook, "git_commit", lambda paths, message: commits.append((paths, message)) or True)

    def fake_release(store, today, calendar):
        releases.append(today)
        return None

    monkeypatch.setattr(playbook, "maybe_release", fake_release)

    store = Store(":memory:")
    for i, name in enumerate(ACCOUNTS):
        acc = Account(name=name, cash=500_000.0)
        if name == "h:active":
            acc.trades = [Fill(date=PREV, code="600009", side="BUY", qty=100, price=9.0, fee=5.0, reason=""),
                          Fill(date=D, code="600001", side="BUY", qty=1000, price=10.63, fee=5.0, reason=""),
                          Fill(date=D, code="600009", side="SELL", qty=100, price=9.5, fee=5.5, reason="止盈")]
        store.save_account(acc)
        store.record_nav(name, PREV, 500_000.0)
        store.record_nav(name, D, 500_000.0 * (1 + 0.01 * (i + 1)))
    store.save_recommendation(D, "h:active", [pick("600001"), pick("600002")], {"llm_reviewed": True})
    store.save_recommendation(D, "h:candidate", [pick("600003")], {"llm_reviewed": False})
    return dict(store=store, cache=FakeCache(), notifier=Recorder(), commits=commits, releases=releases)


OUTPUT = ReviewOutput(summary="今日 600001 高开 6% 买入后回落。", lessons=[
    Lesson(action="add", rule_id="", text="主板竞价高开超过 5% 的候选否决",
           evidence="2026-10-16 600001 高开 6.2% 买入，收盘 -4.4%"),
    Lesson(action="add", rule_id="", text="无证据规则", evidence=""),
])


def run(env, llm):
    review.run_review(D, settings(), env["store"], llm, env["cache"], env["notifier"], CAL)


def test_review_applies_lessons_to_candidate_only(env):
    llm = FakeLLM(OUTPUT)
    run(env, llm)
    call = llm.calls[0]
    assert call["purpose"] == "review" and call["essential"] is False and call["effort"] == "medium"
    assert call["schema"] is ReviewOutput and call["system"] == "复盘系统提示"
    user = call["user"]
    for s in ("600001", "600002", "600003", "10.62", "BUY", "止盈", "旧规则", "未经 LLM 审核"):
        assert s in user, s
    assert "@ 9.00" not in user.split("### 当日成交")[1].split("###")[0]  # 前一日的买入不算当日成交
    cand, act = playbook.load("candidate"), playbook.load("active")
    assert [r["text"] for r in cand["rules"]] == ["旧规则", "主板竞价高开超过 5% 的候选否决"]
    assert [r["text"] for r in act["rules"]] == ["旧规则"]
    assert env["releases"] == [D]
    assert env["commits"] == [(["playbook/active.json", "playbook/candidate.json"],
                               "playbook: 2026-10-16 复盘更新 1 条")]
    assert env["cache"].requested[0] == ["600001", "600002", "600003", "600009"]
    assert env["cache"].requested[2] == D


def test_review_pushes_report_for_all_accounts(env):
    run(env, FakeLLM(OUTPUT))
    assert len(env["notifier"].sent) == 1
    report = env["notifier"].sent[0]
    for name in ACCOUNTS:
        assert name in report
    assert "今日 600001 高开 6% 买入后回落。" in report
    assert "+4.00%" in report  # bench:csi300 当日 +4%


def test_review_caps_lessons(env):
    many = ReviewOutput(summary="s", lessons=[
        Lesson(action="add", rule_id="", text=f"规则{i}", evidence=f"证据{i}") for i in range(5)])
    run(env, FakeLLM(many))
    assert len(playbook.load("candidate")["rules"]) == 1 + review.MAX_LESSONS


def test_review_budget_exceeded_still_pushes_report(env):
    run(env, FakeLLM(BudgetExceeded("over")))
    assert len(env["notifier"].sent) == 1
    assert "预算" in env["notifier"].sent[0] and "h:active" in env["notifier"].sent[0]
    assert playbook.load("candidate")["rules"] == playbook.load("active")["rules"]
    assert env["commits"] == []
    assert env["releases"] == [D]  # 发布判断不依赖 LLM


@pytest.mark.parametrize("exc", [LLMRefusal("r"), TimeoutError("t"), ValueError("bad")])
def test_review_llm_failure_still_pushes_report(env, exc):
    run(env, FakeLLM(exc))
    assert len(env["notifier"].sent) == 1 and "复盘失败" in env["notifier"].sent[0]


def test_review_release_message_is_pushed_and_committed(env, monkeypatch):
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: "playbook v2 上线：测试")
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "playbook v2 上线：测试" in env["notifier"].sent[0]
    assert env["commits"] == [(["playbook/active.json", "playbook/candidate.json"],
                               "playbook: 2026-10-16 playbook v2 上线：测试")]


def test_review_without_recommendations_skips_llm(env):
    store = Store(":memory:")
    for name in ACCOUNTS:
        store.save_account(Account(name=name, cash=500_000.0))
        store.record_nav(name, D, 500_000.0)
    store.save_account(Account(name="q:no_nav", cash=1.0))  # 无净值记录 -> 不进日报
    llm = FakeLLM(OUTPUT)
    review.run_review(D, settings(), store, llm, env["cache"], env["notifier"], CAL)
    assert llm.calls == [] and env["cache"].requested is None
    report = env["notifier"].sent[0]
    assert "无推荐" in report and "q:no_nav" not in report
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_review.py -v
```

预期：`ImportError: cannot import name 'review' from 'agents'`，全部 ERROR。

**Step 3: 最小实现**

`agents/review.py`:

```python
"""16:00 盘后复盘：对比两套 LLM 账户的推荐与实际，经验只写入 candidate playbook，并推送日报。"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from agents import playbook
from agents.llm import LLM, BudgetExceeded
from config import Settings
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.store import Store
from notify.format import format_daily_report

log = logging.getLogger(__name__)

LLM_ACCOUNTS = ("h:active", "h:candidate")
MAX_LESSONS = 3
BAR_LOOKBACK_DAYS = 10
PLAYBOOK_FILES = ["playbook/active.json", "playbook/candidate.json"]


class Lesson(BaseModel):
    action: Literal["add", "remove", "merge"]
    rule_id: str  # add 时为 ""
    text: str
    evidence: str


class ReviewOutput(BaseModel):
    summary: str
    lessons: list[Lesson]


def _account_section(store: Store, d: dt.date, name: str) -> tuple[list[str], set[str]]:
    rec = store.load_recommendation(d, name)
    acc = store.load_account(name)
    fills = [t for t in acc.trades if t.date == d] if acc else []
    positions = list(acc.positions.values()) if acc else []
    codes = {p.code for p in rec[0]} if rec else set()
    codes |= {t.code for t in fills} | {p.code for p in positions}
    lines = [f"## 账户 {name}"]
    if rec is None:
        lines.append("推荐：（当日无推荐）")
    else:
        picks, meta = rec
        tag = "经 LLM 审核" if meta.get("llm_reviewed") else "未经 LLM 审核，退回量化"
        lines.append(f"推荐（{tag}）：" + ("" if picks else "经审核后不推荐"))
        lines += [f"- {p.code} {p.name} 买≤{p.entry_max:.2f} 止损 {p.stop:.2f} 止盈 {p.target:.2f}"
                  f" 持有 {p.hold_days} 日｜{p.reason}" for p in picks]
    lines.append("### 当日成交")
    lines += [f"- {t.side} {t.code} {t.qty} 股 @ {t.price:.2f}，费用 {t.fee:.2f}，{t.reason or '开盘成交'}"
              for t in fills] or ["（无）"]
    lines.append("### 收盘持仓")
    lines += [f"- {p.code} {p.name} {p.qty} 股，成本 {p.cost:.2f}，止损 {p.stop:.2f}，止盈 {p.target:.2f}，"
              f"到期 {p.exit_date.isoformat()}" for p in positions] or ["（空仓）"]
    return lines, codes


def _bar_lines(cache: BarCache, codes: set[str], d: dt.date) -> list[str]:
    panel, names = cache.load_panel(sorted(codes), d - dt.timedelta(days=BAR_LOOKBACK_DAYS), d)
    ts, out = pd.Timestamp(d), ["## 个股当日行情"]
    for code in sorted(codes):
        df = panel.get(code)
        if df is None or ts not in df.index:
            out.append(f"- {code}：当日无行情（可能停牌）")
            continue
        r = df.loc[ts]
        pc = float(r["prev_close"])
        out.append(f"- {code} {names.get(code, '')}：开 {r['open']:.2f}（{r['open'] / pc - 1:+.2%}）"
                   f" 高 {r['high']:.2f} 低 {r['low']:.2f} 收 {r['close']:.2f}（{r['close'] / pc - 1:+.2%}）"
                   f" 成交额 {r['amount'] / 1e8:.2f} 亿")
    return out


def _report_rows(store: Store, d: dt.date) -> list[dict]:
    rows = []
    for name in store.list_accounts():
        nav = store.nav_series(name)
        if nav.empty:
            continue
        nav = nav.loc[: pd.Timestamp(d)]
        if nav.empty:
            continue
        equity = float(nav.iloc[-1])
        day_ret = equity / float(nav.iloc[-2]) - 1.0 if len(nav) >= 2 else 0.0
        rows.append({"account": name, "equity": equity, "day_ret": day_ret,
                     "total_ret": equity / float(nav.iloc[0]) - 1.0})
    return rows


def _build_prompt(d: dt.date, pb: dict, sections: list[str], bars: list[str]) -> str:
    return "\n".join([f"# 复盘日 {d.isoformat()}", "",
                      f"## 候选版经验规则（playbook v{pb.get('version', 1)}，你的 lesson 只作用于这一版）",
                      playbook.render(pb), "", *sections, "", *bars, "",
                      f"请写当日复盘 summary，并给出不超过 {MAX_LESSONS} 条带证据的 lesson（可以为空）。"])


def run_review(d: dt.date, settings: Settings, store: Store, llm: LLM, cache: BarCache, notifier,
               calendar: TradingCalendar) -> None:
    sections: list[str] = []
    codes: set[str] = set()
    has_rec = False
    for name in LLM_ACCOUNTS:
        lines, c = _account_section(store, d, name)
        sections += lines
        codes |= c
        has_rec = has_rec or store.load_recommendation(d, name) is not None

    changed = 0
    if not has_rec and not codes:
        summary = "今日两个 LLM 账户均无推荐、无持仓，未调用 LLM 复盘。"
    else:
        cand = playbook.load("candidate")
        try:
            out = llm.parse(purpose="review", system=playbook.load_prompt("review"),
                            user=_build_prompt(d, cand, sections, _bar_lines(cache, codes, d)),
                            schema=ReviewOutput, effort="medium", essential=False)
        except BudgetExceeded:
            summary = "本月 LLM 预算已用完，今日未做 LLM 复盘。"
        except Exception as e:  # noqa: BLE001 - 拒答/超时/解析失败：日报照发
            log.warning("review LLM failed: %r", e)
            summary = f"LLM 复盘失败（{type(e).__name__}），今日 playbook 未更新。"
        else:
            summary = out.summary
            lessons = [les for les in out.lessons[:MAX_LESSONS] if les.evidence.strip()]
            new = playbook.apply_lessons(cand, lessons, d)
            if new != cand:
                playbook.save("candidate", new)
                changed = len(lessons)

    release = playbook.maybe_release(store, d, calendar)
    parts = ([f"复盘更新 {changed} 条"] if changed else []) + ([release] if release else [])
    if parts:
        playbook.git_commit(PLAYBOOK_FILES, f"playbook: {d.isoformat()} " + "；".join(parts))
        if release:
            summary = f"{summary}\n{release}"
    notifier.send_markdown(format_daily_report(d, _report_rows(store, d), summary))
```

说明：`changed` 统计带证据、实际让 candidate 发生变化的那一批 lesson 数量（无效的 remove/merge 只要整体无变化就不提交）。`maybe_release` 不依赖 LLM，预算用完时照常执行，保证每两周一次的发布判断不被跳过。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_review.py -v
```

预期：9 passed（参数化的 3 个失败场景各算 1 个）。再跑本部分全部测试：

```
uv run pytest tests/test_llm.py tests/test_playbook.py tests/test_premarket.py tests/test_review.py -v
```

预期：45 passed。

**Step 5: Manual smoke step（真实 Claude 复盘一次，不推送、不提交）**

前提：已执行 `python -m jobs.bootstrap`，并至少结算过一个有推荐的交易日。

```
uv run python -c "import datetime as dt; from agents import playbook, review; from agents.llm import LLM; from config import get_settings; from data.akshare_client import AkClient; from data.cache import BarCache; from engine.calendar import TradingCalendar; from engine.store import Store; from notify.wecom import NullNotifier; s=get_settings(); st=Store(s.root / s.db_path); c=AkClient(); cal=TradingCalendar(c.trade_calendar()); playbook.git_commit=lambda *a, **k: False; n=NullNotifier(); d=cal.prev_day(dt.date.today() + dt.timedelta(days=1)); review.run_review(d, s, st, LLM(s, st), BarCache(s.root / s.db_path, c), n, cal); print(n.sent[0]); print(playbook.load('candidate'))"
```

预期：打印日报 markdown，"复盘"段为 Claude 写的中文摘要；`candidate` 中新增的规则（若有）`evidence` 含日期和股票代码。人工检查摘要中没有编造输入里不存在的数字。检查后用 `git checkout -- playbook/candidate.json` 撤销冒烟写入的规则（`git_commit` 已打桩，不会产生提交）。

**Step 6: 提交**

```
git add agents/review.py tests/test_review.py
git commit -m "feat: add post-close LLM review that evolves candidate playbook and pushes daily report"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）


---

## Part 5: 研究循环（递归自我提升）

> 契约补充说明（本节内自行决定的最简一致选择）：工具函数以 `tool_<name>(ctx, ...)` 的模块级函数实现，`ctx` 为 `ResearchContext`；`run_research` 内用同名闭包（`list_strategies`/`read_file`/`write_strategy`/`run_evaluation`/`recent_trials`）包一层 `@beta_tool` 暴露给模型。工具出错时返回以 `ERROR:` 开头的字符串（不抛异常），让模型自行纠正。`run_evaluation` 的次数在启动子进程前就计数（超时/失败也算一次）。子进程不加 `-I`（`-I` 会让 `-m engine.run_eval` 找不到项目包），以 `cwd=root` 运行。

本部分产出 `agents/research.py`：每周一次（`jobs/research.py`，周六 10:00）由 Claude 提出策略变体、写入 `strategies/`、调用隔离的评估子进程回测。安全边界：

- 工具只能读 `strategies/*.py`、`playbook/*`、`engine/*.py`，只能写 `strategies/<name>.py`，且必须通过 `sandbox.check_source`。
- 评估在子进程里跑 `engine.run_eval`，模型只拿到 `EvalReport.to_agent_dict()`（留出集只有通过/不通过）。
- 会话前后都校验 `integrity.verify(root) == []`，否则告警并中止（不提交 git）。
- 每条消息的 usage 记账到 `store.add_llm_usage("research", ...)`，月预算超限立即停止迭代。

### Task 5.1: 研究工具函数（agents/research.py 第一部分）

**Files:**
- Create: `agents/research.py`
- Test: `tests/test_research_tools.py`

**Step 1: 写失败测试**

`tests/test_research_tools.py`：

```python
import json
import subprocess
import sys
from datetime import date

import pytest

from agents import research
from agents.research import (
    ResearchContext,
    tool_list_strategies,
    tool_read_file,
    tool_recent_trials,
    tool_run_evaluation,
    tool_write_strategy,
)
from engine.store import Store

GOOD_SRC = '''"""假设：测试用空策略。"""
from engine.types import Pick
from engine.dataview import DataView


class Probe:
    name = "probe_one"

    def select(self, view: DataView) -> list[Pick]:
        return []


STRATEGY = Probe()
'''


@pytest.fixture
def ctx(tmp_path):
    for d in ("strategies", "engine", "playbook"):
        (tmp_path / d).mkdir()
    (tmp_path / "strategies" / "baseline_momo.py").write_text("# incumbent\n", encoding="utf-8")
    (tmp_path / "engine" / "metrics.py").write_text("# metrics\n", encoding="utf-8")
    (tmp_path / "playbook" / "active.json").write_text('{"version": 1}', encoding="utf-8")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / "config.py").write_text("X = 1\n", encoding="utf-8")
    store = Store(":memory:")
    store.set_status("baseline_momo", "incumbent", date(2026, 10, 1))
    return ResearchContext(root=tmp_path, store=store, today=date(2026, 10, 17), max_evals=2)


# ---------- read_file ----------

@pytest.mark.parametrize("path", [
    "strategies/baseline_momo.py",
    "engine/metrics.py",
    "playbook/active.json",
    r"strategies\baseline_momo.py",
])
def test_read_file_allowed(ctx, path):
    assert not tool_read_file(ctx, path).startswith("ERROR")


@pytest.mark.parametrize("path", [
    ".env",
    "config.py",
    "../secret.txt",
    "strategies/../.env",
    "strategies/../config.py",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "engine/notes.txt",
    "strategies/missing.py",
    "data/rsiqs.sqlite",
])
def test_read_file_rejected(ctx, path):
    assert tool_read_file(ctx, path).startswith("ERROR")


def test_read_file_absolute_inside_root_rejected(ctx):
    p = str(ctx.root / "strategies" / "baseline_momo.py")
    assert tool_read_file(ctx, p).startswith("ERROR")


# ---------- list / recent ----------

def test_list_strategies_reports_status(ctx, monkeypatch):
    monkeypatch.setattr(research, "list_strategy_names", lambda: ["baseline_momo", "foo_bar"])
    out = json.loads(tool_list_strategies(ctx))
    assert {"name": "baseline_momo", "status": "incumbent"} in out
    assert {"name": "foo_bar", "status": None} in out


def test_recent_trials_json(ctx):
    ctx.store.add_trial("foo_bar", {"k": 1}, 0.7, 0.9, True, False)
    out = json.loads(tool_recent_trials(ctx))
    assert out[0]["strategy"] == "foo_bar"


# ---------- write_strategy ----------

@pytest.mark.parametrize("name", ["Bad", "ab", "1abc", "foo-bar", "../evil", "base", "sandbox", "__init__"])
def test_write_strategy_bad_name(ctx, name):
    assert tool_write_strategy(ctx, name, GOOD_SRC).startswith("ERROR")
    assert ctx.store.get_status(name) is None


def test_write_strategy_incumbent_rejected(ctx):
    out = tool_write_strategy(ctx, "baseline_momo", GOOD_SRC)
    assert out.startswith("ERROR")
    assert (ctx.root / "strategies" / "baseline_momo.py").read_text(encoding="utf-8") == "# incumbent\n"


def test_write_strategy_sandbox_violation(ctx):
    src = "import os\n" + GOOD_SRC
    out = tool_write_strategy(ctx, "probe_one", src)
    assert out.startswith("ERROR") and "os" in out
    assert not (ctx.root / "strategies" / "probe_one.py").exists()


def test_write_strategy_syntax_error(ctx):
    out = tool_write_strategy(ctx, "probe_one", "def broken(:\n")
    assert out.startswith("ERROR")
    assert not (ctx.root / "strategies" / "probe_one.py").exists()


def test_write_strategy_success(ctx):
    out = tool_write_strategy(ctx, "probe_one", GOOD_SRC)
    assert out.startswith("OK")
    assert (ctx.root / "strategies" / "probe_one.py").read_text(encoding="utf-8") == GOOD_SRC
    assert ctx.store.get_status("probe_one") == "candidate"
    assert "probe_one" in ctx.written


# ---------- run_evaluation ----------

def _fake_run(payload, calls):
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="log line\n" + json.dumps(payload) + "\n", stderr="")
    return run


def _write_probe(ctx):
    assert tool_write_strategy(ctx, "probe_one", GOOD_SRC).startswith("OK")


def test_run_evaluation_pass_sets_shadow(ctx, monkeypatch):
    _write_probe(ctx)
    calls = []
    payload = {"strategy": "probe_one", "train": {"sharpe": 1.0}, "val": {"sharpe": 1.2},
               "gate_pass": True, "holdout_pass": True}
    monkeypatch.setattr(research.subprocess, "run", _fake_run(payload, calls))
    out = json.loads(tool_run_evaluation(ctx, "probe_one"))
    assert out["gate_pass"] is True and out["holdout_pass"] is True
    assert ctx.store.get_status("probe_one") == "shadow"
    cmd, kw = calls[0]
    assert cmd == [sys.executable, "-m", "engine.run_eval", "probe_one"]
    assert kw["cwd"] == str(ctx.root) and kw["timeout"] == 900
    assert ctx.results[-1]["strategy"] == "probe_one"


def test_run_evaluation_gate_fail_keeps_candidate(ctx, monkeypatch):
    _write_probe(ctx)
    payload = {"strategy": "probe_one", "train": {}, "val": {}, "gate_pass": False, "holdout_pass": None}
    monkeypatch.setattr(research.subprocess, "run", _fake_run(payload, []))
    tool_run_evaluation(ctx, "probe_one")
    assert ctx.store.get_status("probe_one") == "candidate"


def test_run_evaluation_holdout_fail_keeps_candidate(ctx, monkeypatch):
    _write_probe(ctx)
    payload = {"strategy": "probe_one", "train": {}, "val": {}, "gate_pass": True, "holdout_pass": False}
    monkeypatch.setattr(research.subprocess, "run", _fake_run(payload, []))
    tool_run_evaluation(ctx, "probe_one")
    assert ctx.store.get_status("probe_one") == "candidate"


def test_run_evaluation_limit_enforced(ctx, monkeypatch):
    _write_probe(ctx)
    calls = []
    payload = {"strategy": "probe_one", "train": {}, "val": {}, "gate_pass": False, "holdout_pass": None}
    monkeypatch.setattr(research.subprocess, "run", _fake_run(payload, calls))
    tool_run_evaluation(ctx, "probe_one")
    tool_run_evaluation(ctx, "probe_one")
    out = tool_run_evaluation(ctx, "probe_one")
    assert out.startswith("ERROR") and "limit" in out.lower()
    assert len(calls) == 2 and ctx.evals_used == 2


def test_run_evaluation_unknown_strategy(ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(research.subprocess, "run", _fake_run({}, calls))
    assert tool_run_evaluation(ctx, "nope_strategy").startswith("ERROR")
    assert tool_run_evaluation(ctx, "../evil").startswith("ERROR")
    assert calls == [] and ctx.evals_used == 0


def test_run_evaluation_timeout_and_bad_output(ctx, monkeypatch):
    _write_probe(ctx)

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(research.subprocess, "run", boom)
    assert "timeout" in tool_run_evaluation(ctx, "probe_one").lower()

    monkeypatch.setattr(research.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="not json\n", stderr=""))
    assert tool_run_evaluation(ctx, "probe_one").startswith("ERROR")
    assert ctx.store.get_status("probe_one") == "candidate"
```

**Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_research_tools.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'agents.research'`（或 `ImportError: cannot import name 'ResearchContext'`）。

**Step 3: 最小实现**

`agents/research.py`（本任务先写工具部分；Task 5.2 追加 `RESEARCH_SYSTEM`、`build_brief`、`run_research`）：

```python
"""研究循环：Claude 提出策略变体 -> 沙箱校验 -> 子进程评估 -> 合格者进影子盘。

工具函数为模块级 tool_* 函数（便于测试），run_research 内再用 @beta_tool 包装。
工具出错返回 "ERROR: ..." 字符串而非抛异常，让模型自行纠正。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from engine.store import Store
from strategies.base import list_strategies as list_strategy_names
from strategies.sandbox import check_source

MAX_EVALS_PER_SESSION = 5
EVAL_TIMEOUT_S = 900
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
RESERVED_NAMES = {"base", "sandbox", "__init__"}
# 可读目录 -> 允许的后缀（None = 任意文件）；只允许 <dir>/<file> 一层
READABLE = {"strategies": ".py", "engine": ".py", "playbook": None}
MAX_READ_CHARS = 60_000


@dataclass
class ResearchContext:
    root: Path
    store: Store
    today: date
    max_evals: int = MAX_EVALS_PER_SESSION
    evals_used: int = 0
    written: set[str] = field(default_factory=set)
    results: list[dict] = field(default_factory=list)


def _err(msg: str) -> str:
    return f"ERROR: {msg}"


def tool_list_strategies(ctx: ResearchContext) -> str:
    rows = [{"name": n, "status": ctx.store.get_status(n)} for n in list_strategy_names()]
    return json.dumps(rows, ensure_ascii=False)


def tool_read_file(ctx: ResearchContext, path: str) -> str:
    p = path.strip().replace("\\", "/")
    if not p or p.startswith("/") or ":" in p:
        return _err("absolute paths are not allowed; use e.g. strategies/baseline_momo.py")
    parts = p.split("/")
    if ".." in parts or "." in parts or len(parts) != 2 or parts[0] not in READABLE:
        return _err("only strategies/*.py, engine/*.py and playbook/* are readable")
    suffix = READABLE[parts[0]]
    if suffix and not parts[1].endswith(suffix):
        return _err(f"only {suffix} files are readable in {parts[0]}/")
    if parts[1].startswith("."):
        return _err("hidden files are not readable")
    base = (ctx.root / parts[0]).resolve()
    target = (base / parts[1]).resolve()
    if target.parent != base or not target.is_file():
        return _err(f"file not found: {p}")
    text = target.read_text(encoding="utf-8")
    return text if len(text) <= MAX_READ_CHARS else text[:MAX_READ_CHARS] + "\n# [truncated]"


def tool_write_strategy(ctx: ResearchContext, name: str, code: str) -> str:
    if not NAME_RE.match(name) or name in RESERVED_NAMES:
        return _err("name must match ^[a-z][a-z0-9_]{2,40}$ and not be base/sandbox/__init__")
    if name in ctx.store.strategies_with_status("incumbent"):
        return _err(f"{name} is the current incumbent and cannot be overwritten; pick a new name")
    try:
        compile(code, f"strategies/{name}.py", "exec")
    except SyntaxError as e:
        return _err(f"syntax error: {e}")
    violations = check_source(code)
    if violations:
        return _err("sandbox violations: " + "; ".join(violations))
    (ctx.root / "strategies" / f"{name}.py").write_text(code, encoding="utf-8")
    ctx.store.set_status(name, "candidate", ctx.today)
    ctx.written.add(name)
    return f"OK: wrote strategies/{name}.py (status=candidate)"


def _last_json_line(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def tool_run_evaluation(ctx: ResearchContext, name: str) -> str:
    if not NAME_RE.match(name) or name in RESERVED_NAMES:
        return _err("invalid strategy name")
    if not (ctx.root / "strategies" / f"{name}.py").is_file():
        return _err(f"strategies/{name}.py does not exist; write it first")
    if ctx.evals_used >= ctx.max_evals:
        return _err(f"evaluation limit reached ({ctx.max_evals} per session); stop and summarize")
    ctx.evals_used += 1  # 先计数：超时/失败同样消耗一次额度
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "engine.run_eval", name],
            cwd=str(ctx.root), capture_output=True, text=True,
            encoding="utf-8", timeout=EVAL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _err(f"evaluation timeout after {EVAL_TIMEOUT_S}s; simplify the strategy")
    report = _last_json_line(proc.stdout or "")
    if proc.returncode != 0 or report is None:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return _err(f"evaluation failed (exit {proc.returncode}): {tail}")
    if report.get("gate_pass") and report.get("holdout_pass"):
        ctx.store.set_status(name, "shadow", ctx.today)
        report["new_status"] = "shadow"
    ctx.results.append(report)
    return json.dumps(report, ensure_ascii=False)


def tool_recent_trials(ctx: ResearchContext, n: int = 20) -> str:
    return json.dumps(ctx.store.recent_trials(n), ensure_ascii=False, default=str)
```

**Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_research_tools.py -v
```

预期：全部 PASS。

**Step 5: 提交**

```bash
git add agents/research.py tests/test_research_tools.py
git commit -m "feat: research agent tool functions with path/sandbox/eval-limit guards"
```

（提交信息末尾附加 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）

### Task 5.2: run_research 主循环 + RESEARCH_SYSTEM 提示词

**Files:**
- Modify: `agents/research.py`（追加）
- Test: `tests/test_research_loop.py`

**Step 1: 写失败测试**

`tests/test_research_loop.py`：

```python
from dataclasses import dataclass
from datetime import date

import pytest

from agents import research
from agents.research import RESEARCH_SYSTEM, run_research
from config import Settings
from engine.store import Store
from notify.wecom import NullNotifier


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeMessage:
    usage: FakeUsage
    content: list
    stop_reason: str = "end_turn"


class FakeMessages:
    def __init__(self, messages):
        self._messages = messages
        self.kwargs = None
        self.yielded = 0

    def tool_runner(self, **kwargs):
        self.kwargs = kwargs

        def gen():
            for m in self._messages:
                self.yielded += 1
                yield m
        return gen()


class FakeClient:
    def __init__(self, messages):
        self.beta = type("Beta", (), {})()
        self.beta.messages = FakeMessages(messages)


def msg(inp, out, text="done"):
    return FakeMessage(usage=FakeUsage(inp, out), content=[FakeText(text)])


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "strategies").mkdir()
    settings = Settings(anthropic_api_key="test-key", wecom_webhook_url="",
                        monthly_budget_cny=50.0, usd_cny=7.2, root=tmp_path)
    store = Store(":memory:")
    store.set_status("baseline_momo", "incumbent", date(2026, 10, 1))
    commits = []
    monkeypatch.setattr(research, "verify", lambda root: [])
    monkeypatch.setattr(research, "git_commit", lambda paths, message: commits.append((paths, message)) or True)
    monkeypatch.setattr(research, "list_strategy_names", lambda: ["baseline_momo"])
    return settings, store, NullNotifier(), commits


def test_system_prompt_covers_key_points():
    for kw in ["≤5", "DSR", "holdout", "过拟合", "docstring", "超额"]:
        assert kw in RESEARCH_SYSTEM


def test_runner_called_with_contract_params_and_cost_recorded(env):
    settings, store, notifier, commits = env
    client = FakeClient([msg(1000, 500), msg(2000, 1000, "总结：无新策略")])
    out = run_research(settings, store, notifier, client=client)
    kw = client.beta.messages.kwargs
    assert kw["model"] == settings.llm_model and kw["max_tokens"] == 16000
    assert kw["system"] == RESEARCH_SYSTEM and kw["output_config"] == {"effort": "high"}
    assert kw["messages"][0]["role"] == "user"
    assert {t.name for t in kw["tools"]} == {
        "list_strategies", "read_file", "write_strategy", "run_evaluation", "recent_trials"}
    # (3000*4 + 1500*20) / 1e6 USD * 7.2
    expected = (3000 * 4.0 + 1500 * 20.0) / 1e6 * 7.2
    today = date.today()
    assert store.month_llm_cost(today.year, today.month) == pytest.approx(expected)
    assert out["status"] == "ok" and out["cost_cny"] == pytest.approx(expected)
    assert out["final_text"] == "总结：无新策略"
    assert commits == []  # 没写策略就不提交
    assert notifier.sent  # 推送了摘要


def test_stops_when_budget_exceeded(env):
    settings, store, notifier, _ = env
    # 每条 1M 输入 token = 4 USD * 7.2 = 28.8 CNY；预算 50 -> 第 2 条后停止
    client = FakeClient([msg(1_000_000, 0) for _ in range(5)])
    out = run_research(settings, store, notifier, client=client)
    assert client.beta.messages.yielded == 2
    assert out["status"] == "budget_stopped"


def test_budget_already_exhausted_skips_session(env):
    settings, store, notifier, _ = env
    store.add_llm_usage("premarket:active", 0, 0, 60.0)
    client = FakeClient([msg(10, 10)])
    out = run_research(settings, store, notifier, client=client)
    assert out["status"] == "budget_exceeded"
    assert client.beta.messages.kwargs is None


def test_integrity_failure_before_aborts_and_alerts(env, monkeypatch):
    settings, store, notifier, commits = env
    monkeypatch.setattr(research, "verify", lambda root: ["engine/broker.py"])
    client = FakeClient([msg(10, 10)])
    out = run_research(settings, store, notifier, client=client)
    assert out["status"] == "integrity_failed"
    assert client.beta.messages.kwargs is None
    assert any("engine/broker.py" in m for m in map(str, notifier.sent))
    assert commits == []


def test_integrity_failure_after_aborts_without_commit(env, monkeypatch):
    settings, store, notifier, commits = env
    results = iter([[], ["engine/evaluator.py"]])
    monkeypatch.setattr(research, "verify", lambda root: next(results))
    client = FakeClient([msg(10, 10)])
    out = run_research(settings, store, notifier, client=client)
    assert out["status"] == "integrity_failed"
    assert any("engine/evaluator.py" in m for m in map(str, notifier.sent))
    assert commits == []


GOOD_SRC = '''"""假设：测试用空策略。"""
from engine.types import Pick
from engine.dataview import DataView


class Probe:
    name = "probe_two"

    def select(self, view: DataView) -> list[Pick]:
        return []


STRATEGY = Probe()
'''


def test_written_strategy_is_committed(env):
    settings, store, notifier, commits = env

    class WritingMessages(FakeMessages):
        def tool_runner(self, **kwargs):
            self.kwargs = kwargs
            tools = {t.name: t for t in kwargs["tools"]}

            def gen():
                # 模拟 runner 执行模型发起的工具调用
                tools["write_strategy"].call({"name": "probe_two", "code": GOOD_SRC})
                self.yielded += 1
                yield msg(100, 100, "写了 probe_two")
            return gen()

    client = FakeClient([])
    client.beta.messages = WritingMessages([])
    out = run_research(settings, store, notifier, client=client)
    assert (settings.root / "strategies" / "probe_two.py").exists()
    assert store.get_status("probe_two") == "candidate"
    assert out["written"] == ["probe_two"]
    assert commits and commits[0][0] == ["strategies/probe_two.py"]


def test_runner_exception_alerts(env):
    settings, store, notifier, _ = env

    class Boom(FakeMessages):
        def tool_runner(self, **kwargs):
            raise RuntimeError("api down")

    client = FakeClient([])
    client.beta.messages = Boom([])
    out = run_research(settings, store, notifier, client=client)
    assert out["status"] == "error" and "api down" in out["error"]
    assert notifier.sent
```

说明：已在 anthropic==1.15.0 上核实：`@beta_tool` 返回 `BetaFunctionTool`，有 `.call(input)`、`.func`、`.name`；`client.beta.messages.tool_runner` 接受 `output_config`、`system`、`tools`、`max_tokens`、`messages`。

**Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_research_loop.py -v
```

预期：FAIL，`ImportError: cannot import name 'RESEARCH_SYSTEM' from 'agents.research'`。

**Step 3: 最小实现**

在 `agents/research.py` 顶部 import 区追加（与 Task 5.1 的 import 合并）：

```python
from anthropic import beta_tool

from agents.llm import LLM, BudgetExceeded
from agents.playbook import git_commit
from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE, SPLITS
from engine.integrity import verify
from notify.format import format_alert
```

在文件末尾追加提示词：

```python
RESEARCH_SYSTEM = """你是 RSIQS 的量化研究员。RSIQS 是 A 股盘前推荐 + 模拟交易系统，你的工作是提出并检验新的选股策略。
You are the quant researcher of RSIQS (A-share pre-open picks + simulated trading).

## 目标 Goal
在“每日最多 ≤5 笔新开仓（max 5 picks/day）”的约束下，提高扣除交易成本（佣金、印花税、过户费、10bp 滑点）后、
相对沪深 300 的风险调整超额收益（risk-adjusted excess return after costs）。
回测引擎已模拟 T+1、涨跌停无法成交、停牌、整手交易；你不能修改 engine/，只能在 strategies/ 下写新策略。

## 数据切分 Data splits
- train 2016-2024：用于开发；val 2025-01 至 2026-06：门槛检验。
- holdout 2026-07 以后：你只会得到 holdout_pass 通过/不通过（pass/fail only），永远看不到具体数值。
  不要试图通过反复提交来“探测” holdout；每次评估都被记录并计入多重检验惩罚。

## 过拟合 Overfitting
- 每一次 run_evaluation 都会计入全局试验次数 n_trials。门槛包含 Deflated Sharpe Ratio（DSR）：
  试验越多，同样的夏普越可能是运气，DSR 越低，越难过门槛。
- 因此：少而精。本次会话最多评估若干次（见用户消息），宁可提交 1-3 个论证充分的变体，也不要做参数网格搜索。
- 不要微调阈值去拟合 train/val；参数取整、取常见值（如 5/10/20/60 日），规则越简单越好。
- 警惕数据窥探：只使用 DataView.history() 给出的截至 asof 的数据，引擎会对未来数据抛 LookaheadError。

## 好的假设 Good hypotheses
- 优先有经济学或市场微观结构依据的想法：例如动量/反转、流动性与成交额、波动率、涨跌停后的行为、
  A 股散户主导下的过度反应、T+1 带来的隔夜风险等。说清“为什么这个效应应该存在、为什么还没被套利掉”。
- 先读现任策略和最近的试验记录（recent_trials），避免重复已失败的方向。

## 写策略的规范 Strategy file rules
- 模块必须定义 STRATEGY = <实例>，实例有 name 属性（与文件名一致）和 select(view: DataView) -> list[Pick]（按优先级降序）。
- 只能 import：math, statistics, numpy, pandas, dataclasses, typing, engine.types, engine.dataview, engine.rules, strategies.base。
  禁止文件/网络/exec/eval/双下划线属性等；违规会被沙箱拒绝。
- 在模块 docstring 里写明：假设（hypothesis）、经济学理由、与现任策略的差异、预期失效情形。
- 每个 Pick 需给出 entry_max、stop、target、hold_days 和中文 reason。

## 工作流程 Workflow
1. list_strategies / read_file 阅读 strategies/baseline_momo.py、engine/dataview.py、engine/types.py 等。
2. recent_trials 查看历史试验。
3. write_strategy 写入新策略（新名字，不能覆盖现任）。
4. run_evaluation 评估；gate_pass 且 holdout_pass 的策略会自动进入影子盘（shadow），之后由前向表现决定是否晋升。
5. 结束时用中文给出简短总结：尝试了哪些假设、结果如何、下次建议的方向。
"""
```

继续在文件末尾追加 `build_brief` 与 `run_research`：

```python
def build_brief(store: Store, today: date, max_evals: int) -> str:
    splits = {k: [str(a), str(b) if b else "latest"] for k, (a, b) in SPLITS.items()}
    return (
        f"今天是 {today.isoformat()}。请开始本周研究会话。\n"
        f"- 当前现任策略：{store.strategies_with_status('incumbent')}\n"
        f"- 影子盘策略：{store.strategies_with_status('shadow')}\n"
        f"- 历史试验总数 n_trials：{store.trial_count()}\n"
        f"- 本次会话最多 run_evaluation {max_evals} 次。\n"
        f"- 数据切分：{json.dumps(splits)}\n"
        f"- 回测门槛 GATE：{json.dumps(GATE)}；holdout 门槛仅以 pass/fail 返回"
        f"（共 {len(HOLDOUT_GATE)} 项条件）。\n"
        "先阅读现任策略和最近试验，再提出 1-3 个有经济学依据的新假设。"
    )


def _final_text(message) -> str:
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def run_research(settings: Settings, store: Store, notifier, client=None) -> dict:
    root = Path(settings.root)
    today = date.today()
    summary: dict = {"status": "ok", "cost_cny": 0.0, "messages": 0, "written": [],
                     "results": [], "final_text": "", "error": ""}

    def alert(title: str, body: str) -> None:
        notifier.send_text(format_alert(title, body))

    bad = verify(root)
    if bad:
        alert("研究循环中止：锁定区哈希不符", "\n".join(bad))
        return {**summary, "status": "integrity_failed", "error": ", ".join(bad)}

    llm = LLM(settings, store, client=client)
    try:
        llm.check_budget(essential=False)
    except BudgetExceeded:
        notifier.send_text("研究循环跳过：本月 LLM 预算已用完")
        return {**summary, "status": "budget_exceeded"}

    # 运行时读取模块常量，便于 smoke 时改为 1
    ctx = ResearchContext(root=root, store=store, today=today, max_evals=MAX_EVALS_PER_SESSION)

    @beta_tool
    def list_strategies() -> str:
        """列出 strategies/ 下所有策略及其状态（candidate/shadow/incumbent/retired/null）。"""
        return tool_list_strategies(ctx)

    @beta_tool
    def read_file(path: str) -> str:
        """只读文件。允许 strategies/*.py、engine/*.py、playbook/*，使用相对路径。

        Args:
            path: 相对项目根目录的路径，例如 strategies/baseline_momo.py
        """
        return tool_read_file(ctx, path)

    @beta_tool
    def write_strategy(name: str, code: str) -> str:
        """写入新策略 strategies/<name>.py 并标记为 candidate。代码须通过沙箱检查。

        Args:
            name: 策略名，匹配 ^[a-z][a-z0-9_]{2,40}$，不能是现任策略
            code: 完整 Python 源码，模块 docstring 写明假设，并定义 STRATEGY
        """
        return tool_write_strategy(ctx, name, code)

    @beta_tool
    def run_evaluation(name: str) -> str:
        """在隔离子进程中回测评估策略，返回 train/val 指标与 gate_pass/holdout_pass。每次调用计入试验次数。

        Args:
            name: 已写入的策略名
        """
        return tool_run_evaluation(ctx, name)

    @beta_tool
    def recent_trials() -> str:
        """返回最近 20 次试验记录（所有策略）。"""
        return tool_recent_trials(ctx)

    tools = [list_strategies, read_file, write_strategy, run_evaluation, recent_trials]
    month = (today.year, today.month)
    last = None
    try:
        runner = llm.client.beta.messages.tool_runner(
            model=settings.llm_model,
            max_tokens=16000,
            system=RESEARCH_SYSTEM,
            tools=tools,
            messages=[{"role": "user", "content": build_brief(store, today, ctx.max_evals)}],
            output_config={"effort": "high"},
        )
        for message in runner:
            last = message
            summary["messages"] += 1
            usage = getattr(message, "usage", None)
            cost = llm.cost_cny(usage)
            store.add_llm_usage("research", int(getattr(usage, "input_tokens", 0) or 0),
                                int(getattr(usage, "output_tokens", 0) or 0), cost)
            summary["cost_cny"] += cost
            if store.month_llm_cost(*month) >= settings.monthly_budget_cny:
                summary["status"] = "budget_stopped"
                break
    except Exception as e:  # API 错误等：告警但仍做完整性检查与提交
        summary["status"] = "error"
        summary["error"] = f"{type(e).__name__}: {e}"
        alert("研究循环异常", summary["error"])

    summary["written"] = sorted(ctx.written)
    summary["results"] = ctx.results
    summary["final_text"] = _final_text(last) if last is not None else ""

    bad = verify(root)
    if bad:
        alert("研究循环中止：会话后锁定区哈希不符", "\n".join(bad))
        return {**summary, "status": "integrity_failed", "error": ", ".join(bad)}

    if ctx.written:
        git_commit([f"strategies/{n}.py" for n in summary["written"]],
                   f"research: {today.isoformat()} new strategies {', '.join(summary['written'])}")

    passed = [r["strategy"] for r in ctx.results if r.get("new_status") == "shadow"]
    notifier.send_text(
        f"🔬 研究会话 {today.isoformat()}（{summary['status']}）\n"
        f"新策略：{', '.join(summary['written']) or '无'}；评估 {ctx.evals_used} 次；进入影子盘：{', '.join(passed) or '无'}\n"
        f"花费：{summary['cost_cny']:.2f} 元\n{summary['final_text'][:1200]}"
    )
    return summary
```

说明：
- `LLM` 仅用于 `cost_cny`/`check_budget` 及持有 client（`client=None` 时由 `LLM` 创建 `anthropic.Anthropic`）。`tool_runner` 不发送 `thinking`/`temperature`/`tool_choice`（Opus 5.5 约束）。
- 预算检查在每条消息记账后进行，超限立即 `break`，正在进行中的工具调用不会再继续。
- 会话后完整性失败时不提交 git，留给人工排查；写入的策略文件保留在工作区。

**Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_research_tools.py tests/test_research_loop.py -v
```

预期：全部 PASS。

**Step 5: 提交**

```bash
git add agents/research.py tests/test_research_loop.py
git commit -m "feat: research loop with tool_runner, budget stop and integrity checks"
```

（提交信息末尾附加 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` trailer。）

**Manual smoke step（真实 Claude API，一次研究会话，仅 1 次评估）**

前置：已完成 `python -m jobs.bootstrap`（数据缓存、现任策略、integrity manifest 已就绪），`.env` 中填入真实 `ANTHROPIC_API_KEY`。

```bash
uv run python -c "from agents import research; research.MAX_EVALS_PER_SESSION = 1; from agents.research import run_research; from config import get_settings; from engine.store import Store; from notify.wecom import NullNotifier; s = get_settings(); n = NullNotifier(); print(run_research(s, Store(s.root / s.db_path), n)); print(n.sent)"
```

检查点：
- 返回 `status` 为 `ok`；推送摘要中“评估 N 次”的 N ≤ 1（第二次 `run_evaluation` 返回 `ERROR: evaluation limit reached`）。
- `strategies/` 下出现新文件，docstring 写有假设；`store.get_status(<name>)` 为 `candidate` 或 `shadow`。
- `uv run python -c "from config import get_settings; from engine.store import Store; s=get_settings(); import datetime as d; t=d.date.today(); print(Store(s.root / s.db_path).month_llm_cost(t.year, t.month))"` 显示有 research 花费。
- `git log -1` 显示 `research: ...` 提交；`uv run python -c "from engine.integrity import verify; from pathlib import Path; print(verify(Path('.')))"` 输出 `[]`。


---

## Part 6: 任务编排、调度与端到端测试

> 约定补充（契约在本部分内的歧义，取最简一致方案）：
> - `run_job` 返回 `True` 表示该任务当日"已完成"（本次成功，或此前已成功而跳过），`False` 表示本次失败。
> - `bootstrap` 把股票池写入 `store.kv_set("universe", codes)`，并在锚定日 `anchor = common.last_closed_day(now, calendar)`（今天是交易日且已过 15:10 则为今天，否则为之前最近的交易日）为每个账户记录初始净值 `capital`，`kv "bench_start"` = anchor；`settle` 以 `h:active / h:candidate / bench:csi300` 三个核心账户的最小 `last_nav_date` 决定补跑起点。
> - `settle` 每次运行只在有待结算日时刷新一次缓存/指数（`run_job("settle_data", 最后待结算日, force=True)`，失败即告警并退出）；单日日线覆盖率 <50% 或缺指数视为数据未更新，抛 `DataError`，停止后续补跑。
> - 行情缓存 `BarCache` 与 `Store` 共用 `settings.db_path` 同一个 sqlite 文件（不同表），其他部分（如 `engine.run_eval`）按同一路径构造即可读到缓存。
> - `bootstrap` 若 `kv "playbook_released"` 不存在，则写入锚定日 ISO 字符串（视为 v1 的发布日），保证 `maybe_release` 的 10 个交易日间隔从启动日起算。
> - `monitor` 去重键持久化在 `kv "monitor_sent:<ISO日期>"`（进程重启不重复告警）；`check_once(d, store, client, notifier) -> list[str]`。
> - `jobs.review` 只复盘"不晚于今天、已结算"的最近一个交易日（契约原文）；该日已复盘则跳过。
> - `jobs.premarket_final` 若当日 `premarket_scan` 未完成（开机晚），先内联补跑扫描再定稿。
> - 所有入口 `main(now: datetime | None = None)`，通过 `common.context()` 取依赖；测试中 monkeypatch `jobs.common.context`。
> - `jobs/common.py` 额外提供 `make_llm(settings, store)` 工厂（入口通过 `common.make_llm` 调用，测试打桩）与 `load_calendar`、`setup_logging`；入口一律以 `from jobs import common` 引用以便 monkeypatch 生效。
> - 端到端测试依赖 Part 2 的 `Store` 可跨线程使用（`check_same_thread=False` + 锁），因为 `run_final` 在两个线程里调用 `finalize`。
> - 测试共享假对象放在 `tests/jobfakes.py`，以 `from tests.jobfakes import ...` 导入；为此新增空文件 `tests/__init__.py`（若已存在则保留），使 `tests` 成为常规包，避免与 site-packages 中同名包冲突。
> - 开机补跑用单独的计划任务 `\RSIQS\Catchup`（AtLogOn，延迟 3 分钟，顺序执行 settle → review 两个动作），满足"settle also AtLogOn"且保证复盘在结算之后。


### Task 6.1: jobs/common.py 与测试假对象

**Files:**
- Create: `jobs/common.py`
- Create: `tests/__init__.py`（空文件；已存在则跳过）
- Create: `tests/jobfakes.py`（本部分所有 jobs 测试共享的假对象）
- Test: `tests/test_jobs_common.py`

**Step 1: 写共享假对象（测试基础设施，非产品代码）**

`tests/jobfakes.py`：

```python
"""jobs 测试共享假对象：不联网、不调用 Claude。"""
from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from config import Settings
from engine.calendar import TradingCalendar
from engine.store import Store
from notify.wecom import NullNotifier

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
    return Settings(anthropic_api_key="test-key", wecom_webhook_url="", root=root)


REPO_ROOT = Path(__file__).resolve().parent.parent


def prepare_root(tmp: Path, monkeypatch) -> Path:
    """临时项目根：复制仓库 playbook/*.md 提示词，并把 playbook 目录、git 提交指向临时根。"""
    import agents.playbook as pb

    (tmp / "playbook").mkdir(parents=True, exist_ok=True)
    for md in (REPO_ROOT / "playbook").glob("*.md"):
        shutil.copy(md, tmp / "playbook" / md.name)
    monkeypatch.setattr(pb, "PLAYBOOK_DIR", tmp / "playbook")
    monkeypatch.setattr(pb, "git_commit", lambda *a, **k: False)
    import agents.review as rv
    if hasattr(rv, "git_commit"):
        monkeypatch.setattr(rv, "git_commit", lambda *a, **k: False)
    return tmp


class FakeClient:
    """AkClient 替身。current 指定"今天"，spot() 按该日开盘价返回竞价/盘中快照。"""

    def __init__(self, dates, panel, names, index):
        self.dates, self.panel, self.names, self.index = list(dates), panel, names, index
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

    def spot(self):
        self._hit("spot")
        if self.spot_df is not None:
            return self.spot_df.copy()
        ts = pd.Timestamp(self.current)
        rows = []
        for c, df in self.panel.items():
            if ts in df.index:
                r = df.loc[ts]
                rows.append({"code": c, "name": self.names[c], "open": r["open"],
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


class FakeCache:
    """BarCache 替身：内存面板，按日期切片。"""

    def __init__(self, panel, names, index):
        self.panel, self.names, self.index = panel, dict(names), index
        self.updates: list[date] = []
        self.saved_index: dict[str, pd.Series] = {}

    def update(self, codes, end, start_default=date(2015, 6, 1)):
        self.updates.append(end)

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
    """LLM 替身：盘前全部 keep（沿用量化价位），复盘固定给出一条带证据的新规则。
    candidates 由测试在主线程预先填入（避免在 finalize 线程里访问 sqlite）。"""

    def __init__(self):
        self.candidates = []
        self.purposes: list[str] = []

    def check_budget(self, essential):
        return None

    def cost_cny(self, usage):
        return 0.0

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0,
              max_tokens=16000, essential=False):
        from agents.premarket import PremarketReview, ReviewedPick
        from agents.review import Lesson, ReviewOutput

        self.purposes.append(purpose)
        if schema is PremarketReview:
            picks = [ReviewedPick(code=p.code, action="keep", confidence=1.0 - i * 0.05,
                                  entry_max=p.entry_max, stop=p.stop, target=p.target,
                                  reason="测试保留") for i, p in enumerate(self.candidates)]
            return PremarketReview(market_view="震荡", picks=picks)
        if schema is ReviewOutput:
            return ReviewOutput(summary="复盘：测试摘要", lessons=[Lesson(
                action="add", rule_id="", text="高开超过 3% 的候选放弃",
                evidence=f"{purpose} 测试证据 600001 高开回落")])
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

```

> 说明：`prepare_root` 依赖 Part 4/5 已提交的 `playbook/premarket_system.md`、`playbook/review_system.md`；若 `agents.review` 以 `from agents.playbook import git_commit` 方式导入，也一并打桩。

**Step 2: 写 run_job 的失败测试**

`tests/test_jobs_common.py`：

```python
from datetime import date

from engine.store import Store
from jobs.common import run_job
from notify.wecom import NullNotifier

D = date(2026, 10, 16)


def test_run_job_success_marks_ok_and_skips_rerun():
    store, notifier, calls = Store(":memory:"), NullNotifier(), []
    assert run_job("settle", D, lambda: calls.append(1), store, notifier) is True
    assert store.job_done("settle", D)
    assert run_job("settle", D, lambda: calls.append(2), store, notifier) is True
    assert calls == [1]  # 已完成 -> 跳过


def test_run_job_force_reruns():
    store, notifier, calls = Store(":memory:"), NullNotifier(), []
    run_job("review", D, lambda: calls.append(1), store, notifier)
    assert run_job("review", D, lambda: calls.append(2), store, notifier, force=True)
    assert calls == [1, 2]


def test_run_job_error_marks_error_and_alerts():
    store, notifier = Store(":memory:"), NullNotifier()

    def boom():
        raise RuntimeError("数据源挂了")

    assert run_job("premarket_scan", D, boom, store, notifier) is False
    assert not store.job_done("premarket_scan", D)
    assert len(notifier.sent) == 1
    assert "premarket_scan" in str(notifier.sent[0])
    assert "数据源挂了" in str(notifier.sent[0])
    # 失败后可重试
    assert run_job("premarket_scan", D, lambda: None, store, notifier) is True
    assert store.job_done("premarket_scan", D)


def test_run_job_alert_failure_does_not_raise():
    store = Store(":memory:")

    class Broken:
        def send_text(self, content):
            raise OSError("网络断开")

    assert run_job("x", D, lambda: 1 / 0, store, Broken()) is False
```

**Step 3: 运行，确认失败**

```
uv run pytest tests/test_jobs_common.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'jobs.common'`。

**Step 4: 最小实现**

`jobs/common.py`：

```python
"""任务入口公共设施：幂等运行、告警、依赖装配。"""
from __future__ import annotations

import logging
import traceback
from datetime import date, datetime, time
from pathlib import Path

from config import get_settings
from data.akshare_client import AkClient
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.store import Store
from notify.format import format_alert
from notify.wecom import NullNotifier, WeCom

log = logging.getLogger("rsiqs.jobs")


def setup_logging(root: Path) -> None:
    """日志写入 <root>/logs/jobs.log（重复调用不重复挂 handler）。"""
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = str(log_dir / "jobs.log")
    if not any(getattr(h, "baseFilename", None) == target for h in log.handlers):
        h = logging.FileHandler(target, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    log.setLevel(logging.INFO)


INDEX_SYMBOL = "sh000300"
BENCH_ACCOUNT = "bench:csi300"
LLM_ACCOUNTS = ("h:active", "h:candidate")
DEFAULT_INCUMBENT = "baseline_momo"
CLOSE_READY = time(15, 10)  # 当日日线视为可用的最早时刻


def today() -> date:
    return datetime.now().date()


def last_closed_day(now: datetime, calendar: TradingCalendar) -> date:
    """不晚于 now、且已收盘（>=15:10）的最近交易日。"""
    d = now.date()
    if calendar.is_trading_day(d) and now.time() >= CLOSE_READY:
        return d
    return calendar.prev_day(d)


def incumbent_strategy(store: Store) -> str:
    names = store.strategies_with_status("incumbent")
    return names[0] if names else DEFAULT_INCUMBENT


def make_llm(settings, store: Store):
    """LLM 工厂（测试中打桩为 FakeLLM）。"""
    from agents.llm import LLM

    return LLM(settings, store)


def run_job(job: str, d: date, fn, store: Store, notifier, force: bool = False) -> bool:
    """幂等执行：已成功则跳过（返回 True）；成功标记 ok；异常标记 error 并告警（返回 False）。"""
    if not force and store.job_done(job, d):
        log.info("skip %s %s (already ok)", job, d)
        return True
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — 任务边界统一兜底
        store.mark_job(job, d, "error")
        log.error("job %s %s failed: %s\n%s", job, d, e, traceback.format_exc())
        try:
            notifier.send_text(format_alert(f"任务失败 {job} {d.isoformat()}", f"{type(e).__name__}: {e}"))
        except Exception as ne:  # noqa: BLE001
            log.error("alert failed: %s", ne)
        return False
    store.mark_job(job, d, "ok")
    log.info("job %s %s ok", job, d)
    return True


def load_calendar(store: Store, client: AkClient, now: date | None = None) -> TradingCalendar:
    """交易日历缓存在 kv "calendar"（ISO 字符串列表），kv "calendar_fetched" 记录刷新日；
    每天最多拉取一次，拉取失败时用旧缓存。"""
    now = now or today()
    dates = store.kv_get("calendar")
    if not dates or store.kv_get("calendar_fetched") != now.isoformat():
        try:
            dates = [x.isoformat()[:10] for x in client.trade_calendar()]
            store.kv_set("calendar", dates)
            store.kv_set("calendar_fetched", now.isoformat())
        except Exception:
            if not dates:
                raise
            log.warning("trade_calendar refresh failed, using cached calendar")
    return TradingCalendar(date.fromisoformat(x) for x in dates)


def context():
    """装配依赖：(settings, store, client, cache, calendar, notifier)。"""
    settings = get_settings()
    setup_logging(settings.root)
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = settings.root / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    client = AkClient()
    cache = BarCache(db_path, client)
    notifier = (WeCom(settings.wecom_webhook_url, log_dir=settings.root / "logs")
                if settings.wecom_webhook_url else NullNotifier())
    calendar = load_calendar(store, client)
    return settings, store, client, cache, calendar, notifier
```

补一条日历缓存测试，追加到 `tests/test_jobs_common.py` 末尾（import 可上移到文件顶部）：

```python
from jobs.common import load_calendar


class CalClient:
    def __init__(self):
        self.n = 0

    def trade_calendar(self):
        self.n += 1
        if self.n > 1:
            raise RuntimeError("down")
        return [date(2026, 10, 15), date(2026, 10, 16)]


def test_load_calendar_cached_per_day_and_falls_back():
    store, c = Store(":memory:"), CalClient()
    cal1 = load_calendar(store, c, now=D)
    cal2 = load_calendar(store, c, now=D)
    assert c.n == 1 and cal2.is_trading_day(D)
    cal3 = load_calendar(store, c, now=date(2026, 10, 17))  # 刷新失败 -> 旧缓存
    assert c.n == 2 and cal3.last() == D and cal1.last() == D
```

再补一条现任策略回退测试：

```python
from datetime import datetime, time

from engine.calendar import TradingCalendar
from jobs.common import incumbent_strategy, last_closed_day


def test_incumbent_strategy_default_and_store():
    store = Store(":memory:")
    assert incumbent_strategy(store) == "baseline_momo"
    store.set_status("momo_v2", "incumbent", D)
    assert incumbent_strategy(store) == "momo_v2"


def test_last_closed_day():
    c = TradingCalendar([date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19)])
    assert last_closed_day(datetime.combine(D, time(15, 9)), c) == date(2026, 10, 15)
    assert last_closed_day(datetime.combine(D, time(15, 10)), c) == D
    assert last_closed_day(datetime(2026, 10, 18, 9, 0), c) == D  # 周日
```

**Step 5: 运行，确认通过**

```
uv run pytest tests/test_jobs_common.py -v
```
预期：7 passed。

**Step 6: 提交**

```
git add jobs/common.py tests/__init__.py tests/jobfakes.py tests/test_jobs_common.py
git commit -m "feat: jobs.common run_job/context with calendar cache"
```
（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`。）


### Task 6.2: jobs/bootstrap.py

**Files:**
- Create: `jobs/bootstrap.py`
- Test: `tests/test_jobs_bootstrap.py`

**Step 1: 写失败测试**

`tests/test_jobs_bootstrap.py`：

```python
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


def test_bootstrap_on_weekend_starts_from_last_trading_day(tmp_path, monkeypatch):
    dates, _, store, _, _, _ = setup(tmp_path, monkeypatch)
    sat = next_saturday(dates[99])
    bootstrap.main(now=datetime.combine(sat, time(10, 0)))
    assert store.last_nav_date("h:active") == max(x for x in dates if x < sat)


def test_bootstrap_before_close_anchors_previous_day(tmp_path, monkeypatch):
    dates, _, store, _, _, _ = setup(tmp_path, monkeypatch)
    bootstrap.main(now=datetime.combine(dates[100], time(9, 0)))
    assert store.last_nav_date("h:active") == dates[99]
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_jobs_bootstrap.py -v
```
预期：FAIL，`ImportError: cannot import name 'bootstrap' from 'jobs'`。

**Step 3: 最小实现**

`jobs/bootstrap.py`：

```python
"""一次性初始化：python -m jobs.bootstrap（可重复运行，已存在的账户/playbook 不覆盖）。"""
from __future__ import annotations

from datetime import date, datetime

import agents.playbook as playbook
from engine import integrity
from engine.ledger import Account
from jobs import common

HISTORY_START = date(2015, 6, 1)


def account_names(store) -> list[str]:
    return [f"q:{common.incumbent_strategy(store)}", *common.LLM_ACCOUNTS, common.BENCH_ACCOUNT]


def main(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    root = settings.root
    for sub in ("data", "logs", "playbook", ".integrity"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    codes = sorted(client.universe())
    store.kv_set("universe", codes)
    spot = client.spot()
    cache.set_names({str(c): str(n) for c, n in zip(spot["code"], spot["name"])})
    cache.update(codes, end=now.date(), start_default=HISTORY_START)
    cache.save_index(common.INDEX_SYMBOL, client.index_daily(common.INDEX_SYMBOL))

    if not store.strategies_with_status("incumbent"):
        store.set_status(common.DEFAULT_INCUMBENT, "incumbent", now.date())

    anchor = common.last_closed_day(now, calendar)
    for key in ("bench_start", "playbook_released"):
        if store.kv_get(key) is None:
            store.kv_set(key, anchor.isoformat())
    created = []
    for name in account_names(store):
        if store.load_account(name) is None:
            store.save_account(Account(name=name, cash=settings.capital))
            store.record_nav(name, anchor, settings.capital)
            created.append(name)

    for kind in ("active", "candidate"):
        if not (playbook.PLAYBOOK_DIR / f"{kind}.json").exists():
            playbook.save(kind, {"version": 1, "created": now.date().isoformat(), "rules": []})

    integrity.write_manifest(root)
    msg = (f"RSIQS 初始化完成：股票池 {len(codes)} 只，起始日 {anchor.isoformat()}，"
           f"新建账户 {', '.join(created) or '无'}")
    notifier.send_text(msg)
    print(msg)
    return {"universe": len(codes), "anchor": anchor.isoformat(), "created": created}


if __name__ == "__main__":
    main()
```

说明：`cache.update` 首次下载 300+500 只股票约 10 年日线，耗时可能 30-60 分钟（AKShare 限速），属预期；中断后重跑为增量。

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_bootstrap.py -v
```
预期：4 passed。

**Step 5: 提交**

```
git add jobs/bootstrap.py tests/test_jobs_bootstrap.py
git commit -m "feat: jobs.bootstrap initializes accounts, playbooks, cache, manifest"
```
（附 Co-Authored-By trailer。）

### Task 6.3: jobs/settle.py（收盘结算 + 多日补跑）

**Files:**
- Create: `jobs/settle.py`
- Test: `tests/test_jobs_settle.py`

规则回顾：待结算区间 = `calendar.next_day(min(核心账户 last_nav_date))` 到"已收盘的最近交易日"（今天是交易日且 `now >= 15:10` 才包含今天）。每天用 `run_job("settle", d)` 包裹；某天失败即停止后续补跑（后一天依赖前一天持仓）。每个账户若 `last_nav_date >= d` 则跳过，保证重复执行不会重复成交。

**Step 1: 写失败测试**

`tests/test_jobs_settle.py`：

```python
from datetime import datetime, time

import pytest

import engine.integrity as integrity
import engine.promotion as promotion
from engine.types import Pick
from jobs import bootstrap, settle
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, cal, install_context,
                            make_dates, make_index, make_panel, make_settings, new_store,
                            next_saturday, prepare_root)

ACCOUNTS = ["q:baseline_momo", "h:active", "h:candidate", "bench:csi300"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(integrity, "write_manifest", lambda r: None)
    dates = make_dates()
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    client.current = dates[100]
    cache, store, notifier = FakeCache(panel, NAMES, index), new_store(), NullNotifier()
    settings = make_settings(root)
    install_context(monkeypatch, settings, store, client, cache, cal(dates), notifier)
    bootstrap.main(now=datetime.combine(dates[100], time(20, 0)))
    notifier.sent.clear()
    decided = []
    monkeypatch.setattr(promotion, "decide", lambda s, d, c: decided.append(d) or [])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0,
                stop=0.01, target=999.0, hold_days=5, reason="测试")
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True})
    return dict(dates=dates, store=store, cache=cache, notifier=notifier,
                settings=settings, index=index, decided=decided)


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm))


def test_catch_up_multiple_missed_days(env):
    dates, store = env["dates"], env["store"]
    settled = settle.main(now=at(dates[104], 16))
    assert settled == dates[101:105]
    for acct in ACCOUNTS:
        idx = store.nav_series(acct).index
        assert [ts.date() for ts in idx] == dates[100:105], acct
        assert store.job_done("settle", dates[101])
    active = store.load_account("h:active")
    assert any(f.side == "BUY" and f.code == "600001" and f.date == dates[101]
               for f in active.trades)
    assert store.load_account("q:baseline_momo").trades  # 量化账户按策略自行选股成交
    assert store.load_account("h:candidate").trades == []  # 无推荐 -> 不交易
    bench = store.nav_series("bench:csi300")
    expected = env["settings"].capital * env["index"].iloc[104] / env["index"].iloc[100]
    assert bench.iloc[-1] == pytest.approx(expected)
    assert env["cache"].updates[-1] == dates[104]  # 补跑前一次性刷新到最后一天
    assert env["decided"] == dates[101:105]  # 每个结算日跑一次晋升判定


def test_rerun_is_idempotent(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[102], 16))
    n_trades = len(store.load_account("h:active").trades)
    cash = store.load_account("q:baseline_momo").cash
    assert settle.main(now=at(dates[102], 17)) == []
    # 即使绕过 job 标记强制重算某天，账户也不会重复成交
    settle.settle_day(dates[102], env["settings"], store, env["cache"], cal(dates))
    assert len(store.load_account("h:active").trades) == n_trades
    assert store.load_account("q:baseline_momo").cash == cash
    assert len(store.nav_series("h:active")) == 3


def test_today_not_settled_before_1510(env):
    dates, store = env["dates"], env["store"]
    assert settle.main(now=at(dates[102], 15, 5)) == [dates[101]]
    assert store.last_nav_date("h:active") == dates[101]
    assert settle.main(now=at(dates[102], 15, 10)) == [dates[102]]
    assert store.last_nav_date("h:active") == dates[102]


def test_weekend_settles_up_to_friday(env):
    dates, store = env["dates"], env["store"]
    sat = next_saturday(dates[103])
    settle.main(now=datetime.combine(sat, time(10)))
    assert store.last_nav_date("bench:csi300") == max(d for d in dates if d < sat)


def test_stale_data_aborts_with_alert(env, monkeypatch):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    real = cache.load_panel
    monkeypatch.setattr(cache, "load_panel", lambda codes, a, b: real(codes, a, dates[100]))
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.last_nav_date("h:active") == dates[100]
    assert not store.job_done("settle", dates[101])
    assert any("覆盖不足" in str(m) for m in env["notifier"].sent)


def test_shadow_account_created_and_broken_strategy_skipped(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    store.set_status("momo_shadow", "shadow", dates[100])
    store.set_status("broken_one", "shadow", dates[100])
    real = settle.load_strategy

    def fake_load(name):
        if name == "broken_one":
            raise ImportError("坏策略")
        return real("baseline_momo")

    monkeypatch.setattr(settle, "load_strategy", fake_load)
    assert settle.main(now=at(dates[101], 16)) == [dates[101]]
    assert store.last_nav_date("q:momo_shadow") == dates[101]
    assert store.load_account("q:momo_shadow").trades
    assert store.last_nav_date("q:broken_one") is None  # 选股失败 -> 当日不记净值
    assert any("broken_one" in str(m) for m in env["notifier"].sent)


def test_promotion_events_are_notified(env, monkeypatch):
    dates = env["dates"]
    monkeypatch.setattr(promotion, "decide",
                        lambda s, d, c: [promotion.Event("promote", "momo_v2", "超额 8%")])
    settle.main(now=at(dates[101], 16))
    assert any("momo_v2" in str(m) and "晋升" in str(m) for m in env["notifier"].sent)
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_jobs_settle.py -v
```
预期：FAIL，`ImportError: cannot import name 'settle' from 'jobs'`。

**Step 3: 最小实现**

`jobs/settle.py`：

```python
"""收盘结算：15:35 定时 + 开机补跑。python -m jobs.settle"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from data.akshare_client import DataError
from engine import promotion
from engine.backtest import bars_for_day
from engine.broker import SimBroker
from engine.dataview import DataView
from engine.ledger import Account
from jobs import common
from notify.format import format_alert
from strategies.base import load_strategy

CORE_ACCOUNTS = (*common.LLM_ACCOUNTS, common.BENCH_ACCOUNT)
LOOKBACK_CAL_DAYS = 550  # 约 370 个交易日，供策略计算因子
MIN_COVERAGE = 0.5       # 当日有日线的股票占比低于此值视为数据未更新
KIND_CN = {"promote": "晋升", "demote": "降级", "retire": "淘汰"}


def pending_days(store, calendar, now: datetime) -> list[date]:
    lasts = [x for x in (store.last_nav_date(n) for n in CORE_ACCOUNTS) if x is not None]
    if not lasts:
        return []  # 尚未 bootstrap
    start, end = calendar.next_day(min(lasts)), common.last_closed_day(now, calendar)
    return calendar.between(start, end) if start <= end else []


def tracked_codes(store) -> list[str]:
    """股票池 ∪ 各账户持仓（股票池变动后旧持仓仍能按规则退出）。"""
    codes = set(store.kv_get("universe") or [])
    for name in store.list_accounts():
        acc = store.load_account(name)
        if acc is not None:
            codes.update(acc.positions)
    return sorted(codes)


def refresh_data(codes: list[str], end: date, client, cache) -> None:
    cache.update(codes, end=end)
    cache.save_index(common.INDEX_SYMBOL, client.index_daily(common.INDEX_SYMBOL))


def _index_close(series: pd.Series, d: date) -> float:
    s = series.copy()
    s.index = pd.to_datetime(s.index).normalize()
    ts = pd.Timestamp(d)
    if ts not in s.index:
        raise DataError(f"沪深300 指数缺少 {d.isoformat()}，数据可能未更新")
    return float(s.loc[ts])


def _settled(store, name: str, d: date) -> bool:
    last = store.last_nav_date(name)
    return last is not None and last >= d


def settle_day(d: date, settings, store, cache, calendar) -> list[str]:
    """结算单个交易日；已记录 d 日净值的账户跳过（幂等）。返回警告列表。"""
    codes = tracked_codes(store)
    panel, names = cache.load_panel(codes, d - timedelta(days=LOOKBACK_CAL_DAYS), d)
    bars = bars_for_day(panel, names, d)
    if len(bars) < max(1, int(len(codes) * MIN_COVERAGE)):
        raise DataError(f"{d.isoformat()} 日线覆盖不足 {len(bars)}/{len(codes)}，数据可能未更新")
    bench_start = date.fromisoformat(store.kv_get("bench_start"))
    index = cache.load_index(common.INDEX_SYMBOL)
    bench_equity = settings.capital * _index_close(index, d) / _index_close(index, bench_start)

    closes = {c: float(df["close"].iloc[-1]) for c, df in panel.items() if len(df)}
    view = DataView(panel, names, asof=calendar.prev_day(d))
    broker, per_pick, warnings = SimBroker(), settings.capital / settings.max_picks, []

    def run(name: str, picks) -> None:
        acc = store.load_account(name)
        if acc is None:
            acc = Account(name=name, cash=settings.capital)
            store.record_nav(name, calendar.prev_day(d), settings.capital)
        broker.execute_day(acc, d, bars, picks, calendar, per_pick)
        store.record_nav(name, d, acc.equity(closes))
        store.save_account(acc)

    for status in ("incumbent", "shadow"):
        for strat in store.strategies_with_status(status):
            name = f"q:{strat}"
            if _settled(store, name, d):
                continue
            try:
                picks = load_strategy(strat).select(view)[: settings.max_picks]
            except Exception as e:  # noqa: BLE001 — 单个策略出错不阻塞其他账户
                common.log.exception("strategy %s failed on %s", strat, d)
                warnings.append(f"策略 {strat} 选股失败，当日跳过：{type(e).__name__}: {e}")
                continue
            run(name, picks)

    for name in common.LLM_ACCOUNTS:
        if not _settled(store, name, d):
            rec = store.load_recommendation(d, name)
            run(name, rec[0] if rec else [])

    if not _settled(store, common.BENCH_ACCOUNT, d):
        store.record_nav(common.BENCH_ACCOUNT, d, bench_equity)
    return warnings


def settle_and_decide(d: date, settings, store, cache, calendar, notifier) -> None:
    warnings = settle_day(d, settings, store, cache, calendar)
    if warnings:
        notifier.send_text(format_alert(f"结算警告 {d.isoformat()}", "\n".join(warnings)))
    for ev in promotion.decide(store, d, calendar):
        notifier.send_text(format_alert(f"策略{KIND_CN.get(ev.kind, ev.kind)}",
                                        f"{ev.strategy}：{ev.detail}"))


def main(now: datetime | None = None) -> list[date]:
    """补跑所有待结算交易日；返回本次结算成功的日期。某日失败即停止（次日依赖前日持仓）。"""
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    days = pending_days(store, calendar, now)
    if not days:
        return []
    codes = tracked_codes(store)
    if not common.run_job("settle_data", days[-1],
                          lambda: refresh_data(codes, days[-1], client, cache),
                          store, notifier, force=True):
        return []
    done = []
    for d in days:
        ok = common.run_job("settle", d, lambda d=d: settle_and_decide(
            d, settings, store, cache, calendar, notifier), store, notifier)
        if not ok:
            break
        done.append(d)
    return done


if __name__ == "__main__":
    main()
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_settle.py -v
```
预期：7 passed。

**Step 5: 提交**

```
git add jobs/settle.py tests/test_jobs_settle.py
git commit -m "feat: jobs.settle with multi-day catch-up and idempotent settlement"
```
（附 Co-Authored-By trailer。）

### Task 6.4: 薄入口 premarket_scan / premarket_final / review / research

**Files:**
- Create: `jobs/premarket_scan.py`, `jobs/premarket_final.py`, `jobs/review.py`, `jobs/research.py`
- Test: `tests/test_jobs_entrypoints.py`

入口只做三件事：判断时间窗/交易日 → 通过 `common.context()` 装配依赖 → 用 `run_job` 调 agents 层。agents 函数一律以模块属性方式调用（`premarket.scan(...)`），便于测试替换。

**Step 1: 写失败测试**

`tests/test_jobs_entrypoints.py`：

```python
from datetime import datetime, time

import pytest

import agents.premarket as premarket
import agents.research as research_agent
import agents.review as review_agent
from jobs import common, premarket_final, premarket_scan, research, review
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, cal, install_context,
                            make_dates, make_index, make_panel, make_settings, new_store,
                            next_saturday)


@pytest.fixture
def env(tmp_path, monkeypatch):
    dates = make_dates(20)
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    cache, store, notifier = FakeCache(panel, NAMES, index), new_store(), NullNotifier()
    install_context(monkeypatch, make_settings(tmp_path), store, client, cache, cal(dates),
                    notifier)
    calls = []

    def fake_scan(d, settings, store, cache, client, strategy, calendar):
        calls.append(("scan", d, strategy.name))
        return []

    def fake_final(d, settings, store, llm, client, notifier, calendar):
        calls.append(("final", d, llm))

    def fake_review(d, settings, store, llm, cache, notifier, calendar):
        calls.append(("review", d, llm))

    def fake_research(settings, store, notifier, client=None):
        calls.append(("research",))
        return {"evals": 0}

    monkeypatch.setattr(premarket, "scan", fake_scan)
    monkeypatch.setattr(premarket, "run_final", fake_final)
    monkeypatch.setattr(review_agent, "run_review", fake_review)
    monkeypatch.setattr(research_agent, "run_research", fake_research)
    monkeypatch.setattr(common, "make_llm", lambda settings, store: "LLM")
    return dict(dates=dates, store=store, notifier=notifier, calls=calls)


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm))


def test_scan_runs_once_on_trading_day(env):
    d = env["dates"][10]
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert premarket_scan.main(now=at(d, 8, 40)) is True  # 已完成 -> 跳过
    assert env["calls"] == [("scan", d, "baseline_momo")]


def test_scan_skips_non_trading_day(env):
    assert premarket_scan.main(now=at(next_saturday(env["dates"][5]), 8, 30)) is False
    assert env["calls"] == []


def test_final_after_scan(env):
    d = env["dates"][10]
    premarket_scan.main(now=at(d, 8, 30))
    assert premarket_final.main(now=at(d, 9, 26)) is True
    assert env["calls"] == [("scan", d, "baseline_momo"), ("final", d, "LLM")]
    assert env["store"].job_done("premarket_final", d)


def test_final_runs_missing_scan_inline(env):
    d = env["dates"][10]
    assert premarket_final.main(now=at(d, 9, 30)) is True
    assert [c[0] for c in env["calls"]] == ["scan", "final"]


def test_final_stale_after_0935(env):
    d = env["dates"][10]
    assert premarket_final.main(now=at(d, 9, 36)) is False
    assert env["calls"] == []
    assert not env["store"].job_done("premarket_final", d)


def test_final_skips_non_trading_day(env):
    assert premarket_final.main(now=at(next_saturday(env["dates"][5]), 9, 26)) is False
    assert env["calls"] == []


def test_final_not_run_when_scan_fails(env, monkeypatch):
    d = env["dates"][10]

    def boom(*a, **k):
        raise RuntimeError("数据源失败")

    monkeypatch.setattr(premarket, "scan", boom)
    assert premarket_final.main(now=at(d, 9, 26)) is False
    assert env["calls"] == []
    assert any("数据源失败" in str(m) for m in env["notifier"].sent)


def test_review_requires_settle_and_runs_once(env):
    d = env["dates"][10]
    assert review.main(now=at(d, 16, 5)) is False  # 未结算
    env["store"].mark_job("settle", d, "ok")
    assert review.main(now=at(d, 16, 5)) is True
    assert review.main(now=at(d, 16, 30)) is True  # 已复盘 -> 跳过
    assert env["calls"] == [("review", d, "LLM")]


def test_review_before_close_targets_previous_day(env):
    d = env["dates"][10]
    env["store"].mark_job("settle", env["dates"][9], "ok")
    assert review.main(now=at(d, 9, 0)) is True
    assert env["calls"] == [("review", env["dates"][9], "LLM")]


def test_review_on_weekend_reviews_friday(env):
    fri = env["dates"][9]  # 2026-01-05 起的第 10 个工作日是周五
    env["store"].mark_job("settle", fri, "ok")
    assert review.main(now=at(next_saturday(fri), 10)) is True
    assert env["calls"] == [("review", fri, "LLM")]


def test_research_runs_once_per_day(env):
    sat = next_saturday(env["dates"][5])
    assert research.main(now=at(sat, 10)) == {"evals": 0}
    assert research.main(now=at(sat, 11)) is None  # 当天已跑过
    assert env["calls"] == [("research",)]
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_jobs_entrypoints.py -v
```
预期：FAIL，`ImportError: cannot import name 'premarket_final' from 'jobs'`。

**Step 3: 最小实现**

`jobs/premarket_scan.py`：

```python
"""08:30 盘前扫描（仅交易日）。python -m jobs.premarket_scan"""
from __future__ import annotations

from datetime import date, datetime

from agents import premarket
from jobs import common
from strategies.base import load_strategy


def run(d: date, settings, store, client, cache, calendar, notifier) -> bool:
    strategy = load_strategy(common.incumbent_strategy(store))
    return common.run_job("premarket_scan", d, lambda: premarket.scan(
        d, settings, store, cache, client, strategy, calendar), store, notifier)


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_scan %s: not a trading day", d)
        return False
    return run(d, settings, store, client, cache, calendar, notifier)


if __name__ == "__main__":
    main()
```

`jobs/premarket_final.py`：

```python
"""09:26 竞价定稿并推送（仅交易日；09:35 之后视为过时跳过）。python -m jobs.premarket_final"""
from __future__ import annotations

from datetime import datetime, time

from agents import premarket
from jobs import common, premarket_scan

STALE_AFTER = time(9, 35)


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_final %s: not a trading day", d)
        return False
    if now.time() > STALE_AFTER:
        common.log.info("premarket_final %s: stale at %s, skipped", d, now.time())
        return False
    if not store.job_done("premarket_scan", d) and not premarket_scan.run(
            d, settings, store, client, cache, calendar, notifier):
        return False  # 扫描失败已告警；按设计当天不出推荐
    llm = common.make_llm(settings, store)
    return common.run_job("premarket_final", d, lambda: premarket.run_final(
        d, settings, store, llm, client, notifier, calendar), store, notifier)


if __name__ == "__main__":
    main()
```

`jobs/review.py`：

```python
"""16:05 复盘 + 日报：针对最近一个已收盘且已结算的交易日。python -m jobs.review"""
from __future__ import annotations

from datetime import datetime

from agents import review as review_agent
from jobs import common


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = common.last_closed_day(now, calendar)
    if not store.job_done("settle", d):
        common.log.info("review %s: settle not done yet, skipped", d)
        return False
    llm = common.make_llm(settings, store)
    return common.run_job("review", d, lambda: review_agent.run_review(
        d, settings, store, llm, cache, notifier, calendar), store, notifier)


if __name__ == "__main__":
    main()
```

`jobs/research.py`：

```python
"""周六 10:00 研究循环。python -m jobs.research"""
from __future__ import annotations

from datetime import datetime

from agents import research as research_agent
from jobs import common


def main(now: datetime | None = None) -> dict | None:
    """返回本次研究摘要；当天已跑过或失败时返回 None。"""
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    out: dict = {}

    def fn():
        out["summary"] = research_agent.run_research(settings, store, notifier)

    common.run_job("research", now.date(), fn, store, notifier)
    return out.get("summary")


if __name__ == "__main__":
    main()
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_entrypoints.py -v
```
预期：11 passed。

**Step 5: 提交**

```
git add jobs/premarket_scan.py jobs/premarket_final.py jobs/review.py jobs/research.py tests/test_jobs_entrypoints.py
git commit -m "feat: thin job entrypoints for premarket, review, research"
```
（附 Co-Authored-By trailer。）

### Task 6.5: jobs/monitor.py（盘中监控）

**Files:**
- Create: `jobs/monitor.py`
- Test: `tests/test_jobs_monitor.py`

只测 `check_once`（一次检查）；`main` 循环仅做时间窗与睡眠，不写单测。监控对象是 `h:active` 账户在上一次结算后的持仓（当天新买入的 T+1 不可卖，无需监控）。

**Step 1: 写失败测试**

`tests/test_jobs_monitor.py`：

```python
from datetime import date, time

import pandas as pd

from engine.ledger import Account, Position
from engine.store import Store
from jobs.monitor import check_once, in_session
from notify.wecom import NullNotifier

D = date(2026, 10, 16)


class SpotClient:
    def __init__(self, rows):
        self.df, self.n = pd.DataFrame(rows), 0

    def spot(self):
        self.n += 1
        return self.df.copy()


def row(code, last, high, prev_close=10.0):
    return {"code": code, "name": "测试", "open": prev_close, "prev_close": prev_close,
            "last": last, "pct": (last / prev_close - 1) * 100, "volume": 1e6,
            "amount": 1e7, "high": high}


def make_store(*positions):
    store = Store(":memory:")
    acc = Account(name="h:active", cash=100_000.0)
    for code, stop, target in positions:
        acc.positions[code] = Position(code=code, name="测试", qty=1000, available=1000,
                                       cost=10.0, entry_date=date(2026, 10, 12), stop=stop,
                                       target=target, exit_date=date(2026, 10, 19))
    store.save_account(acc)
    return store


def test_stop_target_zhaban_alerts_deduped_per_day():
    store = make_store(("600001", 9.3, 11.0), ("600002", 9.3, 11.0),
                       ("600003", 9.3, 11.5), ("600004", 9.0, 12.0))
    client = SpotClient([
        row("600001", 9.2, 10.1),                   # 跌破止损
        row("600002", 11.2, 11.2, prev_close=10.5),  # 突破止盈（未到涨停 11.55）
        row("600003", 10.5, 11.0),                  # 曾触及涨停 11.00 后打开 -> 炸板
        row("600004", 11.0, 11.0),                  # 封死涨停 -> 无告警
    ])
    n = NullNotifier()
    first = check_once(D, store, client, n)
    text = "\n".join(first)
    assert len(first) == 3
    assert "触发止损" in text and "600001" in text
    assert "触发止盈" in text and "600002" in text
    assert "炸板" in text and "600003" in text
    assert "600004" not in text
    assert check_once(D, store, client, n) == []          # 同日同事件不重复
    assert len(n.sent) == 3
    assert len(check_once(date(2026, 10, 19), store, client, n)) == 3  # 新的一天重新计


def test_new_event_for_same_code_still_alerts():
    store = make_store(("600001", 9.5, 12.0))
    client = SpotClient([row("600001", 10.6, 11.0)])
    n = NullNotifier()
    assert len(check_once(D, store, client, n)) == 1      # 炸板
    client.df = pd.DataFrame([row("600001", 9.4, 11.0)])
    out = check_once(D, store, client, n)                 # 炸板已报过，止损是新事件
    assert len(out) == 1 and "触发止损" in out[0]


def test_no_positions_skips_spot_call():
    client = SpotClient([])
    assert check_once(D, Store(":memory:"), client, NullNotifier()) == []
    assert client.n == 0


def test_missing_high_column_and_suspended_stock():
    store = make_store(("600001", 9.3, 11.0), ("600002", 9.3, 11.0))
    client = SpotClient([row("600001", 9.0, 9.0), row("600002", float("nan"), 0.0)])
    client.df = client.df.drop(columns=["high"])
    out = check_once(D, store, client, NullNotifier())
    assert len(out) == 1 and "600001" in out[0] and "触发止损" in out[0]


def test_in_session():
    assert in_session(time(9, 30)) and in_session(time(11, 30)) and in_session(time(14, 59))
    assert not in_session(time(9, 29)) and not in_session(time(12, 0))
    assert not in_session(time(15, 1))
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_jobs_monitor.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'jobs.monitor'`。

**Step 3: 最小实现**

`jobs/monitor.py`：

```python
"""盘中监控：09:30-15:00 每 120 秒检查 h:active 持仓（仅交易日）。python -m jobs.monitor"""
from __future__ import annotations

import math
import time as _time
from datetime import date, datetime, time

from engine.rules import limit_prices
from jobs import common
from notify.format import format_alert

ACCOUNT = "h:active"
INTERVAL_SEC = 120
SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))  # 午休 11:30-13:00 不查
MAX_FAILS = 3  # 连续失败达到此次数告警一次


def in_session(t: time) -> bool:
    return any(a <= t <= b for a, b in SESSIONS)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def check_once(d: date, store, client, notifier) -> list[str]:
    """检查一次持仓，返回本次新发出的告警；(code, 事件) 去重键按日持久化在 kv。"""
    acc = store.load_account(ACCOUNT)
    if acc is None or not acc.positions:
        return []
    rows = {str(r["code"]): r for r in client.spot().to_dict("records")}
    key = f"monitor_sent:{d.isoformat()}"
    sent = set(store.kv_get(key) or [])
    out = []
    for code, pos in acc.positions.items():
        r = rows.get(code, {})
        last, high, prev = _num(r.get("last")), _num(r.get("high")), _num(r.get("prev_close"))
        if not last > 0:  # 停牌或无报价
            continue
        events = []
        if last <= pos.stop:
            events.append(("触发止损", f"现价 {last:.2f} ≤ 止损 {pos.stop:.2f}"))
        if last >= pos.target:
            events.append(("触发止盈", f"现价 {last:.2f} ≥ 止盈 {pos.target:.2f}"))
        if high > 0 and prev > 0:  # 无 high 列时跳过炸板判断
            up, _ = limit_prices(code, str(r.get("name") or pos.name), prev)
            if high >= up and last < up:
                events.append(("炸板", f"最高 {high:.2f} 曾触及涨停 {up:.2f}，现价 {last:.2f}"))
        for event, detail in events:
            if f"{code}:{event}" in sent:
                continue
            sent.add(f"{code}:{event}")
            msg = format_alert(f"{event} {code} {pos.name}",
                               f"{detail}；持仓 {pos.qty} 股，成本 {pos.cost:.2f}")
            notifier.send_text(msg)
            out.append(msg)
    if out:
        store.kv_set(key, sorted(sent))
    return out


def main(now: datetime | None = None, interval: float = INTERVAL_SEC) -> None:
    """循环到 15:00；now 仅用于判断是否交易日，循环内用真实时间。"""
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        return
    fails = 0
    while True:
        t = datetime.now()
        if t.date() != d or t.time() > SESSIONS[-1][1]:
            break
        if in_session(t.time()):
            try:
                check_once(d, store, client, notifier)
                fails = 0
            except Exception as e:  # noqa: BLE001 — 单次失败不退出循环
                fails += 1
                common.log.warning("monitor check failed (%d): %s", fails, e)
                if fails == MAX_FAILS:
                    notifier.send_text(format_alert("盘中监控异常", f"行情连续 {fails} 次获取失败：{e}"))
        _time.sleep(interval)


if __name__ == "__main__":
    main()
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_monitor.py -v
```
预期：5 passed。

**Manual smoke step（交易时段，联网）：** `uv run python -c "from jobs import common, monitor; s, st, c, _, _, n = common.context(); print(monitor.check_once(common.today(), st, c, n))"`，预期打印列表（无持仓时为 `[]`），且无异常；可再执行 `uv run python -c "from data.akshare_client import AkClient; print(AkClient().spot().columns.tolist())"` 确认 `high` 列存在（缺失时炸板判断自动跳过）。

**Step 5: 提交**

```
git add jobs/monitor.py tests/test_jobs_monitor.py
git commit -m "feat: jobs.monitor intraday stop/target/zhaban alerts with daily dedupe"
```
（附 Co-Authored-By trailer。）

### Task 6.6: Windows 计划任务安装/卸载脚本

**Files:**
- Create: `scripts/install_tasks.ps1`
- Create: `scripts/uninstall_tasks.ps1`
- Test: `tests/test_install_tasks.py`（静态检查：脚本引用的 job 模块都存在、时间点齐全、纯 ASCII）

任务清单（均在任务计划程序文件夹 `\RSIQS\` 下，以当前用户身份、登录时运行；工作日触发器每天触发，非交易日由代码自行跳过）：

| 任务 | 触发 | 动作 |
|---|---|---|
| PremarketScan | 周一至周五 08:30 | `jobs.premarket_scan` |
| PremarketFinal | 周一至周五 09:26 | `jobs.premarket_final` |
| Monitor | 周一至周五 09:30 | `jobs.monitor` |
| Settle | 周一至周五 15:35 | `jobs.settle` |
| Review | 周一至周五 16:05 | `jobs.review` |
| Research | 周六 10:00 | `jobs.research` |
| Catchup | 登录时（延迟 3 分钟） | `jobs.settle` 然后 `jobs.review`（顺序执行） |

所有任务开启 `StartWhenAvailable`（错过的触发在开机后补跑，过时与否由代码判断）、`MultipleInstances IgnoreNew`（同一任务不并发）、允许电池供电运行。`.ps1` 保持纯 ASCII：Windows PowerShell 5.1 按系统代码页读取无 BOM 文件，中文会乱码。

**Step 1: 写失败测试**

`tests/test_install_tasks.py`：

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_every_task_points_to_existing_job_module():
    jobs = set(re.findall(r'New-JobAction "(\w+)"', read("install_tasks.ps1")))
    assert jobs == {"premarket_scan", "premarket_final", "monitor", "settle", "review",
                    "research"}
    for job in jobs:
        assert (ROOT / "jobs" / f"{job}.py").exists(), job


def test_schedule_times_and_triggers():
    ps1 = read("install_tasks.ps1")
    for t in ("08:30", "09:26", "09:30", "15:35", "16:05", "10:00"):
        assert f'"{t}"' in ps1, t
    assert "-AtLogOn" in ps1 and "Saturday" in ps1 and "StartWhenAvailable" in ps1


def test_scripts_are_ascii_and_use_rsiqs_folder():
    for name in ("install_tasks.ps1", "uninstall_tasks.ps1"):
        text = read(name)
        assert text.isascii(), name
        assert "\\RSIQS\\" in text, name
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_install_tasks.py -v
```
预期：FAIL，`FileNotFoundError: ... scripts/install_tasks.ps1`。

**Step 3: 实现脚本**

`scripts/install_tasks.ps1`：

```powershell
# Register RSIQS scheduled tasks for the current user (run only while logged on).
# Usage (repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
# Re-running is safe: -Force overwrites existing task definitions.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Uv = (Get-Command uv -ErrorAction Stop).Source
$Folder = "\RSIQS\"
$User = "$env:USERDOMAIN\$env:USERNAME"
$Weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function New-JobAction([string]$Job) {
    New-ScheduledTaskAction -Execute $Uv -WorkingDirectory $Root `
        -Argument "run --project `"$Root`" python -m jobs.$Job"
}

function New-WeekdayTrigger([string]$At) {
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At $At
}

function Register-Rsiqs([string]$Name, $Trigger, $Action) {
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive
    Register-ScheduledTask -TaskPath $Folder -TaskName $Name -Trigger $Trigger -Action $Action `
        -Settings $settings -Principal $principal -Description "RSIQS $Name" -Force | Out-Null
    Write-Host "registered $Folder$Name"
}

Register-Rsiqs "PremarketScan"  (New-WeekdayTrigger "08:30") (New-JobAction "premarket_scan")
Register-Rsiqs "PremarketFinal" (New-WeekdayTrigger "09:26") (New-JobAction "premarket_final")
Register-Rsiqs "Monitor"        (New-WeekdayTrigger "09:30") (New-JobAction "monitor")
Register-Rsiqs "Settle"         (New-WeekdayTrigger "15:35") (New-JobAction "settle")
Register-Rsiqs "Review"         (New-WeekdayTrigger "16:05") (New-JobAction "review")
Register-Rsiqs "Research" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "10:00") `
    (New-JobAction "research")

# Catch-up after boot/logon: settle first, then review (actions run sequentially).
$logon = New-ScheduledTaskTrigger -AtLogOn -User $User
$logon.Delay = "PT3M"
Register-Rsiqs "Catchup" $logon @((New-JobAction "settle"), (New-JobAction "review"))

Get-ScheduledTask -TaskPath $Folder | Select-Object TaskName, State | Format-Table -AutoSize
```

`scripts/uninstall_tasks.ps1`：

```powershell
# Remove all RSIQS scheduled tasks (stops running instances first).
# Usage (repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall_tasks.ps1
$ErrorActionPreference = "Stop"
$Folder = "\RSIQS\"
$tasks = @(Get-ScheduledTask -TaskPath $Folder -ErrorAction SilentlyContinue)
foreach ($t in $tasks) {
    Stop-ScheduledTask -TaskPath $Folder -TaskName $t.TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskPath $Folder -TaskName $t.TaskName -Confirm:$false
    Write-Host "removed $Folder$($t.TaskName)"
}
Write-Host "RSIQS tasks removed: $($tasks.Count)"
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_install_tasks.py -v
```
预期：3 passed。

**Step 5: Manual smoke step（仅 Windows，本机 PowerShell）**

```powershell
cd <项目根目录>
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
Get-ScheduledTask -TaskPath "\RSIQS\" | Get-ScheduledTaskInfo | Format-Table TaskName, NextRunTime, LastRunTime, LastTaskResult
(Get-ScheduledTask -TaskPath "\RSIQS\" -TaskName Catchup).Actions | Format-Table Execute, Arguments, WorkingDirectory
Start-ScheduledTask -TaskPath "\RSIQS\" -TaskName Settle
Get-Content logs\jobs.log -Tail 20
```

预期：列出 7 个任务（State 为 Ready）；NextRunTime 与上表一致（Research 为下一个周六 10:00）；Catchup 有两个动作（settle、review）；手动触发 Settle 后 `LastTaskResult` 为 `0`，`logs\jobs.log` 出现 `job settle ... ok` 或 `skip settle`。再验证卸载：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall_tasks.ps1` 后 `Get-ScheduledTask -TaskPath "\RSIQS\"` 无输出（报 "No MSFT_ScheduledTask objects found"），然后重新执行安装脚本。

注意：
- 若注册时报 `Access is denied`，以管理员身份打开 PowerShell 重跑（任务仍以当前用户 Interactive 身份运行）。
- 每个任务运行时会弹出控制台窗口，Monitor 的窗口会保持到 15:00，不要关闭。若介意弹窗，可在 `New-JobAction` 中把 `-Execute` 换成 `"$Root\.venv\Scripts\pythonw.exe"`、`-Argument` 换成 `"-m jobs.$Job"`（需先 `uv sync`）。

**Step 6: 提交**

```
git add scripts/install_tasks.ps1 scripts/uninstall_tasks.ps1 tests/test_install_tasks.py
git commit -m "feat: Windows scheduled task install/uninstall scripts"
```
（附 Co-Authored-By trailer。）

### Task 6.7: tests/test_e2e.py（两个交易日的端到端回放）

**Files:**
- Test: `tests/test_e2e.py`

真实组件：`jobs.*` 入口、`agents.premarket.scan/run_final`、`agents.review.run_review`、`agents.playbook`、`engine.*`（撮合、账本、Store、promotion）、`strategies.baseline_momo`。替身：`FakeClient`（行情/新闻/竞价）、`FakeCache`、`FakeLLM`、`NullNotifier`，git 提交被打桩。时间全部通过 `main(now=...)` 注入。

**Step 1: 写测试**

`tests/test_e2e.py`：

```python
"""端到端：bootstrap -> 扫描 -> 定稿推送 -> 结算 -> 复盘 -> 次日，全程无网络、无真实 Claude。"""
from datetime import datetime, time

import pytest

import agents.playbook as pb
import engine.integrity as integrity
from jobs import bootstrap, common, premarket_final, premarket_scan, review, settle
from notify.wecom import NullNotifier
from tests.jobfakes import (CODES, NAMES, FakeCache, FakeClient, FakeLLM, cal,
                            install_context, make_dates, make_index, make_panel,
                            make_settings, new_store, prepare_root)

ACCOUNTS = ["q:baseline_momo", "h:active", "h:candidate", "bench:csi300"]


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm))


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(integrity, "write_manifest", lambda r: None)
    dates = make_dates()
    panel, index = make_panel(CODES, dates), make_index(dates)
    client = FakeClient(dates, panel, NAMES, index)
    cache, store, notifier = FakeCache(panel, NAMES, index), new_store(), NullNotifier()
    settings = make_settings(root)
    install_context(monkeypatch, settings, store, client, cache, cal(dates), notifier)
    llm = FakeLLM()
    monkeypatch.setattr(common, "make_llm", lambda s, st: llm)
    sent_at_settle = {}
    real_settle_day = settle.settle_day

    def spy_settle_day(d, *a, **k):
        sent_at_settle[d] = len(notifier.sent)
        return real_settle_day(d, *a, **k)

    monkeypatch.setattr(settle, "settle_day", spy_settle_day)
    client.current = dates[100]
    bootstrap.main(now=at(dates[100], 20))
    notifier.sent.clear()
    return dict(dates=dates, client=client, store=store, notifier=notifier, llm=llm,
                settings=settings, sent_at_settle=sent_at_settle)


def test_two_trading_days_full_cycle(world):
    dates, client, store = world["dates"], world["client"], world["store"]
    notifier, llm = world["notifier"], world["llm"]
    for day in dates[101:103]:
        client.current = day
        assert premarket_scan.main(now=at(day, 8, 30)) is True
        cands, _ = store.load_candidates(day)
        assert cands
        llm.candidates = cands  # 主线程预填，FakeLLM 在定稿线程里只读
        n0 = len(notifier.sent)
        assert premarket_final.main(now=at(day, 9, 26)) is True
        pushed = [str(m) for m in notifier.sent[n0:]]
        recs, _ = store.load_recommendation(day, "h:active")
        assert recs and len(recs) <= world["settings"].max_picks
        assert any(recs[0].code in m for m in pushed)        # 推荐已推送
        assert store.load_recommendation(day, "h:candidate") is not None

        assert settle.main(now=at(day, 15, 35)) == [day]
        assert world["sent_at_settle"][day] > n0               # 推送发生在结算之前
        n1 = len(notifier.sent)
        assert review.main(now=at(day, 16, 5)) is True
        assert store.job_done("review", day)
        assert len(notifier.sent) > n1                         # 日报已推送

    for acct in ACCOUNTS:
        assert [ts.date() for ts in store.nav_series(acct).index] == dates[100:103], acct
    day1_recs = {p.code for p in store.load_recommendation(dates[101], "h:active")[0]}
    buys = {f.code for f in store.load_account("h:active").trades
            if f.side == "BUY" and f.date == dates[101]}
    assert buys and buys <= day1_recs
    assert store.load_account("q:baseline_momo").trades
    assert store.nav_series("h:active").iloc[-1] != world["settings"].capital

    cand, active = pb.load("candidate"), pb.load("active")
    assert any("高开" in r["text"] for r in cand["rules"])    # 候选 playbook 已更新
    assert active["rules"] == [] and active["version"] == 1   # 未满 10 个交易日，不发布
    assert {"premarket:active", "premarket:candidate", "review"} <= set(llm.purposes)


def test_logon_catch_up_after_missed_days(world):
    """电脑在 dates[101]、dates[102] 全天关机，dates[103] 10:00 开机。"""
    dates, client, store = world["dates"], world["client"], world["store"]
    day = dates[103]
    client.current = day
    assert premarket_final.main(now=at(day, 10, 0)) is False  # 过时推荐跳过
    assert store.load_recommendation(day, "h:active") is None
    assert settle.main(now=at(day, 10, 3)) == dates[101:103]   # Catchup 动作 1
    assert review.main(now=at(day, 10, 5)) is True              # Catchup 动作 2
    assert store.job_done("review", dates[102])
    for acct in ACCOUNTS:
        assert store.last_nav_date(acct) == dates[102], acct
    assert store.load_account("h:active").trades == []          # 无推荐的日子不交易
```

**Step 2: 运行**

```
uv run pytest tests/test_e2e.py -v
```
预期：2 passed（此时 Part 1-5 与 Task 6.1-6.6 均已完成，本测试是集成验证，不需要新增产品代码）。若失败，按报错定位到契约不一致的那一方修复（不放宽断言）；常见原因：
- `sqlite3.ProgrammingError: ... created in a thread` → Part 2 的 `Store` 需 `sqlite3.connect(..., check_same_thread=False)` 并以 `threading.Lock` 串行化执行（`run_final` 在线程中调用 `finalize`）。
- 推送文本里找不到股票代码 → `notify.format.format_recommendation` 必须包含每只股票的代码。
- `maybe_release` 在第 1 天就发布 → Part 4 需读取 `kv "playbook_released"`（ISO 日期，bootstrap 写入）作为上次发布日。
- `FileNotFoundError: playbook/...md` → Part 4 的提示词文件须已提交，且 `agents.playbook` 在调用时读取模块级 `PLAYBOOK_DIR`（不要在函数默认参数里固化路径）。
- 补跑测试中 `review.main` 返回 False → `agents.review.run_review` 在当日无推荐、无成交时也应正常结束（照常推送日报）。

**Step 3: 提交**

```
git add tests/test_e2e.py
git commit -m "test: end-to-end replay of two trading days and logon catch-up"
```
（附 Co-Authored-By trailer。）

### Task 6.8: 全量测试 + 首次实盘运行清单

**Files:**
- Create: `scripts/smoke_live.py`（仅手动运行，真实调用 Claude 与企业微信各一次）

**Step 1: 全量测试（离线）**

```
uv run pytest -v
```
预期：全部 passed、0 failed。本部分贡献 39 个测试：`test_jobs_common.py` 7、`test_jobs_bootstrap.py` 4、`test_jobs_settle.py` 7、`test_jobs_entrypoints.py` 11、`test_jobs_monitor.py` 5、`test_install_tasks.py` 3、`test_e2e.py` 2。任何测试都不得访问网络或真实 Claude API；如有失败，先修复再继续。

**Step 2: 写冒烟脚本**

`scripts/smoke_live.py`：

```python
"""手动冒烟（真实网络，产生少量 API 费用）：交易日历 + Claude 结构化输出 + 企业微信推送。
用法：uv run python scripts/smoke_live.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from agents.llm import LLM  # noqa: E402
from jobs import common  # noqa: E402
from notify.wecom import WeCom  # noqa: E402


class Ping(BaseModel):
    ok: bool
    note: str


def main() -> None:
    settings, store, client, cache, calendar, notifier = common.context()
    today = common.today()
    print("calendar last:", calendar.last(), "trading today:", calendar.is_trading_day(today))
    reply = LLM(settings, store).parse(
        "smoke", "你是连通性测试助手，严格按 JSON schema 输出。",
        "返回 ok=true，note 用一句中文说明你已就绪。", Ping,
        effort="low", max_tokens=1024, essential=True)
    print("claude:", reply)
    print("month cost CNY:", round(store.month_llm_cost(today.year, today.month), 4))
    wecom = WeCom(settings.wecom_webhook_url, log_dir=settings.root / "logs")
    print("wecom:", wecom.send_text(f"RSIQS 冒烟测试：{reply.note}"))


if __name__ == "__main__":
    main()
```

**Step 3: 填写 .env（Windows 本机，项目根目录）**

```powershell
Copy-Item .env.example .env
notepad .env
git check-ignore .env
```
填写 `ANTHROPIC_API_KEY`、`WECOM_WEBHOOK_URL`（企业微信群机器人完整地址），按需调整 `MONTHLY_BUDGET_CNY`（默认 300）、`CAPITAL`、`MAX_PICKS`。预期 `git check-ignore .env` 输出 `.env`（确认不会被提交）。

**Step 4: Manual smoke steps（联网）**

```
uv run python scripts/smoke_akshare.py
uv run python scripts/smoke_live.py
```
预期：`smoke_akshare.py` 按 Part 3 的说明全部通过（列名映射正确）；`smoke_live.py` 打印 `claude: ok=True note='...'`、`month cost CNY` 大于 0、`wecom: True`，且企业微信群收到"RSIQS 冒烟测试"消息。任一失败先排查，不要继续。

**Step 5: 初始化**

```
uv run python -m jobs.bootstrap
```
预期：首次需下载约 800 只股票自 2015-06-01 起的日线，耗时 30-60 分钟（中断后重跑为增量）；结束打印"RSIQS 初始化完成：股票池 N 只，起始日 YYYY-MM-DD，新建账户 ..."，群里收到同样消息。随后检查：

```
uv run python -c "from jobs import common; s, st, *_ = common.context(); print(st.list_accounts(), st.get_status('baseline_momo'), st.kv_get('bench_start'))"
uv run python -c "from pathlib import Path; from engine.integrity import verify; print(verify(Path('.')))"
git status --short
```
预期：账户含 `q:baseline_momo`、`h:active`、`h:candidate`、`bench:csi300`，状态 `incumbent`，起始日为最近收盘的交易日；`verify` 输出 `[]`。若 `.integrity/manifest.json` 或 `playbook/active.json`、`playbook/candidate.json` 出现在 `git status` 中：

```
git add .integrity/manifest.json playbook/active.json playbook/candidate.json
git commit -m "chore: initial integrity manifest and playbooks"
```
（附 Co-Authored-By trailer。）

**Step 6: 手动跑通一次各入口（联网，交易日或非交易日均可）**

```
uv run python -m jobs.settle
uv run python -m jobs.review
uv run python -m jobs.premarket_scan
uv run python -m jobs.premarket_final
Get-Content logs\jobs.log -Tail 30
```
预期：非交易日或时间窗外，入口静默返回（日志里有 `not a trading day` / `stale` / `settle not done yet`）；交易日 08:30-09:35 之间运行 scan + final 会在群里收到推荐；`logs\jobs.log` 无 `ERROR`。若 scan/final 因时间窗被跳过，在下一个交易日早上 08:30 前保持电脑开机，观察自动运行。

**Step 7: 安装计划任务**

按 Task 6.6 Step 5 执行 `scripts\install_tasks.ps1` 并用 `Get-ScheduledTask -TaskPath "\RSIQS\"` 核对 7 个任务与下次运行时间。

**Step 8: 首周观察清单**

- 每个交易日 09:28 前群里收到推荐；若标注"未经 LLM 审核"，查 `logs\jobs.log` 中 premarket 的超时/预算原因。
- 15:35 后 `jobs.log` 出现 `job settle <日期> ok`；16:05 后收到日报（含全部账户的净值与日收益）。
- 关机一天后开机：3 分钟后 Catchup 任务补齐结算与复盘，日志里能看到逐日的 `job settle ... ok`。
- 周六 10:00 研究循环运行后收到研究摘要；`uv run python -c "from jobs import common; s, st, *_ = common.context(); import datetime as dt; t = dt.date.today(); print(st.month_llm_cost(t.year, t.month))"` 查看当月花费，确认低于 `MONTHLY_BUDGET_CNY`。
- `logs\notify_failed.jsonl` 应为空或不存在；有内容说明推送失败过，检查网络与 webhook。


---

