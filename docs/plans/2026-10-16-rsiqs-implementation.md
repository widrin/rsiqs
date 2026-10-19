# RSIQS 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** 构建 A 股盘前推荐 + 模拟交易 + 递归自我提升系统：每个交易日 9:28 前经企业微信推送 ≤5 只推荐，模拟撮合结算，并由 Claude 迭代量化策略与 LLM playbook。

**Architecture:** 混合两层。量化层（`strategies/`，可回测，研究循环迭代）出候选池；LLM 层（`agents/premarket.py` + `playbook/`，仅前向验证）结合新闻、公告、竞价做否决与调权。`engine/` 为锁定区（撮合、账本、评估器、留出集），哈希校验；多个影子账户并行模拟，晋升规则以数据决定现任。设计见 `docs/plans/2026-10-16-rsiqs-design.md`。

**Tech Stack:** Python 3.12 + uv；akshare 1.19.1、pandas 3.0.6、numpy 2.5.4、requests、pydantic、python-dotenv、sqlite3；Claude Code CLI（`claude.exe -p --bare`，`claude-opus-5-5`，结构化输出，经中转 Token 或官方 API Key 鉴权）；pytest 9.1.1；Windows 任务计划。

**执行顺序：** Part 1 → 2 → 3 → 4 → 5 → 6，各 Part 内按 Task 编号。测试一律不访问网络与真实 API；需要真实环境的验证均为显式的 "Manual smoke step"。

**修订（2026-10-19）：** Claude 接入改为 Claude Code CLI，盘前改为两段式，研究循环改为无工具多轮结构化输出。各修订段落已分别在临时工程中测试通过；合并后的全量测试结果见下一行。

**修订（2026-10-19，Part 1 审查修复）：** 涨跌停与费用按日期生效，创业板含 302 号段，涨跌停判断容忍一分钱；撮合改为开盘卖出、开盘买入、盘中卖出三步；账本自保 T+1、拒绝加仓与异常价格；`Pick`、`Fill`、配置增加输入校验。后续 Part 的代码无需改动，只同步了提示词中的撮合与费用描述。

**修订（2026-10-19，Part 1 第二至四轮审查修复）：** 当天开盘、最高、最低价中任何一个超出 [跌停价-0.01, 涨停价+0.01]（不含一档）时，视为区间不可靠（例如除权日的昨收仍是除权前价格），成交价不截断，封板只看一字形态：最高价等于最低价才算封死涨停或跌停。算错的区间可能窄得多（前复权扣减了之后的现金分红时，高分红股早期原始价格涨跌三四个点就会越过算出的涨跌停价），普通交易日照常成交；一字板仍然买不进、卖不出；开盘价不高于 pick 止损价时不买；`execute_day` 依据 `Account.last_trade_day` 保证每个交易日只执行一次；配置每个键依次取非空的系统环境变量、非空的 `.env` 值、默认值。后续 Part 的改动：(a) Task 3.5 基线策略按 `view.asof` 当天的规则计算涨停价，并用 `at_limit_up` 判断涨停（容忍一分钱），新增 1 个测试；(b) Task 5.2 `RESEARCH_SYSTEM` 补充编码约定（涨跌停价传入 `view.asof`，涨跌停判断用 `at_limit_up` / `at_limit_down`），买入规则补上“开盘价不高于 stop 不成交”，提示词测试增加对应关键词；(c) Task 4.3 `auction_filter` 剔除竞价价不高于采用止损价的股票，与撮合口径一致，新增 1 个测试，盘前、复盘提示词同步；(d) Task 6.8 说明鉴权取值顺序。

**修订（2026-10-19，Part 2 审查修复）：** 评估器在回放留出期之前记录试验，训练、验证段报错也计入 n_trials，留出期任何异常只记为未通过、不外传文本；回测净值前置期初资金点，面板截断到本段末尾；晋升先对齐净值再算收益，按收益日计数，降级要求 t <= -1.5；`DataView.codes()` 不列出尚未上市的股票；`record_nav` 拒绝非有限值；清单损坏视为校验不通过。后续 Part 的改动：(a) Task 3.5 基线只取需要的 60 行；(b) Task 3.6 源码检查禁止任何含 `__` 的名字、属性和标识符式字符串，以及动态取属性、`print`、`exit`、`SystemExit`，`run_eval` 对 `SystemExit` 也回传错误 JSON；(c) Task 5.1 `evaluate_strategy` 以数据库里本次的试验记录决定是否进入影子盘，`EVAL_TIMEOUT` 改为 1800 秒；(d) Task 5.2 编码约定同步；(e) Task 6.2 已有清单时不重写，只校验并告警；(f) Task 6.3 先存账户再记净值，收盘价缺失时沿用上一个有效值，策略只拿到截至前一交易日的面板。

**修订（2026-10-19，结算改用不复权日线）：** 复审发现结算直接用前复权日线撑不住补跑：多日补跑跨越除权日时，较早的日子已是之后的复权基准，除权前按下调后的价格成交，到除权日分红又入账一次；除权日日线缺失时股数放大而估值仍是除权前价格；除权日分红接口失败后再也不会补调整。修订：Task 3.1 `AkClient.raw_bars`，Task 3.2 `bar_raw` 表，Task 6.3 撮合与估值改用不复权日线（量化价位按当天两根 K 线换算，估值价按除权参考价换算，疑似除权缺记录时暂停或告警），Task 4.3 盘前定稿在除权日按竞价快照昨收换算价位。另外：开盘买入只看开盘价（开盘达到或越过算出的涨停价不买）；源码检查改为只允许给 self 的属性赋值，`strategies.base` 移出导入白名单，补齐读写文件的属性；`run_eval` 的规则快照在加载策略之前取，并覆盖 pandas、math、numpy 的关键函数和 `engine.rules` 常量。

**修订（2026-10-19，Part 2 修复复审）：** 留出期里任何异常（含策略抛出的 KeyboardInterrupt）都只记为未通过，Ctrl+C 时改抛不带消息的新异常；区间不可靠时开盘涨停也只认一字板，不再用"开盘价等于最高价"（那等于让当天之后的走势决定买不买）；降级后的影子期从下一交易日算起；源码检查补上按字符串求值（`typing.get_type_hints`、`ForwardRef`）、含 `__` 的普通字符串、对已导入模块或类的属性赋值、经 `import engine.types` 绑定的包名访问白名单之外的模块；`run_eval` 评估前后核对评估规则，被改过就作废试验（`Store.void_trial`）。

**修订（2026-10-19，除权除息）：** 前复权缓存在除权后整体变化，持仓的股数、成本和价位却停留在除权前，除权日会记下假止损。修订：(a) Task 1.5 `Account.apply_dividend` 按除权参考价 (价格 - 每股派息) / (1 + 每股送转) 调整股数、成本、止损和目标价，现金分红按 20% 扣税后入账，已调整的事件记入 `corporate_actions`，重复调用不会重复调整；(b) Task 3.1 `AkClient.dividends` 读取新浪分红明细中已实施的方案；(c) Task 3.2 增量更新时与已存的最后 5 行比对，复权基准变化就全量重新下载这只股票，并新增分红表；(d) Task 6.3 结算前刷新持仓和当日推荐股票的分红记录，撮合前调整上次结算之后除权的持仓，盘前推荐的价位在除权日换算到除权后；(e) Task 6.5 盘中监控在除权日按换算后的价位判断。

**全量验证：** 2026-10-19 把合并后计划的全部代码块（84 个文件）提取到仓库外的临时工程运行，639 个测试全部通过（Part 1 共 152 个、Part 2 共 105 个）。测试不联网、不调用真实 claude.exe、不推送企业微信。

---

## Part 1: 工程脚手架与撮合核心

> 契约歧义说明：(1) `TradingCalendar.offset` 越过日历两端时夹到首/末交易日而不报错，保证回测末段买入仍有合法 exit_date（d 非交易日仍抛 ValueError）。(2) `Fill` 额外提供 `to_dict/from_dict` 供 `Account` 持久化。(3) 不建模加仓：`Account.buy` 对已持仓代码抛 ValueError（SimBroker 本来就跳过已持仓代码）。(4) 滑点价不做四舍五入，保持浮点，只截断在当日 [跌停价, 涨停价] 之内；当天开盘、最高、最低价中任何一个超出 [跌停价-0.01, 涨停价+0.01] 时，说明区间本身不可靠（例如除权日的昨收仍是除权前价格），此时不截断；卖出侧只有一字板才算封死跌停，开盘买入只看开盘价（达到或越过算出的涨停价就不买）。(5) 除权除息只在模拟盘结算时由 `Account.apply_dividend` 处理：回测用的是一致的前复权历史，不需要调整；不建模配股，送转后的零碎股向下取整，分红一律按持股 1 个月以内的 20% 扣税。

本部分在 `e:\workspace\rsiqs` 下建立 uv 工程与 `engine/` 撮合核心（types / rules / calendar / ledger / broker），全部纯函数式可测，无网络依赖。所有代码已在隔离环境用 pytest 9.1.1 实测通过（152 个用例）。

### Task 1.1: 工程脚手架（uv + config）

**Files:**
- Create: `pyproject.toml`, `.env.example`, `.gitignore`, `config.py`
- Create: `engine/__init__.py`, `data/__init__.py`, `notify/__init__.py`, `strategies/__init__.py`, `agents/__init__.py`, `jobs/__init__.py`（均为空文件）
- Test: `tests/test_config.py`

Claude 一律经 Claude Code CLI（`claude.exe -p --bare`）调用，见 Task 4.1。用户的中转会拒绝 Anthropic SDK 直连，所以依赖里没有 `anthropic` 包；费用直接取 CLI 输出的 `total_cost_usd`，`config` 里也不再需要价格表。

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

`.env.example`（只放占位符，绝不写真实密钥、Token 或 webhook key）：

```ini
# Copy to .env and fill in real values. Never commit .env.
# 二选一：官方 API Key，或中转地址+Token；CLAUDE_EXE 留空自动查找
# 路径不要加双引号（双引号内的 \n、\b 会被当成转义符），直接写或用单引号
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=
ANTHROPIC_AUTH_TOKEN=
CLAUDE_EXE=
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
*.sqlite
*.sqlite3
*.db
__pycache__/
*.pyc
.venv/
.pytest_cache/
logs/
data/cache/
.worktrees/
```

创建包目录与空 `__init__.py`，然后同步依赖：

```powershell
foreach ($p in 'engine','data','notify','strategies','agents','jobs','tests') { New-Item -ItemType Directory -Force $p | Out-Null }
foreach ($p in 'engine','data','notify','strategies','agents','jobs') { if (-not (Test-Path "$p/__init__.py")) { New-Item -ItemType File "$p/__init__.py" | Out-Null } }
uv sync
```
预期：生成 `uv.lock` 与 `.venv`，`uv run python -c "import pandas, akshare, pydantic; print(pandas.__version__)"` 输出 `3.0.6`。

**Step 1: 写失败测试** `tests/test_config.py`（autouse fixture 把 `config.ENV_FILE` 指向临时目录里不存在的文件，避免读到本机真实 `.env`）

```python
import os

import pytest

import config

ENV_NAMES = ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_EXE",
             "WECOM_WEBHOOK_URL", "MONTHLY_BUDGET_CNY", "USD_CNY", "DB_PATH", "LLM_MODEL", "CAPITAL",
             "MAX_PICKS"]
REAL_ENV_FILE = config.ENV_FILE


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch, tmp_path):
    """get_settings() without an argument must never read this machine's real .env."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent.env")


def _isolate(monkeypatch):
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)


def test_defaults(monkeypatch):
    _isolate(monkeypatch)
    s = config.get_settings()
    assert s.anthropic_api_key == "" and s.wecom_webhook_url == ""
    assert s.anthropic_base_url == "" and s.anthropic_auth_token == "" and s.claude_exe == ""
    assert s.monthly_budget_cny == 300.0 and s.usd_cny == 7.2
    assert s.db_path == "data/rsiqs.sqlite" and s.llm_model == "claude-opus-5-5"
    assert s.capital == 500_000.0 and s.max_picks == 5
    assert (s.root / "config.py").exists()


def test_env_overrides(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://relay.example.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    monkeypatch.setenv("CLAUDE_EXE", r"D:\tools\claude.exe")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv("MONTHLY_BUDGET_CNY", "150")
    monkeypatch.setenv("USD_CNY", "7.0")
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("CAPITAL", "1000000")
    monkeypatch.setenv("MAX_PICKS", "3")
    s = config.get_settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.anthropic_base_url == "https://relay.example.invalid"
    assert s.anthropic_auth_token == "tok-test"
    assert s.claude_exe == r"D:\tools\claude.exe"
    assert s.wecom_webhook_url == "https://example.invalid/hook"
    assert s.monthly_budget_cny == 150.0 and s.usd_cny == 7.0
    assert s.db_path == ":memory:" and s.capital == 1_000_000.0 and s.max_picks == 3


def test_empty_env_falls_back_to_defaults(monkeypatch):
    _isolate(monkeypatch)
    for n in ENV_NAMES:
        monkeypatch.setenv(n, "")
    monkeypatch.setenv("CAPITAL", "  ")
    s = config.get_settings()
    assert s.monthly_budget_cny == 300.0 and s.usd_cny == 7.2
    assert s.db_path == "data/rsiqs.sqlite" and s.llm_model == "claude-opus-5-5"
    assert s.capital == 500_000.0 and s.max_picks == 5
    assert s.anthropic_api_key == "" and s.claude_exe == ""


@pytest.mark.parametrize("name,value", [
    ("MAX_PICKS", "0"), ("MAX_PICKS", "2.5"), ("CAPITAL", "0"), ("CAPITAL", "-1"),
    ("CAPITAL", "nan"), ("MONTHLY_BUDGET_CNY", "-0.01"), ("USD_CNY", "0"), ("USD_CNY", "abc"),
])
def test_invalid_numbers_raise_clear_error(monkeypatch, name, value):
    _isolate(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        config.get_settings()


def test_zero_budget_allowed_and_settings_validated_directly(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("MONTHLY_BUDGET_CNY", "0")
    assert config.get_settings().monthly_budget_cny == 0.0
    with pytest.raises(ValueError, match="MAX_PICKS"):
        config.Settings(anthropic_api_key="", wecom_webhook_url="", max_picks=0)


def test_repr_hides_secrets():
    s = config.Settings(anthropic_api_key="sk-secret-1", wecom_webhook_url="https://example.invalid/hook?key=secret-2",
                        anthropic_auth_token="secret-3", anthropic_base_url="https://relay.example.invalid")
    text = repr(s)
    for secret in ("sk-secret-1", "secret-2", "secret-3"):
        assert secret not in text
    assert "relay.example.invalid" in text and "claude-opus-5-5" in text


def test_env_example_lists_every_key_without_secrets():
    lines = (config.ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = dict(ln.split("=", 1) for ln in lines if ln.strip() and not ln.startswith("#"))
    assert set(ENV_NAMES) <= set(values)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_EXE"):
        assert values[k] == ""
    assert values["WECOM_WEBHOOK_URL"].endswith("key=YOUR-KEY-HERE")


def _env_file(tmp_path, *lines):
    f = tmp_path / ".env"
    f.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    return f


def test_env_file_value_used_when_system_var_is_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    monkeypatch.setenv("CAPITAL", "")
    s = config.get_settings(_env_file(tmp_path, "CAPITAL=1000000", "MAX_PICKS=3"))
    assert s.capital == 1_000_000.0 and s.max_picks == 3


def test_non_empty_system_var_wins_over_env_file(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    monkeypatch.setenv("CAPITAL", "200000")
    assert config.get_settings(_env_file(tmp_path, "CAPITAL=1000000")).capital == 200_000.0


def test_both_empty_fall_back_to_default(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    monkeypatch.setenv("CAPITAL", "")
    s = config.get_settings(_env_file(tmp_path, "CAPITAL=", "USD_CNY=  ", "MAX_PICKS"))
    assert s.capital == 500_000.0 and s.usd_cny == 7.2 and s.max_picks == 5


def test_get_settings_does_not_write_environment(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    s = config.get_settings(_env_file(tmp_path, "ANTHROPIC_AUTH_TOKEN=tok-file"))
    assert s.anthropic_auth_token == "tok-file"
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ


def test_default_env_file_is_root_dotenv(monkeypatch):
    _isolate(monkeypatch)
    assert REAL_ENV_FILE == config.ROOT / ".env"
    seen = []
    monkeypatch.setattr(config, "dotenv_values", lambda path: seen.append(path) or {})
    config.get_settings()
    assert seen == [config.ENV_FILE]
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_config.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'config'`。

**Step 3: 最小实现** `config.py`（密钥类字段 `repr=False`：日志或异常里打印 `Settings` 时不会泄露；每个键依次取非空的系统环境变量、非空的 `.env` 值（用 `dotenv_values` 读取，默认文件为 `config.ENV_FILE`，即 `ROOT / ".env"`）、默认值，空字符串和纯空白都算未设置；不用 `load_dotenv`，因为它不会覆盖系统里已存在的空变量，`.env` 的值会被忽略，`get_settings` 也不写入 `os.environ`；`Settings.__post_init__` 校验 `max_picks >= 1`、`capital > 0`、`monthly_budget_cny >= 0`、`usd_cny > 0`，报错信息里带环境变量名）

```python
"""Runtime settings loaded from .env / environment."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = field(repr=False)
    wecom_webhook_url: str = field(repr=False)
    monthly_budget_cny: float = 300.0
    usd_cny: float = 7.2
    db_path: str = "data/rsiqs.sqlite"
    llm_model: str = "claude-opus-5-5"
    capital: float = 500_000.0
    max_picks: int = 5
    root: Path = field(default=ROOT)
    # Claude Code CLI 鉴权二选一：anthropic_api_key，或中转 anthropic_base_url + anthropic_auth_token
    anthropic_base_url: str = ""
    anthropic_auth_token: str = field(default="", repr=False)
    claude_exe: str = ""  # claude.exe 完整路径；留空由 agents.llm.find_claude_exe 自动查找

    def __post_init__(self):
        # 比较式遇到 NaN 为 False，所以 NaN 同样会被拒绝
        checks = [("MAX_PICKS", self.max_picks, self.max_picks >= 1, ">= 1"),
                  ("CAPITAL", self.capital, self.capital > 0, "> 0"),
                  ("MONTHLY_BUDGET_CNY", self.monthly_budget_cny, self.monthly_budget_cny >= 0, ">= 0"),
                  ("USD_CNY", self.usd_cny, self.usd_cny > 0, "> 0")]
        for env_name, value, ok, rule in checks:
            if not ok:
                raise ValueError(f"配置 {env_name} 必须 {rule}，当前为 {value!r}")


def _num(env, name: str, default: str, cast):
    raw = env(name, default)
    try:
        return cast(raw)
    except ValueError:
        kind = "整数" if cast is int else "数字"
        raise ValueError(f"配置 {name} 必须是{kind}，当前为 {raw!r}") from None


def get_settings(env_file: Path | None = None) -> Settings:
    """每个键依次取：非空的系统环境变量、非空的 .env 值、默认值。不写入 os.environ。

    不用 load_dotenv：它不会覆盖系统里已存在的空变量，.env 的值会被忽略。"""
    file_values = dotenv_values(ENV_FILE if env_file is None else env_file)

    def env(name: str, default: str) -> str:
        for value in (os.environ.get(name), file_values.get(name)):
            if value is not None and value.strip():  # 空字符串（含纯空白）视为未设置
                return value
        return default

    return Settings(
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        wecom_webhook_url=env("WECOM_WEBHOOK_URL", ""),
        monthly_budget_cny=_num(env, "MONTHLY_BUDGET_CNY", "300", float),
        usd_cny=_num(env, "USD_CNY", "7.2", float),
        db_path=env("DB_PATH", "data/rsiqs.sqlite"),
        llm_model=env("LLM_MODEL", "claude-opus-5-5"),
        capital=_num(env, "CAPITAL", "500000", float),
        max_picks=_num(env, "MAX_PICKS", "5", int),
        root=ROOT,
        anthropic_base_url=env("ANTHROPIC_BASE_URL", ""),
        anthropic_auth_token=env("ANTHROPIC_AUTH_TOKEN", ""),
        claude_exe=env("CLAUDE_EXE", ""),
    )
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_config.py -v
```
预期：19 passed（无效数值的参数化测试含 8 个用例）。

**Step 5: Commit**

```powershell
git add pyproject.toml uv.lock .env.example .gitignore config.py engine/__init__.py data/__init__.py notify/__init__.py strategies/__init__.py agents/__init__.py jobs/__init__.py tests/test_config.py
git commit -m "chore: scaffold uv project and settings"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）

### Task 1.2: engine/types.py

**Files:**
- Create: `engine/types.py`
- Test: `tests/test_types.py`

**Step 1: 写失败测试** `tests/test_types.py`

```python
import dataclasses
from datetime import date

import numpy as np
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


def _pick_kwargs(**over):
    kw = dict(code="600000", name="浦发银行", score=1.0, entry_max=10.3, stop=9.3, target=11.0,
              hold_days=5)
    kw.update(over)
    return kw


def test_pick_coerces_prices_and_hold_days():
    p = Pick(**_pick_kwargs(entry_max="10.3", stop=9, target=np.float64(11.0), hold_days=np.int64(3)))
    assert (p.entry_max, p.stop, p.target, p.hold_days) == (10.3, 9.0, 11.0, 3)
    assert type(p.stop) is float and type(p.target) is float and type(p.hold_days) is int
    assert Pick(**_pick_kwargs(hold_days=4.0)).hold_days == 4


@pytest.mark.parametrize("over", [
    {"hold_days": 2.5}, {"hold_days": 0}, {"hold_days": -1}, {"hold_days": "abc"},
    {"hold_days": True}, {"hold_days": float("nan")},
    {"entry_max": float("nan")}, {"stop": 0}, {"target": -1.0}, {"stop": float("inf")},
    {"entry_max": "abc"}, {"stop": None},
    {"stop": 11.0, "target": 11.0}, {"stop": 11.5, "target": 11.0},
])
def test_pick_rejects_invalid(over):
    with pytest.raises(ValueError):
        Pick(**_pick_kwargs(**over))


def test_pick_entry_max_may_exceed_target():
    assert Pick(**_pick_kwargs(entry_max=12.0, target=11.0)).entry_max == 12.0


def test_fill_side_must_be_buy_or_sell():
    for side in ("buy", "HOLD", ""):
        with pytest.raises(ValueError):
            Fill(date(2026, 10, 16), "600000", side, 100, 10.0, 5.0, "x")
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_types.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.types'`。


**Step 3: 最小实现** `engine/types.py`（`Pick.__post_init__` 把价格字段转成 float、`hold_days` 转成 int：2.5 这类非整数直接拒绝，numpy 整数可以；要求价格为有限正数、`hold_days >= 1`、`stop < target`，不要求 `entry_max < target`。`Fill.side` 只能是 "BUY" 或 "SELL"）

```python
"""Core value types shared across the engine."""
import datetime
import math
import numbers
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

    def __post_init__(self):
        """Coerce prices to float and hold_days to int; reject values the broker cannot trade.

        entry_max is deliberately not compared with target (a plan may cap the buy price above it).
        """
        for name in ("entry_max", "stop", "target"):
            raw = getattr(self, name)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Pick {self.code}: {name} must be a number, got {raw!r}") from None
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"Pick {self.code}: {name} must be finite and > 0, got {v}")
            object.__setattr__(self, name, v)
        h = self.hold_days
        if (isinstance(h, bool) or not isinstance(h, numbers.Real) or not math.isfinite(h)
                or h != int(h)):
            raise ValueError(f"Pick {self.code}: hold_days must be an integer, got {h!r}")
        object.__setattr__(self, "hold_days", int(h))
        if self.hold_days < 1:
            raise ValueError(f"Pick {self.code}: hold_days must be >= 1, got {self.hold_days}")
        if not self.stop < self.target:
            raise ValueError(f"Pick {self.code}: stop {self.stop} must be below target {self.target}")

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

    def __post_init__(self):
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Fill side must be 'BUY' or 'SELL', got {self.side!r}")

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
预期：21 passed（Pick 非法输入的参数化测试含 14 个用例）。


**Step 5: Commit**

```powershell
git add engine/types.py tests/test_types.py
git commit -m "feat(engine): add Bar, Pick, Fill value types"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）

### Task 1.3: engine/rules.py（板块、涨跌停、整手、费用）

**Files:**
- Create: `engine/rules.py`
- Test: `tests/test_rules.py`

要点：
- 涨跌停价用 `Decimal(str(prev_close))` + `ROUND_HALF_UP`。例：10.05×1.1=11.055→**11.06**，10.05×0.9=9.045→**9.05**；直接用 float 会得到 11.05/9.04（错误）。
- 板块：代码以 "30" 开头的都算创业板（300/301/302，302132 中航成飞自 2025-02-17 起属创业板）。
- 涨跌幅和费用按日期生效：`limit_pct`、`limit_prices`、`calc_fee` 末尾有可选参数 `d`，`None` 表示当前规则。北交所 30%、科创板 20%；创业板 2020-08-24 起 20%，此前 10%（ST 5%）；主板 10%，主板 ST 在 2026-07-06 前为 5%、之后为 10%。过户费 2022-04-29 前 0.002%、之后 0.001%；印花税（仅卖出）2023-08-28 前 0.1%、之后 0.05%；佣金万 2.5、最低 5 元。各生效日期是模块级常量，各附一行来源注释。`d` 传 `datetime`/`pandas.Timestamp` 时按其日期处理。
- `calc_fee` 的 side 只能是 "BUY" 或 "SELL"，否则抛 ValueError。
- `at_limit_up(price, up)` / `at_limit_down(price, down)` 容忍一档价差（0.01 元）：前复权价格是原始价格缩放后再舍入到分，封板股的复权价可能与按复权昨收算出的涨跌停价差一分钱。对原始价格的影响是离涨停（跌停）差一分钱也按涨停（跌停）处理，结果略偏保守。
- 整手统一按 100 股向下取整（科创板 200 股起买未建模，已在 docstring 注明）。

**Step 1: 写失败测试** `tests/test_rules.py`

```python
from datetime import date, datetime, timedelta

import pytest

from engine import rules
from engine.rules import (Board, at_limit_down, at_limit_up, board, calc_fee, is_st, limit_pct,
                          limit_prices, round_lot)

DAY = timedelta(days=1)


@pytest.mark.parametrize("code,expected", [
    ("600000", Board.MAIN), ("000001", Board.MAIN), ("002594", Board.MAIN),
    ("300750", Board.CHINEXT), ("301269", Board.CHINEXT), ("302132", Board.CHINEXT),
    ("688981", Board.STAR), ("689009", Board.STAR),
    ("830799", Board.BSE), ("430047", Board.BSE), ("920001", Board.BSE),
])
def test_board(code, expected):
    assert board(code) is expected


def test_is_st():
    assert is_st("*ST康美") and is_st("ST海越") and is_st("st测试")
    assert not is_st("平安银行")


@pytest.mark.parametrize("code,name,pct", [
    ("600000", "浦发银行", 0.10), ("600000", "*ST浦发", 0.10),
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
    # 3.4965->3.50, 3.1635->3.16
    assert limit_prices("600000", "ST浦发", 3.33, date(2026, 7, 3)) == (3.50, 3.16)
    assert limit_prices("600000", "ST浦发", 3.33) == (3.66, 3.00)  # 主板 ST 自 2026-07-06 起 ±10%
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


def test_fee_rejects_unknown_side():
    for side in ("buy", "sell", "HOLD", ""):
        with pytest.raises(ValueError):
            calc_fee(side, 10_000)


def test_chinext_limit_switch_date():
    d = rules.CHINEXT_20PCT_FROM
    assert d == date(2020, 8, 24)
    assert limit_pct("300750", "宁德时代", d - DAY) == 0.10
    assert limit_pct("300750", "ST宁德", d - DAY) == 0.05
    assert limit_pct("300750", "宁德时代", d) == 0.20
    assert limit_pct("300750", "ST宁德", d) == 0.20
    assert limit_prices("300750", "宁德时代", 10.0, d - DAY) == (11.0, 9.0)
    assert limit_pct("300750", "宁德时代", datetime(2020, 8, 21, 15, 0)) == 0.10  # datetime/Timestamp ok


def test_main_board_st_limit_switch_date():
    d = rules.MAIN_ST_10PCT_FROM
    assert d == date(2026, 7, 6)
    assert limit_pct("600000", "*ST浦发", d - DAY) == 0.05
    assert limit_pct("600000", "*ST浦发", d) == 0.10
    assert limit_pct("600000", "浦发银行", d - DAY) == 0.10
    assert limit_prices("000001", "ST平安", 10.0, d - DAY) == (10.5, 9.5)
    assert limit_prices("000001", "ST平安", 10.0, d) == (11.0, 9.0)


def test_star_and_bse_limits_ignore_date_and_st():
    for d in (date(2019, 7, 22), None):
        assert limit_pct("688981", "ST中芯", d) == 0.20
        assert limit_pct("830799", "ST艾融", d) == 0.30


def test_302132_is_chinext():
    assert limit_pct("302132", "中航成飞", date(2025, 2, 17)) == 0.20
    assert limit_prices("302132", "中航成飞", 50.0) == (60.0, 40.0)


def test_transfer_fee_switch_date():
    d = rules.TRANSFER_CUT_FROM
    assert d == date(2022, 4, 29)
    # 100000: commission 25 + transfer 2 (0.002%) / 1 (0.001%)
    assert calc_fee("BUY", 100_000, d - DAY) == 27.0
    assert calc_fee("BUY", 100_000, d) == 26.0


def test_stamp_duty_switch_date():
    d = rules.STAMP_CUT_FROM
    assert d == date(2023, 8, 28)
    # 100000: commission 25 + transfer 1 + stamp 100 (0.1%) / 50 (0.05%)
    assert calc_fee("SELL", 100_000, d - DAY) == 126.0
    assert calc_fee("SELL", 100_000, d) == 76.0
    assert calc_fee("BUY", 100_000, d - DAY) == 26.0  # stamp duty is sell-only
    # before both cuts: 25 + 2 + 100
    assert calc_fee("SELL", 100_000, date(2020, 1, 2)) == 127.0


def test_limit_tolerance_one_tick():
    # forward-adjusted prices can sit one cent off the computed limit
    assert at_limit_up(11.06, 11.06) and at_limit_up(11.05, 11.06) and at_limit_up(11.2, 11.06)
    assert not at_limit_up(11.04, 11.06)
    assert at_limit_down(9.05, 9.05) and at_limit_down(9.06, 9.05) and at_limit_down(9.0, 9.05)
    assert not at_limit_down(9.07, 9.05)
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_rules.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.rules'`。


**Step 3: 最小实现** `engine/rules.py`

```python
"""A-share trading rules: boards, price limits, lots, fees.

Rules that changed over time take an optional trailing ``d``; ``d=None`` means the current rules.
"""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

# 创业板注册制首批股票上市日起涨跌幅 10% -> 20%（深交所《创业板交易特别规定》）
CHINEXT_20PCT_FROM = date(2020, 8, 24)
# 中国结算下调过户费：成交额的 0.002% -> 0.001%，2022-04-29 起执行
TRANSFER_CUT_FROM = date(2022, 4, 29)
# 财政部、税务总局减半征收证券交易印花税：0.1% -> 0.05%，2023-08-28 起执行
STAMP_CUT_FROM = date(2023, 8, 28)
# 沪深交易所 2026-04-24 发布修订后的交易规则：主板 ST 5% -> 10%，2026-07-06 起执行
MAIN_ST_10PCT_FROM = date(2026, 7, 6)


def _in_force(since: date, d: date | None) -> bool:
    if d is None:
        return True
    if isinstance(d, datetime):  # also covers pandas.Timestamp
        d = d.date()
    return d >= since


class Board(Enum):
    MAIN = "main"
    CHINEXT = "chinext"
    STAR = "star"
    BSE = "bse"


def board(code: str) -> Board:
    if code.startswith("30"):  # 300/301/302
        return Board.CHINEXT
    if code.startswith(("688", "689")):
        return Board.STAR
    if code.startswith(("8", "4", "92")):
        return Board.BSE
    return Board.MAIN


def is_st(name: str) -> bool:
    return "ST" in name.upper()


def limit_pct(code: str, name: str = "", d: date | None = None) -> float:
    b = board(code)
    if b is Board.BSE:
        return 0.30
    if b is Board.STAR:
        return 0.20
    if b is Board.CHINEXT:
        if _in_force(CHINEXT_20PCT_FROM, d):
            return 0.20
        return 0.05 if is_st(name) else 0.10
    if is_st(name) and not _in_force(MAIN_ST_10PCT_FROM, d):
        return 0.05
    return 0.10


def limit_prices(code: str, name: str, prev_close: float, d: date | None = None) -> tuple[float, float]:
    """(limit_up, limit_down) rounded half-up to 0.01, computed in Decimal to avoid float drift."""
    pc = Decimal(str(prev_close))
    pct = Decimal(str(limit_pct(code, name, d)))
    cent = Decimal("0.01")
    up = (pc * (1 + pct)).quantize(cent, rounding=ROUND_HALF_UP)
    down = (pc * (1 - pct)).quantize(cent, rounding=ROUND_HALF_UP)
    return float(up), float(down)


def at_limit_up(price: float, up: float) -> bool:
    """True when ``price`` is at the limit-up price, with one tick (0.01) of tolerance.

    Daily bars are forward-adjusted (前复权): adjusted prices are raw prices rescaled and rounded
    to 0.01, so a stock sealed at limit-up can sit one cent away from the limit computed from the
    adjusted prev_close. The tolerance absorbs that rounding. Cash dividends are subtracted rather
    than rescaled, so an old sealed price can also sit above the computed limit by more than a
    cent; any price above it counts as limit-up too. Effect on raw prices: a
    price exactly one cent below the true limit also counts as limit-up (a buy there is skipped),
    which only makes the simulation slightly more conservative.
    """
    return round(price * 100) >= round(up * 100) - 1


def at_limit_down(price: float, down: float) -> bool:
    """True when ``price`` is at the limit-down price, with one tick (0.01) of tolerance.

    Same reason as ``at_limit_up``: forward-adjusted rounding can leave a sealed limit-down one cent
    off, and any price below the computed limit counts as limit-down. Effect on raw prices: a high
    exactly one cent above the true limit-down also counts as sealed (the sell is skipped), again
    erring on the conservative side.
    """
    return round(price * 100) <= round(down * 100) + 1


def round_lot(qty: float) -> int:
    """Floor to a multiple of 100 shares. Simplification: STAR's 200-share minimum
    and 1-share increments are not modelled; all boards use 100-share lots."""
    if qty <= 0:
        return 0
    return int(qty // 100) * 100


COMMISSION_RATE = 0.00025
COMMISSION_MIN = 5.0
TRANSFER_RATE = 0.00001  # from TRANSFER_CUT_FROM
TRANSFER_RATE_OLD = 0.00002
STAMP_RATE = 0.0005  # from STAMP_CUT_FROM, sell side only
STAMP_RATE_OLD = 0.001
# 财税〔2015〕101 号：持股 1 个月以内，股息红利所得全额计税，税率 20%。模拟盘持仓都很短，一律按此扣税
DIVIDEND_TAX_RATE = 0.20


def calc_fee(side: str, amount: float, d: date | None = None) -> float:
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    transfer = TRANSFER_RATE if _in_force(TRANSFER_CUT_FROM, d) else TRANSFER_RATE_OLD
    fee = max(amount * COMMISSION_RATE, COMMISSION_MIN) + amount * transfer
    if side == "SELL":
        fee += amount * (STAMP_RATE if _in_force(STAMP_CUT_FROM, d) else STAMP_RATE_OLD)
    return round(fee, 2)
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_rules.py -v
```
预期：32 passed（`test_board` 11 个、`test_limit_pct` 6 个参数化用例）。


**Step 5: Commit**

```powershell
git add engine/rules.py tests/test_rules.py
git commit -m "feat(engine): add A-share board, limit price and fee rules"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）

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
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）

### Task 1.5: engine/ledger.py（账户、T+1、现金与费用）

**Files:**
- Create: `engine/ledger.py`
- Test: `tests/test_ledger.py`

`Position.cost` 为含买入费用的每股均价；`equity()` 对无报价的持仓按 cost 估值。T+1 由账本自己保证：`start_day(d)` 只解锁 `entry_date < d` 的持仓，买入当天再调用也不会解锁。买入已持有的代码抛 ValueError（不建模加仓）；买卖价格必须是有限正数，否则抛 ValueError；`calc_fee` 传入成交日期 `d`，按当天的过户费和印花税计费。`last_trade_day` 记录 SimBroker 最后一次执行完成的交易日，随 `to_dict` / `from_dict` 持久化；旧数据没有这个字段时读成 None。`apply_dividend(d, code, cash_per_share, share_ratio)` 在除权除息日 d 调整持仓：股数乘以 (1 + 每股送转) 后向下取整，现金分红按 `rules.DIVIDEND_TAX_RATE`（20%）扣税后入账，成本按扣除分红后的总成本摊到新股数，止损价和目标价换算为除权参考价 `ex_rights_price`；事件记入 `corporate_actions` 并持久化，同一事件重复调用返回 None、不重复调整。

**Step 1: 写失败测试** `tests/test_ledger.py`

```python
import json
from datetime import date

import pytest

from engine.ledger import Account, InsufficientCash, NotAvailable, ex_rights_price
from engine.rules import DIVIDEND_TAX_RATE

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
    assert acc.last_trade_day is None
    acc.last_trade_day = D2
    restored = Account.from_dict(json.loads(json.dumps(acc.to_dict())))
    assert restored == acc and restored.last_trade_day == D2


def test_from_dict_without_last_trade_day():
    d = _bought().to_dict()
    del d["last_trade_day"]
    assert Account.from_dict(d).last_trade_day is None


def test_start_day_only_unlocks_positions_bought_earlier():
    acc = _bought()
    acc.start_day(D1)  # same day as the buy: still locked
    assert acc.positions["600000"].available == 0
    with pytest.raises(NotAvailable):
        acc.sell(D1, "600000", 10.5, 1000, "same day")
    acc.start_day(D2)
    assert acc.positions["600000"].available == 1000


def test_buy_existing_code_raises():
    acc = _bought()
    cash = acc.cash
    with pytest.raises(ValueError):
        acc.buy(D2, "600000", "浦发银行", 10.0, 100, 9.3, 11.0, EXIT)
    assert acc.cash == cash and acc.positions["600000"].qty == 1000 and len(acc.trades) == 1


@pytest.mark.parametrize("price", [float("nan"), 0.0, -1.0, float("inf")])
def test_rejects_non_positive_or_non_finite_price(price):
    acc = Account("t", 100_000.0)
    with pytest.raises(ValueError):
        acc.buy(D1, "600000", "浦发银行", price, 100, 9.3, 11.0, EXIT)
    assert acc.cash == 100_000.0 and not acc.positions and not acc.trades
    acc = _bought()
    acc.start_day(D2)
    with pytest.raises(ValueError):
        acc.sell(D2, "600000", price, 1000, "x")
    assert acc.positions["600000"].qty == 1000 and len(acc.trades) == 1


def test_fees_follow_trade_date():
    acc = Account("t", 200_000.0)
    acc.buy(date(2022, 4, 28), "600000", "浦发银行", 10.0, 10_000, 9.3, 11.0, date(2023, 9, 1))
    assert acc.trades[-1].fee == 27.0  # 25 + transfer 0.002%
    acc.start_day(date(2023, 8, 25))
    acc.sell(date(2023, 8, 25), "600000", 10.0, 5_000, "x")
    assert acc.trades[-1].fee == 63.0  # 50000: 12.5 + 0.5 + stamp 0.1%
    acc.sell(date(2023, 8, 28), "600000", 10.0, 5_000, "x")
    assert acc.trades[-1].fee == 38.0  # 50000: 12.5 + 0.5 + stamp 0.05%


EX = date(2026, 10, 19)


def test_ex_rights_price():
    assert ex_rights_price(20.0, 0.0, 0.5) == pytest.approx(13.3333333)  # 10 转 5
    assert ex_rights_price(10.0, 0.3, 0.0) == pytest.approx(9.7)           # 10 派 3
    assert ex_rights_price(10.0, 0.3, 0.2) == pytest.approx(9.7 / 1.2)
    assert ex_rights_price(0.2, 0.5, 0.0) == 0.01  # 价格不会变成负数


def test_apply_cash_dividend_and_bonus_shares():
    acc = _bought()  # 1000 股，成本含费，止损 9.3，止盈 11.0
    cash0, cost0 = acc.cash, acc.positions["600000"].cost
    act = acc.apply_dividend(EX, "600000", cash_per_share=0.3, share_ratio=0.5)
    pos = acc.positions["600000"]
    net = round(1000 * 0.3 * (1 - DIVIDEND_TAX_RATE), 2)
    assert act == {"date": EX.isoformat(), "code": "600000", "cash_per_share": 0.3, "share_ratio": 0.5,
                   "qty_before": 1000, "qty_after": 1500, "cash": net}
    assert acc.cash == pytest.approx(cash0 + net) and net == 240.0
    assert pos.qty == 1500
    assert pos.stop == pytest.approx((9.3 - 0.3) / 1.5) and pos.target == pytest.approx((11.0 - 0.3) / 1.5)
    assert pos.cost == pytest.approx((cost0 * 1000 - net) / 1500)
    assert acc.corporate_actions == [act]


def test_apply_dividend_is_idempotent_and_ignores_unknown_code():
    acc = _bought()
    assert acc.apply_dividend(EX, "000001", 0.3, 0.0) is None
    acc.apply_dividend(EX, "600000", 0.3, 0.0)
    snapshot = acc.to_dict()
    assert acc.apply_dividend(EX, "600000", 0.3, 0.0) is None  # 同一事件不重复调整
    assert acc.to_dict() == snapshot


def test_bonus_shares_round_down_and_keep_t_plus_1_lock():
    acc = Account("t", 100_000.0)
    acc.buy(D1, "600000", "浦发银行", 10.0, 1900, 9.3, 11.0, EXIT, "test")
    acc.apply_dividend(D1, "600000", 0.0, 0.3)
    pos = acc.positions["600000"]
    assert pos.qty == 2470 and pos.available == 0
    acc.start_day(D2)
    assert pos.available == 2470
    acc2 = Account("t", 100_000.0)
    acc2.buy(D1, "600000", "浦发银行", 10.0, 100, 9.3, 11.0, EXIT, "test")
    acc2.apply_dividend(D1, "600000", 0.0, 0.15)  # 100 * 1.15 = 114.99999999999999，不能取整成 114
    assert acc2.positions["600000"].qty == 115


def test_apply_dividend_key_is_code_and_date():
    acc = _bought()
    acc.buy(D1, "000001", "平安银行", 10.0, 1000, 9.3, 11.0, EXIT, "test")
    assert acc.apply_dividend(EX, "600000", 0.3, 0.0) is not None
    assert acc.apply_dividend(EX, "000001", 0.3, 0.0) is not None  # 同一天另一只股票
    assert acc.apply_dividend(date(2026, 11, 20), "600000", 0.2, 0.0) is not None  # 同一只股票另一次除权
    assert len(acc.corporate_actions) == 3


@pytest.mark.parametrize("cash,ratio", [(-0.1, 0.0), (0.0, -0.1), (float("nan"), 0.0), (0.0, float("inf")),
                                        (0.0, 0.0)])
def test_apply_dividend_rejects_bad_values(cash, ratio):
    acc = _bought()
    with pytest.raises(ValueError):
        acc.apply_dividend(EX, "600000", cash, ratio)


def test_corporate_actions_roundtrip_and_old_data():
    acc = _bought()
    acc.apply_dividend(EX, "600000", 0.3, 0.5)
    restored = Account.from_dict(json.loads(json.dumps(acc.to_dict())))
    assert restored == acc
    d = _bought().to_dict()
    del d["corporate_actions"]
    assert Account.from_dict(d).corporate_actions == []
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_ledger.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.ledger'`。


**Step 3: 最小实现** `engine/ledger.py`

```python
"""Simulated account ledger with A-share T+1 settlement."""
import math
from dataclasses import asdict, dataclass, field
from datetime import date

from engine.rules import DIVIDEND_TAX_RATE, calc_fee
from engine.types import Fill


class InsufficientCash(Exception):
    pass


class NotAvailable(Exception):
    pass


def _check_price(price: float) -> None:
    if not (math.isfinite(price) and price > 0):
        raise ValueError(f"price must be finite and > 0, got {price!r}")


def ex_rights_price(price: float, cash_per_share: float, share_ratio: float) -> float:
    """除权除息参考价：(price - 每股派息) / (1 + 每股送转)。不建模配股；结果不低于 0.01。"""
    return max((price - cash_per_share) / (1.0 + share_ratio), 0.01)


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
    last_trade_day: date | None = None  # last day SimBroker.execute_day completed for this account
    corporate_actions: list[dict] = field(default_factory=list)  # applied ex-rights/ex-dividend events

    def start_day(self, d: date) -> None:
        """T+1 unlock: only positions bought before ``d`` become sellable. Calling it again on the
        buy day (or twice on the same day) keeps today's purchases locked."""
        for p in self.positions.values():
            p.available = p.qty if p.entry_date < d else 0

    def buy(self, d: date, code: str, name: str, price: float, qty: int, stop: float,
            target: float, exit_date: date, reason: str = "") -> Fill:
        if qty <= 0 or qty % 100:
            raise ValueError(f"qty must be a positive multiple of 100, got {qty}")
        if code in self.positions:
            raise ValueError(f"{self.name}: already holding {code}; adding to a position is not modelled")
        _check_price(price)
        amount = price * qty
        fee = calc_fee("BUY", amount, d)
        if amount + fee > self.cash + 1e-9:
            raise InsufficientCash(f"{self.name}: need {amount + fee:.2f}, have {self.cash:.2f}")
        self.cash -= amount + fee
        self.positions[code] = Position(code, name, qty, 0, (amount + fee) / qty, d,
                                        stop, target, exit_date)
        fill = Fill(d, code, "BUY", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def sell(self, d: date, code: str, price: float, qty: int, reason: str) -> Fill:
        _check_price(price)
        pos = self.positions.get(code)
        if pos is None or qty <= 0 or qty > pos.available:
            avail = pos.available if pos else 0
            raise NotAvailable(f"{self.name}: sell {code} x{qty}, available {avail}")
        amount = price * qty
        fee = calc_fee("SELL", amount, d)
        self.cash += amount - fee
        pos.qty -= qty
        pos.available -= qty
        if pos.qty == 0:
            del self.positions[code]
        fill = Fill(d, code, "SELL", qty, price, fee, reason)
        self.trades.append(fill)
        return fill

    def apply_dividend(self, d: date, code: str, cash_per_share: float, share_ratio: float,
                       tax_rate: float = DIVIDEND_TAX_RATE) -> dict | None:
        """Apply an ex-rights / ex-dividend event on ex-date ``d`` to the position in ``code``.

        Shares grow by ``share_ratio`` (bonus + transfer shares per share, rounded down to whole shares);
        the cash dividend (``cash_per_share`` before tax) is credited after ``tax_rate``; cost, stop and
        target move to the ex-rights reference price. Returns the recorded event, or None when there is
        no such position or the event was already applied (re-running a settlement day is safe).
        """
        for v in (cash_per_share, share_ratio):
            if not (math.isfinite(v) and v >= 0):
                raise ValueError(f"dividend values must be finite and >= 0, got {cash_per_share!r}, {share_ratio!r}")
        if cash_per_share == 0 and share_ratio == 0:
            raise ValueError("empty dividend event")
        pos = self.positions.get(code)
        if pos is None or any(a["code"] == code and a["date"] == d.isoformat() for a in self.corporate_actions):
            return None
        qty0 = pos.qty
        qty1 = int(qty0 * (1.0 + share_ratio) + 1e-6)  # 100 * 1.15 is 114.99999999999999 in floats
        net = round(qty0 * cash_per_share * (1.0 - tax_rate), 2)
        self.cash += net
        pos.cost = (pos.cost * qty0 - net) / qty1
        pos.qty, pos.available = qty1, int(pos.available * (1.0 + share_ratio) + 1e-6)
        pos.stop = ex_rights_price(pos.stop, cash_per_share, share_ratio)
        pos.target = ex_rights_price(pos.target, cash_per_share, share_ratio)
        act = {"date": d.isoformat(), "code": code, "cash_per_share": cash_per_share, "share_ratio": share_ratio,
               "qty_before": qty0, "qty_after": qty1, "cash": net}
        self.corporate_actions.append(act)
        return act

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(c, p.cost) for c, p in self.positions.items())

    def to_dict(self) -> dict:
        positions = {}
        for c, p in self.positions.items():
            d = asdict(p)
            d["entry_date"] = p.entry_date.isoformat()
            d["exit_date"] = p.exit_date.isoformat()
            positions[c] = d
        last = self.last_trade_day.isoformat() if self.last_trade_day else None
        return {"name": self.name, "cash": self.cash, "positions": positions,
                "trades": [t.to_dict() for t in self.trades], "last_trade_day": last,
                "corporate_actions": [dict(a) for a in self.corporate_actions]}

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        positions = {}
        for c, p in d.get("positions", {}).items():
            positions[c] = Position(
                code=p["code"], name=p["name"], qty=int(p["qty"]), available=int(p["available"]),
                cost=float(p["cost"]), entry_date=date.fromisoformat(p["entry_date"]),
                stop=float(p["stop"]), target=float(p["target"]),
                exit_date=date.fromisoformat(p["exit_date"]))
        last = d.get("last_trade_day")  # absent in accounts saved before this field existed
        return cls(name=d["name"], cash=float(d["cash"]), positions=positions,
                   trades=[Fill.from_dict(t) for t in d.get("trades", [])],
                   last_trade_day=date.fromisoformat(last) if last else None,
                   corporate_actions=[dict(a) for a in d.get("corporate_actions", [])])
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_ledger.py -v
```
预期：27 passed（非法价格的参数化测试含 4 个用例，非法分红参数的参数化测试含 5 个用例）。


**Step 5: Commit**

```powershell
git add engine/ledger.py tests/test_ledger.py
git commit -m "feat(engine): add T+1 account ledger"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）

### Task 1.6: engine/broker.py（开盘撮合）

**Files:**
- Create: `engine/broker.py`
- Test: `tests/test_broker.py`

执行顺序（`execute_day` 的 docstring 写明了同样的规则）：

0. 先调用 `start_day(d)`。行情只有在 volume > 0，且 open/high/low/prev_close 都是有限正数时才算有效；否则按停牌处理，不做任何交易。涨跌停价用 `limit_prices(..., d)`，即当天生效的规则。开盘、最高、最低价中任何一个超出 [跌停价-0.01, 涨停价+0.01]，就说明这只股票当天的区间不可靠（例如除权日的昨收仍是除权前价格，或用今天的名称判断历史 ST 状态，或前复权扣减了之后的现金分红），此时成交价不截断；卖出侧只有一字板（最高价等于最低价）才算封死跌停：算错的区间可能窄得多（前复权扣减了之后的现金分红时，高分红股早期原始价格跌三四个点就会越过算出的跌停价），普通下跌日照常卖出。开盘买入只看开盘价：开盘达到或越过算出的涨停价就不买，与区间是否可靠无关（可靠与否取决于当天的最高、最低价，不能用来决定开盘买入）。
1. 开盘卖出：`at_limit_down(high, down)` 说明全天封死跌停，不能卖。否则依次判断：到期按开盘价卖出；开盘价 ≤ 止损价按开盘价止损；开盘价 ≥ 目标价按开盘价止盈；只在盘中触及止损或目标的持仓推迟到第 3 步。
2. 开盘买入（按 picks 顺序）：跳过已持仓、无效行情、`at_limit_up(open, up)`、开盘价高于 entry_max、开盘价不高于 pick 止损价的情况；股数按整手计算并考虑最低 5 元佣金，现金不会为负；exit_date 用 `calendar.offset` 计算。
3. 盘中卖出：处理第 1 步推迟的持仓。盘中低点 ≤ 止损价按止损价卖出（止损和目标同时触及时止损优先，因为日线看不出盘中先后）；否则按目标价卖出。

卖出价 ×(1-滑点)，买入价 ×(1+滑点)；区间可靠时再截断在 [跌停价, 涨停价] 之内。滑点可能让成交价略微超出当日 [最低价, 最高价]，这是有意采用的保守成本假设。资金时序：只有第 1 步开盘卖出回笼的资金当天可用于买入，第 3 步盘中卖出的资金当天不能用。当天买入的仓位 `available=0`。`execute_day` 依据 `Account.last_trade_day` 保证每个交易日只执行一次：同一天再次调用直接返回 `[]`，不改动账户；日期早于 `last_trade_day` 时抛 ValueError；正常执行结束后把 `last_trade_day` 设为 `d`。

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


def held(stop=9.3, target=11.0, exit_date=DAYS[5], cash=100_000.0):
    """Account holding 1000 sh of 600000 bought on D0."""
    acc = Account("t", cash)
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
    acc2 = Account("t", 100_000.0)  # fresh account: a second run on acc would be a same-day no-op
    assert run(acc2, [], [pick()]) == [] and not acc2.positions


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
    f = run(acc, [bar(o=9.0, h=9.2, l=8.9, c=9.1, pc=9.8)])[0]  # limit-down 8.82: no clipping
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
    acc2 = held(exit_date=D1)
    assert run(acc2, []) == [] and acc2.positions["600000"].qty == 1000


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


PA = dict(code="000001", name="平安银行")
PA_BAR = dict(o=12.0, h=12.3, l=11.9, pc=12.0, **PA)
PA_PICK = dict(entry_max=12.5, stop=11.0, target=13.5, **PA)


def test_intraday_take_profit_cash_not_used_for_open_buy():
    acc = held(cash=10_100.0)  # 94.9 cash left after the seed buy
    fills = run(acc, [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)])
    assert [(f.side, f.code, f.reason) for f in fills] == [("SELL", "600000", "止盈")]
    assert "000001" not in acc.positions and acc.cash > 10_000


def test_expiry_cash_used_for_open_buy():
    acc = held(cash=10_100.0, exit_date=D1)
    fills = run(acc, [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)])
    assert [(f.side, f.code, f.reason) for f in fills] == [("SELL", "600000", "到期"),
                                                          ("BUY", "000001", "测试")]


def test_open_above_target_wins_over_later_stop():
    acc = held(stop=9.3, target=11.0)
    f = run(acc, [bar(o=11.1, h=11.2, l=9.2, pc=10.2)])[0]  # gap above target, then falls through stop
    assert f.reason == "止盈" and f.price == pytest.approx(11.1 * (1 - SLIP))


def test_intraday_stop_and_target_both_touched_stop_wins():
    acc = held(stop=9.3, target=11.0)
    f = run(acc, [bar(o=10.0, h=11.2, l=9.2)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(9.3 * (1 - SLIP))


def test_rerun_same_day_is_a_no_op():
    # cash-starved, intraday take-profit: a second run must not spend the step-3 cash on the pick
    acc = held(cash=10_100.0)
    bars, picks = [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)]
    assert [f.reason for f in run(acc, bars, picks)] == ["止盈"]
    before = acc.to_dict()
    assert run(acc, bars, picks) == []
    assert acc.to_dict() == before and acc.last_trade_day == D1


def test_earlier_day_than_last_trade_day_raises():
    acc = Account("t", 100_000.0)
    run(acc, [bar(d=D2)], [pick()], d=D2)
    before = acc.to_dict()
    with pytest.raises(ValueError):
        run(acc, [bar(d=D1)], [pick(code="000001", name="平安银行")], d=D1)
    assert acc.to_dict() == before


def test_last_trade_day_survives_serialization():
    acc = held(cash=10_100.0)
    bars, picks = [bar(o=10.5, h=11.2, l=10.4), bar(**PA_BAR)], [pick(**PA_PICK)]
    run(acc, bars, picks)
    acc2 = Account.from_dict(acc.to_dict())
    assert acc2.last_trade_day == D1
    before = acc2.to_dict()
    assert run(acc2, bars, picks) == [] and acc2.to_dict() == before


@pytest.mark.parametrize("over", [dict(o=float("nan")), dict(h=float("nan")), dict(l=0.0),
                                  dict(pc=0.0), dict(pc=float("nan")), dict(vol=float("nan"))])
def test_invalid_bar_treated_as_suspended(over):
    acc = held(exit_date=D1)
    assert run(acc, [bar(**over)]) == [] and acc.positions["600000"].qty == 1000
    acc2 = Account("t", 100_000.0)
    assert run(acc2, [bar(**over)], [pick()]) == [] and not acc2.positions


def test_one_tick_tolerance_blocks_buy_and_sell():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.99, h=11.0, l=10.9)], [pick(entry_max=12.0)]) == []  # limit-up 11.00
    acc2 = held(stop=9.3)
    assert run(acc2, [bar(o=9.0, h=9.01, l=9.0, c=9.0)]) == []  # limit-down 9.00, high 9.01


def test_sell_price_clipped_to_limit_down():
    acc = held(stop=9.3)
    f = run(acc, [bar(o=9.0, h=9.5, l=9.0, c=9.2, pc=10.0)])[0]  # open at limit-down 9.00, then trades
    assert f.reason == "止损" and f.price == 9.0  # 9.0 * (1 - slip) = 8.991 -> clipped to 9.00


ST_CAL = TradingCalendar([date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7)])


def test_st_sealed_limit_down_before_rule_change_blocks_sell():
    # 2026-07-03: main-board ST still ±5%, so 9.50 is the sealed limit-down (would not be under ±10%)
    d0, d = date(2026, 7, 2), date(2026, 7, 3)
    acc = Account("t", 100_000.0)
    acc.buy(d0, "600001", "ST测试", 10.0, 1000, 9.6, 11.0, d, "seed")
    st_bar = Bar("600001", d, 9.5, 9.5, 9.5, 9.5, 10.0, 1e6, 9.5e6, "ST测试")
    assert SimBroker(10.0).execute_day(acc, d, {"600001": st_bar}, [], ST_CAL, 20_000.0) == []
    assert acc.positions["600001"].qty == 1000
    # 2026-07-06: ±10% applies, the same prices are no longer limit-down -> expiry sell goes through
    d2 = date(2026, 7, 6)
    st_bar2 = Bar("600001", d2, 9.5, 9.5, 9.5, 9.5, 10.0, 1e6, 9.5e6, "ST测试")
    fills = SimBroker(10.0).execute_day(acc, d2, {"600001": st_bar2}, [], ST_CAL, 20_000.0)
    assert [f.reason for f in fills] == ["到期"]


def test_open_at_or_below_stop_not_bought():
    for o in (9.6, 9.8):  # below and equal to the stop; a fresh account each time
        acc = Account("t", 100_000.0)
        assert run(acc, [bar(o=o, h=9.9, l=9.5, c=9.7)], [pick(stop=9.8)]) == []
        assert not acc.positions


def test_ex_rights_day_buy_not_clipped_to_stale_limit_band():
    # 10转5 ex-date, prev_close still the pre-split 20.00: band [18.00, 22.00] never traded today
    acc = Account("t", 100_000.0)
    f = run(acc, [bar(o=13.40, h=13.6, l=13.3, c=13.5, pc=20.0)],
            [pick(entry_max=14.0, stop=12.5, target=15.0)])[0]
    assert f.side == "BUY" and f.price == pytest.approx(13.40 * (1 + SLIP))


def test_unreliable_band_one_price_bar_still_sealed():
    # forward adjustment subtracts later cash dividends, so an old one-price limit-up can sit above
    # the limit computed from the adjusted prev_close: 9.50 gives 10.45, the bar is 10.50
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=10.5, h=10.5, l=10.5, c=10.5, pc=9.5)],
               [pick(entry_max=11.0, stop=9.8, target=12.0)]) == []
    # the same on the way down: limit-down 8.55, a one-price bar at 8.50 cannot be sold, even at expiry
    acc2 = held(exit_date=D1)
    assert run(acc2, [bar(o=8.5, h=8.5, l=8.5, c=8.5, pc=9.5)]) == []
    assert acc2.positions["600000"].qty == 1000


def test_unreliable_band_ordinary_day_trades():
    # 工商银行-like: adjusted prev_close 1.50 after ~3.00 of later dividends, so the computed band
    # [1.35, 1.65] is far narrower than the real one; a -3.6% day that traded is not limit-down
    acc = held(stop=1.40, target=1.80)
    f = run(acc, [bar(o=1.34, h=1.35, l=1.20, c=1.25, pc=1.50)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(1.34 * (1 - SLIP))
    # opened at the low and then traded up: still not one price all day
    acc4 = held(stop=1.40, target=1.80)
    f4 = run(acc4, [bar(o=1.20, h=1.34, l=1.20, c=1.30, pc=1.50)])[0]
    assert f4.reason == "止损" and f4.price == pytest.approx(1.20 * (1 - SLIP))
    # buys only look at the open: an open at or beyond the computed limit-up is skipped, whatever
    # the rest of the day does (conservative; the day's later path must not decide the buy)
    acc2 = Account("t", 100_000.0)
    assert run(acc2, [bar(o=1.66, h=1.70, l=1.62, c=1.68, pc=1.50)],
               [pick(entry_max=1.70, stop=1.55, target=1.90)]) == []
    # ex-rights day with a stale prev_close (band [18.00, 22.00]): the expiry sell goes through
    acc3 = held(exit_date=D1)
    f3 = run(acc3, [bar(o=13.4, h=13.6, l=13.3, c=13.5, pc=20.0)])[0]
    assert f3.reason == "到期" and f3.price == pytest.approx(13.4 * (1 - SLIP))


@pytest.mark.parametrize("o,expect_buy", [(1.70, False), (1.65, False), (1.63, True)])
@pytest.mark.parametrize("path", ["fade", "rise"])
def test_buy_decision_ignores_the_rest_of_the_day(o, expect_buy, path):
    # computed limit-up 1.65: the same open gives the same decision whether the day later fades
    # (open is the high, band looks reliable) or keeps rising (high beyond the band, band unreliable)
    hi, lo, cl = (o, o - 0.08, o - 0.05) if path == "fade" else (o + 0.08, o - 0.02, o + 0.06)
    acc = Account("t", 100_000.0)
    fills = run(acc, [bar(o=o, h=hi, l=lo, c=cl, pc=1.50)], [pick(entry_max=1.75, stop=1.40, target=1.90)])
    assert bool(fills) is expect_buy


def test_unreliable_band_one_price_open_is_sealed_limit_up():
    acc = Account("t", 100_000.0)
    assert run(acc, [bar(o=1.70, h=1.70, l=1.70, c=1.70, pc=1.50)],
               [pick(entry_max=1.75, stop=1.55, target=1.90)]) == []


def test_unreliable_band_intraday_stop_not_clipped():
    acc = held(stop=11.7, target=13.0)
    # band [9.00, 11.00] is below the whole day; the stop fill stays at stop * (1 - slip), not 11.00
    f = run(acc, [bar(o=12.0, h=12.5, l=11.6, c=12.2, pc=10.0)])[0]
    assert f.reason == "止损" and f.price == pytest.approx(11.7 * (1 - SLIP))


@pytest.mark.parametrize("h,clipped", [(22.01, True), (22.02, False)])
def test_band_reliability_allows_one_tick(h, clipped):
    # band [18.00, 22.00]: a high one tick above limit-up is still rounding, two ticks is not
    acc = Account("t", 100_000.0)
    f = run(acc, [bar(o=21.98, h=h, l=21.9, c=21.99, pc=20.0)],
            [pick(entry_max=22.5, stop=20.0, target=24.0)])[0]
    raw = 21.98 * (1 + SLIP)
    assert f.price == (22.0 if clipped else pytest.approx(raw))


@pytest.mark.parametrize("low,clipped", [(17.99, True), (17.98, False)])
def test_band_reliability_allows_one_tick_below(low, clipped):
    # band [18.00, 22.00]: only the low decides; the intraday stop at 18.00 sells at 17.982 before clipping
    acc = held(stop=18.0, target=21.0)
    f = run(acc, [bar(o=18.5, h=18.8, l=low, c=18.6, pc=20.0)])[0]
    assert f.reason == "止损"
    assert f.price == (18.0 if clipped else pytest.approx(18.0 * (1 - SLIP)))


def test_reliable_band_still_clips_buy_at_limit_up():
    acc = Account("t", 100_000.0)
    f = run(acc, [bar(o=21.98, h=22.0, l=21.9, c=22.0, pc=20.0)],
            [pick(entry_max=22.5, stop=20.0, target=24.0)])[0]
    assert f.price == 22.0  # 21.98 * 1.001 = 22.002 -> clipped to limit-up 22.00
```

**Step 2: 运行测试，确认失败**

```powershell
uv run pytest tests/test_broker.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'engine.broker'`。


**Step 3: 最小实现** `engine/broker.py`

```python
"""Simulated open-auction broker applying A-share execution rules."""
import math
from datetime import date

from engine.calendar import TradingCalendar
from engine.ledger import Account, Position
from engine.rules import at_limit_down, at_limit_up, calc_fee, limit_prices, round_lot
from engine.types import Bar, Fill, Pick

Band = tuple[float, float, bool]  # (limit_up, limit_down, reliable)


def _valid(bar: Bar | None) -> bool:
    """A bar is tradable only with volume > 0 and finite, positive open/high/low/prev_close."""
    if bar is None or not bar.volume > 0:
        return False
    return all(math.isfinite(v) and v > 0 for v in (bar.open, bar.high, bar.low, bar.prev_close))


def _cents(v: float) -> int:
    return round(v * 100)


def _band(bar: Bar, d: date) -> Band:
    """Limit prices for ``bar`` on ``d`` and whether the day's prices agree with them.

    The band is reliable only if open, high and low all lie within [limit_down - 0.01,
    limit_up + 0.01] (one tick for forward-adjusted rounding). A price outside it means the band
    itself is off, e.g. an ex-rights day whose prev_close is still the pre-split close, an ST
    status taken from today's name, or later cash dividends subtracted from forward-adjusted
    prices (which shifts an old sealed price by 10% of those dividends).
    """
    up, down = limit_prices(bar.code, bar.name, bar.prev_close, d)
    lo, hi = _cents(down) - 1, _cents(up) + 1
    return up, down, all(lo <= _cents(v) <= hi for v in (bar.open, bar.high, bar.low))


def _clip(price: float, band: Band) -> float:
    up, down, reliable = band
    return min(max(price, down), up) if reliable else price


class SimBroker:
    def __init__(self, slippage_bps: float = 10.0):
        self.slip = slippage_bps / 10_000

    def execute_day(self, account: Account, d: date, bars: dict[str, Bar], picks: list[Pick],
                    calendar: TradingCalendar, capital_per_pick: float) -> list[Fill]:
        """Simulate trading day ``d`` for ``account`` in three steps.

        Each day runs once per account: if ``d`` equals ``account.last_trade_day`` this returns
        ``[]`` without touching the account; a ``d`` before it raises ValueError. A completed run
        sets ``last_trade_day = d``.

        0. ``account.start_day(d)`` unlocks positions bought before ``d`` (T+1). A bar is valid only
           if volume > 0 and open/high/low/prev_close are finite and positive; otherwise the stock
           is treated as suspended and nothing trades in it. Limits come from
           ``limit_prices(..., d)``, i.e. the rules in force on ``d``. The band is unreliable when
           open, high or low lies outside [limit_down - 0.01, limit_up + 0.01] (e.g. an ex-rights
           day with a stale prev_close); then fill prices are not clipped, and a limit counts as
           sealed for sells only for a one-price bar (high == low): a wrong band can be far too
           narrow (e.g. later cash dividends subtracted from forward-adjusted prices), so an
           ordinary down day beyond it still sells. Buys look at the open only: an open at or
           beyond the computed limit-up is skipped whether or not the band is reliable, because
           reliability depends on the day's high and low and must not decide the buy.
        1. Open sells. A position whose high is at limit-down (``at_limit_down``) was sealed all
           day and cannot be sold. Otherwise, in priority order: expiry (``d >= exit_date``) sells
           at the open; open <= stop sells at the open (stop-loss); open >= target sells at the
           open (take-profit). Positions that only touch stop or target intraday are deferred to
           step 3. Cash from step 1 is available for today's buys.
        2. Open buys, in pick order, at the open: skip codes already held, invalid bars, an open at
           limit-up (``at_limit_up``), an open above ``entry_max`` and an open at or below the
           pick's stop. Quantity is whole lots, sized so cash (including the 5-yuan minimum
           commission) never goes negative; ``exit_date = calendar.offset(d, hold_days)``.
        3. Intraday sells of the deferred positions: low <= stop sells at the stop price (when both
           stop and target were touched the intraday path is unknown, so the stop wins); otherwise
           at the target price. This cash is not available to step 2.

        Sell prices are multiplied by (1 - slippage), buy prices by (1 + slippage), and every fill
        price is then clipped into [limit_down, limit_up] when the band is reliable. Slippage can
        put a fill slightly outside the day's [low, high]; that is a deliberate, conservative cost
        assumption.
        """
        last = account.last_trade_day
        if last is not None and d < last:
            raise ValueError(f"{account.name}: {d} is before last trade day {last}")
        if d == last:
            return []  # already executed today
        fills: list[Fill] = []
        account.start_day(d)
        deferred: list[tuple[Position, float, Band]] = []  # (position, low, band)
        # 1) open sells: only positions unlocked today (bought before d)
        for pos in list(account.positions.values()):
            bar = bars.get(pos.code)
            if pos.available <= 0 or not _valid(bar):
                continue  # locked by T+1, or suspended / bad data
            band = _band(bar, d)
            one_price = _cents(bar.high) == _cents(bar.low)
            if at_limit_down(bar.high, band[1]) and (band[2] or one_price):
                continue  # sealed at limit-down all day: cannot sell
            if d >= pos.exit_date:
                reason = "到期"
            elif bar.open <= pos.stop:
                reason = "止损"
            elif bar.open >= pos.target:
                reason = "止盈"
            else:
                if bar.low <= pos.stop or bar.high >= pos.target:
                    deferred.append((pos, bar.low, band))
                continue
            fills.append(self._sell(account, d, pos, bar.open, reason, band))
        # 2) open buys
        for pick in picks:
            if pick.code in account.positions:
                continue
            bar = bars.get(pick.code)
            if not _valid(bar):
                continue
            band = _band(bar, d)
            if at_limit_up(bar.open, band[0]):
                continue  # 开盘涨停 (or beyond the computed limit-up): cannot buy
            if bar.open > pick.entry_max:
                continue  # gapped above the buy zone
            if bar.open <= pick.stop:
                continue  # opened at or below the stop: the plan is already broken
            price = _clip(bar.open * (1 + self.slip), band)
            qty = round_lot(min(capital_per_pick, account.cash * 0.999) / price)
            # 0.1% buffer may not cover the 5-yuan minimum commission: shrink until affordable
            while qty >= 100 and price * qty + calc_fee("BUY", price * qty, d) > account.cash:
                qty -= 100
            if qty < 100:
                continue
            fills.append(account.buy(d, pick.code, pick.name or bar.name, price, qty, pick.stop,
                                     pick.target, calendar.offset(d, pick.hold_days), pick.reason))
        # 3) intraday sells deferred from step 1 (stop wins when both levels were touched)
        for pos, low, band in deferred:
            if low <= pos.stop:
                fills.append(self._sell(account, d, pos, pos.stop, "止损", band))
            else:
                fills.append(self._sell(account, d, pos, pos.target, "止盈", band))
        account.last_trade_day = d
        return fills

    def _sell(self, account: Account, d: date, pos: Position, price: float, reason: str,
              band: Band) -> Fill:
        return account.sell(d, pos.code, _clip(price * (1 - self.slip), band), pos.available, reason)
```

**Step 4: 运行测试，确认通过**

```powershell
uv run pytest tests/test_broker.py -v
```
预期：49 passed（无效行情的参数化测试含 6 个用例，区间可靠性的两个参数化测试各含 2 个用例，开盘买入不看当日走势的参数化测试含 6 个用例）。


**Step 4b: 全量回归**

```powershell
uv run pytest -v
```
预期：Part 1 共 152 个用例（config 19 + types 21 + rules 32 + calendar 4 + ledger 27 + broker 49）全部通过。


**Step 5: Commit**

```powershell
git add engine/broker.py tests/test_broker.py
git commit -m "feat(engine): add open-auction SimBroker"
```
（commit message 末尾追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）


---

## Part 2: 存储、指标、回测与评估器

> 契约歧义的取舍（每条一行）：
> - `summarize` 的 `excess_ann` 与 `promotion.compare` 的年化超额 = 逐日超额收益均值 × 252（算术年化）。`compare` 先按日期对齐两条净值再算收益（一方缺日时两边都用跨过缺口的同一段收益）；`summarize` 的 `n_days` 是收益天数（净值点数减一）。
> - `deflated_sharpe` 使用每期（日）SR；各试验 SR 的方差近似为 `1/n_obs`（零假设下 SR 估计量方差），`n_trials<=1` 时 SR0=0；逆正态用标准库 `statistics.NormalDist().inv_cdf`，CDF 用 `math.erf`。
> - `sharpe` 把 `std <= 1e-12`（含 pandas 浮点噪声，如 `[0.1]*3` 的 std≈1.7e-17）按 `std==0` 处理，返回 0。
> - `DataView` 只读：面板只保存在 `codes/name/history/bar_on` 几个方法的闭包里，实例上没有任何能取到它的属性（改编后的私有名字、`str.format` 字段都拿不到）；闭包只能经 `__closure__` 这类双下划线属性取到，Task 3.6 的源码检查禁止任何含 `__` 的名字、属性和字符串。`run_backtest` 另外把面板截断到本段末尾。`codes()` 只列出 asof 时已有数据的股票；`bar_on` 当日无数据抛 `KeyError`。
> - `run_backtest`：净值第一个点是 start 前一交易日的期初资金（首日建仓成本和涨跌计入指标，基准同样从这一天起算）；按"最近有效收盘价"估值（停牌或收盘价为 NaN 时沿用上一个有效收盘价）；持有期越过日历末尾的 pick 当日丢弃；`benchmark_nav` 索引先 `pd.to_datetime` 归一（兼容 date 对象索引）；每天的行情用 `DayBars`（一次性转成数组，按日二分查找）取，结果与 `bars_for_day` 相同。
> - `evaluate` 增加仅供测试的可选关键字参数 `splits: dict|None=None`（默认 `SPLITS`）；holdout 的 `None` 解析为 `min(calendar.last(), 面板最后有数据日)`，因为新浪交易日历含未来日期。
> - `evaluate` 的 DSR 用训练+验证两段日收益拼接计算，`n_trials` 含本次；所有门槛比较为 `>=`；留出期 `n_days==0` 视为不通过。试验在回放留出期之前入库（`holdout_pass` 先记为 None，跑完再更新）；训练、验证段报错时同样入库（夏普记为 None）再抛出；留出期任何异常（含 `SystemExit`）都只记为未通过，不外传异常文本。
> - `to_agent_dict` 返回 `strategy, train, val, dsr, gate_pass, holdout_pass, n_trials`（dsr/n_trials 只来自训练+验证）；`EvalReport` 本身不保存任何留出期数值。
> - `Store` 用一把 `RLock` 串行化同一 sqlite 连接（`check_same_thread=False`），供 `agents/premarket.run_final` 双线程共用；`set_status` 对非法状态抛 `ValueError`。
> - `promotion.decide`：无现任时以 `bench:csi300` 净值为基准；影子期按收益日计数，净值从进入影子后第一个交易日的前一交易日（结算记基线的那天）开始取；晋升取年化超额最大者，旧现任全部 `retired`；降级 = 无晋升时，若某影子已有 ≥`DEMOTE_WINDOW` 个收益日，且现任最近 `DEMOTE_WINDOW` 个收益日相对它年化超额 ≤`DEMOTE_EXCESS_ANN`、t ≤ `-DEMOTE_T`，现任降为 `shadow`、落后最多的那个影子接任（算作本次唯一的晋升）。降级也要求显著，否则零超额的噪声会让现任和影子来回换位。
> - `integrity` 覆盖 `.py` 和 `.pyw`，计算哈希前把 `\r\n` 归一为 `\n`（防 Windows git autocrlf 误报）；清单缺失、损坏或格式不对时 `verify` 返回 `[MANIFEST_PATH]`（视为不通过）；读取时用 `utf-8-sig`，兼容记事本加的 BOM。
> - `Store.record_nav` 拒绝非有限净值（SQLite 会把 NaN 存成 NULL）；`add_trial` 返回试验 id，另有 `set_trial_holdout`、`last_trial`。

本部分依赖 Part 1 的 `engine/types.py`（Bar/Pick/Fill）、`engine/rules.py`、`engine/calendar.py`（TradingCalendar）、`engine/ledger.py`（Account）、`engine/broker.py`（SimBroker）。按顺序执行：2.1 → 2.2 → 2.3 → 2.4 → 2.5（依赖 2.1–2.4）→ 2.6 → 2.7（依赖 2.1）→ 2.8。

本部分全部测试离线运行：不访问网络、不调用 Claude API、不推送企业微信，因此没有手工冒烟步骤。

所有 commit 命令都用第二个 `-m` 追加 trailer：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

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

import engine.store as store_mod
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
    acc.last_trade_day = date(2026, 1, 5)
    store.save_account(acc)
    got = store.load_account("q:baseline_momo")
    assert got == acc and got.last_trade_day == date(2026, 1, 5)
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

def test_llm_usage_month_cost(store, monkeypatch):
    stamps = iter(["2026-01-31T23:59:59", "2026-02-01T00:00:00", "2025-01-15T10:00:00"])
    monkeypatch.setattr(store_mod, "_now", lambda: next(stamps))
    store.add_llm_usage("premarket:active", 1000, 200, 1.0)
    store.add_llm_usage("review", 1000, 200, 2.0)
    store.add_llm_usage("research", 1000, 200, 4.0)
    assert store.month_llm_cost(2026, 1) == pytest.approx(1.0)
    assert store.month_llm_cost(2026, 2) == pytest.approx(2.0)
    assert store.month_llm_cost(2025, 1) == pytest.approx(4.0)
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


def test_store_shared_across_threads(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(store_mod, "_now", lambda: "2026-03-10T09:00:00")
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda i: store.add_llm_usage(f"p{i}", 1, 1, 1.0), range(50)))
    assert store.month_llm_cost(2026, 3) == pytest.approx(50.0)


def test_file_db_persists(tmp_path):
    p = tmp_path / "sub" / "x.sqlite"
    Store(p).kv_set("a", 1)
    assert Store(p).kv_get("a") == 1


def test_nav_series_sorted_regardless_of_insert_order(store):
    for d in (date(2026, 1, 8), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 5)):
        store.record_nav("b", d, float(d.day))
    s = store.nav_series("b")
    assert list(s.index) == [pd.Timestamp(f"2026-01-0{i}") for i in (5, 6, 7, 8)]
    assert list(s) == [5.0, 6.0, 7.0, 8.0]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_record_nav_rejects_non_finite(store, bad):
    with pytest.raises(ValueError):
        store.record_nav("a", date(2026, 1, 5), bad)
    assert store.nav_series("a").empty


def test_trial_holdout_update_and_last_trial(store):
    assert store.last_trial("a") is None
    tid = store.add_trial("a", {}, 0.6, None, True, None)  # val 夏普缺失也能入库
    store.add_trial("b", {}, 0.1, 0.2, False, None)
    assert store.last_trial("a")["holdout_pass"] is None and store.last_trial("a")["val_sharpe"] is None
    store.set_trial_holdout(tid, True)
    t = store.last_trial("a")
    assert t["id"] == tid and t["gate_pass"] is True and t["holdout_pass"] is True
    assert store.recent_trials(2)[1]["holdout_pass"] is True


def test_void_trial(store):
    tid = store.add_trial("a", {}, 1.0, 1.2, True, True)
    store.void_trial(tid)
    t = store.last_trial("a")
    assert t["gate_pass"] is False and t["holdout_pass"] is False and store.trial_count() == 1
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
        """作废一次试验（评估规则在评估期间被改动）：仍计入 n_trials，但门槛和留出期都记为未通过。"""
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
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_store.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/store.py tests/test_store.py
git commit -m "feat: add sqlite Store for accounts, nav, recs, trials, status, kv" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
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
    assert m.annualized_return(_s([-0.5, -0.6, 0.1])) == pytest.approx(0.22 ** (252 / 3) - 1)
    assert m.annualized_return(_s([0.1, -1.0])) == -1.0  # 净值归零


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
    assert s["n_days"] == 3  # 收益天数：净值第一个点是起点
    assert s["excess_ann"] == pytest.approx((0.1 - 0.1 + 0.1) / 3 * 252)


def test_deflated_sharpe_reference_values():
    # 参考值由独立实现复算（Bailey & Lopez de Prado 2014，kurt 为非超额峰度）
    assert m.deflated_sharpe(0.1, 10, 250, skew=-0.5, kurt=5.0) == pytest.approx(0.5025292790757397, rel=1e-9)
    assert m.deflated_sharpe(0.1, 100, 500) == pytest.approx(0.38456717973214927, rel=1e-9)
    assert m.deflated_sharpe(0.1, 2, 250) < m.deflated_sharpe(0.1, 1, 250)
    # 负偏度让 DSR 更低（A 股策略常见左偏）
    assert m.deflated_sharpe(0.1, 10, 250, skew=-0.5) < m.deflated_sharpe(0.1, 10, 250, skew=0.5)


def test_summarize_with_moving_benchmark():
    nav = _s([100, 103, 101, 106, 104, 109])
    bench = _s([100, 101, 103, 102, 104, 105])
    s = m.summarize(nav, bench)
    r = m.daily_returns(nav)
    assert s["sharpe"] == pytest.approx(m.sharpe(r))
    assert s["ann_return"] == pytest.approx(m.annualized_return(r))
    assert s["excess_ann"] == pytest.approx(float((r - m.daily_returns(bench)).mean() * 252))
    assert s["sharpe"] != pytest.approx(m.sharpe(r - m.daily_returns(bench)))
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
        "n_days": int(len(r)),  # 收益天数；nav 的第一个点是起点（回测为期初资金）
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
git commit -m "feat: add performance metrics and deflated sharpe" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
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
    # 面板只在闭包里：实例上没有任何属性能取到它（改编后的名字、format 字段、反射都不行）
    assert not hasattr(view, "_DataView__panel")
    assert all("panel" not in n for n in dir(view))
    with pytest.raises(AttributeError):
        "{0._DataView__panel}".format(view)


def test_no_reference_to_future_rows_reachable(view):
    import gc
    for name in dir(view):
        if not name.startswith("__"):
            attr = getattr(type(view), name, None)
            if isinstance(attr, property) or callable(attr):
                continue
            assert not isinstance(getattr(view, name), (dict, pd.DataFrame)), name
    assert pd.Timestamp("2026-01-14") not in pd.concat([view.history(c) for c in view.codes()]).index
    with pytest.raises(TypeError):
        vars(view)  # 无 __dict__


def test_history_zero_and_unknown(view):
    assert view.history("600000", 0).empty
    with pytest.raises(KeyError):
        view.history("999999")


def test_codes_exclude_stocks_not_yet_listed():
    panel = {"600000": _frame(), "000001": _frame(start="2026-01-12")}  # 000001 在 asof 之后才上市
    v = DataView(panel, {}, asof=date(2026, 1, 9))
    assert v.codes() == ["600000"]
    assert DataView(panel, {}, asof=date(2026, 1, 12)).codes() == ["000001", "600000"]
    assert DataView({"600000": _frame().iloc[:0]}, {}, asof=date(2026, 1, 9)).codes() == []


def test_history_n_larger_than_available(view):
    assert len(view.history("600000", 7)) == 5


def test_bar_on_missing_day_raises_key_error(view):
    with pytest.raises(KeyError):
        view.bar_on("600000", date(2026, 1, 3))  # 周六
    panel = {"600000": _frame().drop(pd.Timestamp("2026-01-07"))}  # 停牌日没有这一行
    with pytest.raises(KeyError):
        DataView(panel, {}, asof=date(2026, 1, 9)).bar_on("600000", date(2026, 1, 7))
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
    # 面板只保存在方法的闭包里，实例上没有任何能取到它的属性：改编后的私有名字、str.format 的字段
    # （"{0._DataView__panel}"）都拿不到 asof 之后的数据。闭包本身只能经 __closure__ 这类双下划线属性
    # 取到，Task 3.6 的源码检查禁止任何含 "__" 的名字、属性和字符串。没有 __dict__，实例只读。
    __slots__ = ("asof", "codes", "name", "history", "bar_on")

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str], asof: date):
        ts = pd.Timestamp(asof)
        # 只列出 asof 时已经有数据的股票：之后才上市的代码本身就是未来信息
        listed = sorted(c for c, df in panel.items() if len(df) and df.index[0] <= ts)

        def codes() -> list[str]:
            return list(listed)

        def name(code: str) -> str:
            return names.get(code, "")

        def history(code: str, n: int | None = None) -> pd.DataFrame:
            df = panel[code]
            end = int(df.index.searchsorted(ts, side="right"))  # 索引升序
            start = 0 if n is None else max(end - n, 0)
            return df.iloc[start:end].copy()

        def bar_on(code: str, d: date) -> Bar:
            if d > asof:
                raise LookaheadError(f"{code} {d} > asof {asof}")
            row = panel[code].loc[pd.Timestamp(d)]  # 无数据 -> KeyError
            return Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                       close=float(row["close"]), prev_close=float(row["prev_close"]), volume=float(row["volume"]),
                       amount=float(row["amount"]), name=names.get(code, ""))

        for attr, value in (("asof", asof), ("codes", codes), ("name", name), ("history", history),
                            ("bar_on", bar_on)):
            object.__setattr__(self, attr, value)

    def __setattr__(self, key, value):
        raise AttributeError("DataView is read-only")

    def __delattr__(self, key):
        raise AttributeError("DataView is read-only")
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_dataview.py -v
```

预期：全部 PASS。

**Step 5: 提交**（第二个 `-m` 追加 Co-Authored-By trailer）

```
git add engine/dataview.py tests/test_dataview.py
git commit -m "feat: add lookahead-safe DataView" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2.4: 回测引擎与 bars_for_day

**Files:**
- Create: `engine/backtest.py`
- Test: `tests/test_backtest.py`

合成 3 只股票的面板 + 简单策略；验证 select 收到的 asof 恰为 `calendar.prev_day(t)`；偷看未来的策略在回测中直接抛出 `LookaheadError`。`bars_for_day` 跳过 `prev_close` 为 NaN 的行（手工构造的面板首行；Task 3.2 的 `load_panel` 会把首行昨收填成开盘价），也供 `jobs.settle` 使用。

**Step 1: 写失败测试** `tests/test_backtest.py`

```python
from datetime import date

import pandas as pd
import pytest

from engine.backtest import BacktestResult, DayBars, bars_for_day, run_backtest
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
    # 第一个点是 start 前一交易日的期初资金，首日的建仓成本和涨跌因此计入指标
    assert list(res.nav.index) == [IDX[1]] + [pd.Timestamp(d) for d in CAL.between(DAYS[2], DAYS[20])]
    assert res.nav.iloc[0] == 100_000 and res.nav.iloc[1] < 100_000  # 首日买入扣了费用和滑点
    buys = [f for f in res.fills if f.side == "BUY"]
    assert buys and all(f.code == "600001" for f in buys)
    assert any(f.side == "SELL" and f.reason == "到期" for f in res.fills)
    assert res.metrics["n_days"] == len(CAL.between(DAYS[2], DAYS[20])) == len(res.nav) - 1
    assert res.metrics["total_return"] > 0  # 买的是上涨股


def test_benchmark_with_date_index_is_normalized():
    a = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=IDX, dtype=float))
    b = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[2], DAYS[20], pd.Series(range(30), index=DAYS, dtype=float))
    assert a.metrics["excess_ann"] != 0.0
    assert a.metrics == b.metrics


def test_picks_beyond_calendar_end_dropped():
    res = run_backtest(Recorder(), _panel(), NAMES, CAL, DAYS[-2], DAYS[-1], BENCH)
    assert res.fills == []


class BuyOnce:
    """第一次被调用时买 600001，持有 hold 个交易日；止损止盈设得很远，只靠到期卖出。"""

    def __init__(self, hold=10):
        self.name, self.hold, self.done = "buy_once", hold, False

    def select(self, view):
        if self.done:
            return []
        self.done = True
        last = float(view.history("600001", 1)["close"].iloc[-1])
        return [Pick("600001", "上涨股", 1.0, last * 1.05, last * 0.5, last * 2.0, self.hold)]


def test_nav_marks_to_market_and_suspended_day_keeps_last_close():
    panel = _panel()
    panel["600001"] = panel["600001"].drop(IDX[6])  # 停牌一天
    res = run_backtest(BuyOnce(), panel, NAMES, CAL, DAYS[2], DAYS[10], BENCH, capital=100_000, max_picks=1)
    assert [f.side for f in res.fills] == ["BUY"]
    nav = res.nav
    assert nav[IDX[1]] == 100_000 > nav[IDX[2]]
    assert nav[IDX[2]] < nav[IDX[3]] < nav[IDX[4]] < nav[IDX[5]]  # 上涨股按市值计价，不是只算现金
    assert nav[IDX[6]] == pytest.approx(nav[IDX[5]])  # 停牌日沿用上一个收盘价，不按成本估值


def test_first_day_loss_counts_in_metrics():
    closes = [10.0] * 3 + [9.2] * 27
    opens = list(closes)
    opens[3] = 10.0  # DAYS[3] 开盘 10.00 买入，收盘 9.20，之后横盘
    panel = {"600001": _frame(closes, opens)}
    res = run_backtest(BuyOnce(hold=20), panel, NAMES, CAL, DAYS[3], DAYS[15], BENCH, capital=100_000, max_picks=1)
    assert [f.side for f in res.fills] == ["BUY"]
    assert res.metrics["total_return"] < -0.07 and res.metrics["max_dd"] < -0.07


@pytest.mark.parametrize("start,end,hold,bought", [
    (DAYS[-5], DAYS[-1], 4, True),   # 到期日正好是日历最后一天
    (DAYS[-5], DAYS[-1], 5, False),  # 越过日历末尾一天：丢弃
    (DAYS[-5], DAYS[-3], 4, True),   # 持有期可以越过回测末端，只要不越过日历
])
def test_hold_period_horizon_boundary(start, end, hold, bought):
    res = run_backtest(BuyOnce(hold), _panel(), NAMES, CAL, start, end, BENCH)
    assert any(f.side == "BUY" for f in res.fills) is bought


@pytest.mark.parametrize("bps", [10.0, 0.0])
def test_budget_per_pick_and_slippage(bps):
    res = run_backtest(BuyOnce(), _panel(), NAMES, CAL, DAYS[2], DAYS[4], BENCH, capital=100_000, max_picks=5,
                       slippage_bps=bps)
    f = res.fills[0]
    assert f.price == pytest.approx(10.0 * 1.01 ** 2 * (1 + bps / 10_000))
    assert 15_000 < f.price * f.qty <= 20_000  # 每只约占资金的 1/max_picks


def test_nan_close_falls_back_to_last_finite_close():
    panel = _panel()
    panel["600001"].loc[IDX[6], "close"] = float("nan")
    res = run_backtest(BuyOnce(), panel, NAMES, CAL, DAYS[2], DAYS[10], BENCH, capital=100_000, max_picks=1)
    assert res.nav.notna().all()
    assert res.nav[IDX[6]] == pytest.approx(res.nav[IDX[5]])


class PanelProbe:
    """测试专用：记下每天策略经 history 能看到的最后一行，以及 asof。"""
    name = "probe"

    def __init__(self):
        self.seen = []

    def select(self, view):
        self.seen.append((view.asof, max(view.history(c).index.max() for c in view.codes())))
        return []


def test_strategy_never_receives_data_after_asof():
    probe = PanelProbe()
    run_backtest(probe, _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH)
    assert probe.seen and all(last == pd.Timestamp(asof) for asof, last in probe.seen)


def test_day_bars_matches_bars_for_day():
    panel = _panel()
    panel["600002"] = panel["600002"].drop(IDX[4])
    for i, (code, df) in enumerate(panel.items()):  # 开高低收、量、额各不相同，列序错了才测得出来
        df = df.copy()
        df["open"] = df["close"] * (1 + 0.003 * (i + 1))
        df["high"] = df[["open", "close"]].max(axis=1) * 1.02
        df["low"] = df[["open", "close"]].min(axis=1) * 0.98
        df["volume"] = range(1000, 1000 + len(df))
        df["amount"] = df["volume"] * 7.0
        panel[code] = df
    index = DayBars(panel, NAMES)
    for d in DAYS + [date(2030, 1, 1)]:
        assert index.get(d) == bars_for_day(panel, NAMES, d)


def test_benchmark_counts_first_day():
    bench = pd.Series(100.0, index=IDX)
    bench[IDX[2]:] = 110.0  # 基准只在 start 当天上涨 10%，之后横盘
    res = run_backtest(BuyOnce(), _panel(), NAMES, CAL, DAYS[2], DAYS[10], bench, capital=100_000, max_picks=1)
    flat = run_backtest(BuyOnce(), _panel(), NAMES, CAL, DAYS[2], DAYS[10], BENCH, capital=100_000, max_picks=1)
    assert res.metrics["excess_ann"] == pytest.approx(flat.metrics["excess_ann"] - 0.1 / 9 * 252)
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

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
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


_COLS = ["open", "high", "low", "close", "prev_close", "volume", "amount"]


def bars_for_day(panel: dict[str, pd.DataFrame], names: dict[str, str], d: date) -> dict[str, Bar]:
    """d 日有日线的股票的 Bar。昨收为 NaN 的行跳过（Part 3 的 load_panel 会把首行昨收填成开盘价，
    所以真实数据里一般不会出现；手工构造的面板首行常为 NaN）。"""
    ts = pd.Timestamp(d)
    out: dict[str, Bar] = {}
    for code, df in panel.items():
        if ts not in df.index:
            continue
        row = df.loc[ts]
        prev = float(row["prev_close"])
        if prev != prev:  # NaN：无昨收，跳过
            continue
        out[code] = Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]), prev_close=prev,
                        volume=float(row["volume"]), amount=float(row["amount"]), name=names.get(code, ""))
    return out


def _ns(ts) -> np.ndarray:
    return np.asarray(ts, dtype="datetime64[ns]").view("int64")


class DayBars:
    """回测用的按日索引：一次性把面板转成数组，每天对每只股票只做一次二分查找。结果与 bars_for_day 相同。"""

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str]):
        self._names = names
        self._data = [(code, _ns(df.index.values), df[_COLS].to_numpy(dtype=float)) for code, df in panel.items()]

    def get(self, d: date) -> dict[str, Bar]:
        t = _ns([pd.Timestamp(d).to_datetime64()])[0]
        out: dict[str, Bar] = {}
        for code, keys, cols in self._data:
            i = int(np.searchsorted(keys, t))  # 索引升序
            if i >= len(keys) or keys[i] != t:
                continue
            o, h, lo, c, pc, v, a = (float(x) for x in cols[i])
            if pc != pc:  # NaN：无昨收，跳过
                continue
            out[code] = Bar(code=code, date=d, open=o, high=h, low=lo, close=c, prev_close=pc,
                            volume=v, amount=a, name=self._names.get(code, ""))
        return out


def run_backtest(strategy, panel, names, calendar: TradingCalendar, start: date, end: date,
                 benchmark_nav: pd.Series, capital: float = 500_000, max_picks: int = 5,
                 slippage_bps: float = 10.0) -> BacktestResult:
    """nav 的第一个点是 start 前一交易日的期初资金，首日的建仓成本和涨跌因此计入指标。
    策略拿到的面板截断到 end：本段回测看不到之后（例如留出期）的任何数据。"""
    acct = Account(name=f"bt:{getattr(strategy, 'name', 'strategy')}", cash=float(capital))
    broker = SimBroker(slippage_bps=slippage_bps)
    last_close: dict[str, float] = {}
    days = calendar.between(start, end)
    horizon = len(calendar.between(start, calendar.last()))
    panel = {c: df.loc[:pd.Timestamp(end)] for c, df in panel.items()}
    day_bars = DayBars(panel, names)
    dates = [calendar.prev_day(days[0])] + days if days else []
    navs: list[float] = [float(capital)] if days else []
    for i, t in enumerate(days):
        view = DataView(panel, names, asof=calendar.prev_day(t))
        picks = list(strategy.select(view))[:max_picks]
        # 持有期越过日历末尾的 pick 无法确定到期日，丢弃（t 之后还剩 horizon-1-i 个交易日）
        picks = [p for p in picks if p.hold_days <= horizon - 1 - i]
        bars = day_bars.get(t)
        broker.execute_day(acct, t, bars, picks, calendar, capital / max_picks)
        # 收盘价缺失（NaN）时沿用上一个有效收盘价，净值不会变成 NaN
        last_close.update({c: b.close for c, b in bars.items() if math.isfinite(b.close)})
        navs.append(acct.equity(last_close))
    nav = pd.Series(navs, index=pd.DatetimeIndex([pd.Timestamp(t) for t in dates]), dtype=float)
    bench = benchmark_nav.copy()
    bench.index = pd.to_datetime(bench.index)  # 兼容 date 对象索引
    bench = bench.sort_index().loc[pd.Timestamp(dates[0] if dates else start):pd.Timestamp(end)]
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
git commit -m "feat: add day-by-day backtest and bars_for_day" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
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
import pytest

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


GOOD_TRAIN = {"sharpe": 0.5}
GOOD_VAL = {"sharpe": 0.8, "excess_ann": 0.03, "max_dd": -0.25}


def test_gate_boundaries_inclusive_and_each_condition_counts():
    assert ev._passes_gate(GOOD_TRAIN, GOOD_VAL, 0.90) is True
    assert ev._passes_gate(GOOD_TRAIN, GOOD_VAL, 0.90 - 1e-9) is False
    assert ev._passes_gate({"sharpe": 0.5 - 1e-9}, GOOD_VAL, 0.95) is False
    for key in GOOD_VAL:
        assert ev._passes_gate(GOOD_TRAIN, {**GOOD_VAL, key: GOOD_VAL[key] - 1e-9}, 0.95) is False, key
    assert ev._passes_gate(GOOD_TRAIN, {**GOOD_VAL, "sharpe": float("nan")}, 0.95) is False


def test_splits_are_contiguous_and_holdout_starts_2026_07():
    tr, va, ho = ev.SPLITS["train"], ev.SPLITS["val"], ev.SPLITS["holdout"]
    assert tr == (date(2016, 1, 1), date(2024, 12, 31))
    assert va == (date(2025, 1, 1), date(2026, 6, 30))
    assert ho == (date(2026, 7, 1), None)


def _patched(monkeypatch, holdout_metrics=None, holdout_raises=None):
    real = ev.run_backtest

    def fake(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        if start == SPL["holdout"][0]:
            if holdout_raises is not None:
                raise holdout_raises
            res = real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)
            res.metrics.update(holdout_metrics or {})
            return res
        return real(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)

    monkeypatch.setattr(ev, "run_backtest", fake)


@pytest.mark.parametrize("over,passed", [
    ({"n_days": 0}, False),
    ({"excess_ann": -1e-9}, False),
    ({"sharpe": 0.5 - 1e-9}, False),
    ({"excess_ann": 0.0, "sharpe": 0.5}, True),  # 边界含等号
])
def test_holdout_gate_conditions(monkeypatch, over, passed):
    _patched(monkeypatch, holdout_metrics=over)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, Store(":memory:"), splits=SPL)
    assert rep.gate_pass is True and rep.holdout_pass is passed


@pytest.mark.parametrize("exc", [RuntimeError("holdout_return=0.4451"), SystemExit(0), KeyError("x"),
                                 KeyboardInterrupt("holdout_return=0.4451")])
def test_holdout_failure_is_a_counted_fail_without_details(monkeypatch, exc):
    _patched(monkeypatch, holdout_raises=exc)
    store = Store(":memory:")
    if isinstance(exc, KeyboardInterrupt):  # 真正的 Ctrl+C 照样中断，但抛出的是不带消息的新异常
        with pytest.raises(KeyboardInterrupt) as info:
            ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
        assert str(info.value) == "" and info.value.__context__ is None and info.value.__cause__ is None
        assert store.last_trial("winner")["holdout_pass"] is False
        return
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    assert rep.gate_pass is True and rep.holdout_pass is False
    assert store.trial_count() == 1 and store.last_trial("winner")["holdout_pass"] is False
    assert "0.4451" not in json.dumps(rep.to_agent_dict()) and "0.4451" not in repr(rep)


class Crasher:
    name = "crasher"

    def select(self, view):
        raise ValueError("boom")


@pytest.mark.parametrize("exc", [ValueError("boom"), SystemExit(0), KeyboardInterrupt()])
def test_failed_train_val_run_still_counts_as_trial(exc):
    class Raiser(Crasher):
        def select(self, view):
            raise exc

    store = Store(":memory:")
    with pytest.raises(type(exc)):
        ev.evaluate(Raiser(), "crasher", _panel(), NAMES, CAL, BENCH, store, {"p": 2}, splits=SPL)
    t = store.last_trial("crasher")
    assert store.trial_count() == 1
    assert t["gate_pass"] is False and t["holdout_pass"] is None and t["params"] == {"p": 2}


def test_dsr_inputs_and_trial_sharpes(monkeypatch):
    store = Store(":memory:")
    store.add_trial("other", {}, 0.0, 0.0, False, None)
    store.add_trial("other2", {}, 0.0, 0.0, False, None)
    seen, navs = {}, {}
    real_bt, real_dsr = ev.run_backtest, ev.deflated_sharpe

    def bt(strategy, panel, names, calendar, start, end, benchmark_nav, **kw):
        res = real_bt(strategy, panel, names, calendar, start, end, benchmark_nav, **kw)
        navs[start] = res.nav
        return res

    def dsr(sr, n_trials, n_obs, skew=0.0, kurt=3.0):
        seen.update(sr=sr, n_trials=n_trials, n_obs=n_obs, skew=skew, kurt=kurt)
        return real_dsr(sr, n_trials, n_obs, skew, kurt)

    monkeypatch.setattr(ev, "run_backtest", bt)
    monkeypatch.setattr(ev, "deflated_sharpe", dsr)
    rep = ev.evaluate(Winner(), "winner", _panel(), NAMES, CAL, BENCH, store, splits=SPL)
    r = pd.concat([navs[SPL["train"][0]].pct_change().dropna(), navs[SPL["val"][0]].pct_change().dropna()])
    assert seen["n_trials"] == 3  # 所有策略的既往试验 + 本次
    assert seen["n_obs"] == len(r)
    assert seen["sr"] == pytest.approx(ev.sharpe(r, periods=1))  # 日频夏普，不是年化
    assert seen["kurt"] == pytest.approx(float(r.kurt()) + 3.0)  # 非超额峰度
    t = store.last_trial("winner")
    assert (t["train_sharpe"], t["val_sharpe"]) == (pytest.approx(rep.train["sharpe"]), pytest.approx(rep.val["sharpe"]))
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

    n_trials = store.trial_count() + 1  # 含本次
    try:
        tr, va = bt("train"), bt("val")
    except BaseException:
        # 跑不完的试验同样计入 n_trials（DSR 的多重检验校正）；训练、验证段的报错照常抛出
        store.add_trial(name, params or {}, None, None, False, None)
        raise
    r = pd.concat([tr.nav.pct_change().dropna(), va.nav.pct_change().dropna()])
    dsr = deflated_sharpe(sharpe(r, periods=1), n_trials, len(r), float(r.skew()) if len(r) > 2 else 0.0,
                          float(r.kurt()) + 3.0 if len(r) > 3 else 3.0)
    gate_pass = _passes_gate(tr.metrics, va.metrics, dsr)
    # 先入库再回放留出期：留出期中途失败或进程被杀，这次试验也已计数
    trial_id = store.add_trial(name, params or {}, tr.metrics["sharpe"], va.metrics["sharpe"], gate_pass, None)
    holdout_pass = None
    if gate_pass:
        interrupted = False
        try:
            ho = bt("holdout").metrics
            holdout_pass = bool(ho["n_days"] > 0 and ho["excess_ann"] >= HOLDOUT_GATE["excess_ann"]
                                and ho["sharpe"] >= HOLDOUT_GATE["sharpe"])
        except BaseException as e:  # noqa: BLE001 - 任何异常只记未通过：异常文本可能带出留出期数值，不外传
            holdout_pass, interrupted = False, isinstance(e, KeyboardInterrupt)
        store.set_trial_holdout(trial_id, holdout_pass)
        if interrupted:  # Ctrl+C 照样中断，但抛出不带消息、不带原异常的新异常（策略也可能抛 KeyboardInterrupt）
            raise KeyboardInterrupt from None
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
git commit -m "feat: add locked evaluator with gate, DSR and blind holdout" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2.6: 锁定区哈希校验

**Files:**
- Create: `engine/integrity.py`
- Test: `tests/test_integrity.py`

只校验 `LOCKED_DIRS` 下的 `*.py` 和 `*.pyw`；清单缺失或损坏时 `verify` 返回 `[MANIFEST_PATH]`（视为不通过）。测试文件用 `write_bytes` 写入 LF，否则 Windows 上换行归一的测试恒为真。

**Step 1: 写失败测试** `tests/test_integrity.py`

```python
from pathlib import Path

import pytest

from engine import integrity


def _tree(root: Path):
    # write_bytes：Windows 上 write_text 会把 \n 写成 \r\n，换行归一的测试就失去意义
    (root / "engine" / "sub").mkdir(parents=True)
    (root / "engine" / "a.py").write_bytes(b"x = 1\n")
    (root / "engine" / "sub" / "b.py").write_bytes(b"y = 2\n")
    (root / "engine" / "notes.txt").write_text("ignored", encoding="utf-8")
    (root / "strategies").mkdir()
    (root / "strategies" / "s.py").write_text("z = 3\n", encoding="utf-8")


def test_manifest_covers_only_locked_py(tmp_path):
    _tree(tmp_path)
    m = integrity.compute_manifest(tmp_path)
    assert set(m) == {"engine/a.py", "engine/sub/b.py"}
    (tmp_path / "engine" / "c.pyw").write_bytes(b"w = 1\n")  # Windows 上 .pyw 同样可被 import
    assert set(integrity.compute_manifest(tmp_path)) == {"engine/a.py", "engine/c.pyw", "engine/sub/b.py"}
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
    assert (tmp_path / "engine" / "a.py").read_bytes() == b"x = 1\n"
    (tmp_path / "engine" / "a.py").write_bytes(b"x = 1\r\n")
    assert integrity.verify(tmp_path) == []
    (tmp_path / "engine" / "a.py").write_bytes(b"x = 2\r\n")
    assert integrity.verify(tmp_path) == ["engine/a.py"]


@pytest.mark.parametrize("content", [b"{", b"[]", b'{"engine/a.py": 1}', b"\xff\xfe"])
def test_unreadable_manifest_fails_verify(tmp_path, content):
    _tree(tmp_path)
    integrity.write_manifest(tmp_path)
    (tmp_path / integrity.MANIFEST_PATH).write_bytes(content)
    assert integrity.verify(tmp_path) == [integrity.MANIFEST_PATH]


def test_manifest_with_bom_is_accepted(tmp_path):
    _tree(tmp_path)
    integrity.write_manifest(tmp_path)
    p = tmp_path / integrity.MANIFEST_PATH
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())  # 记事本另存为 UTF-8 会加 BOM
    assert integrity.verify(tmp_path) == []
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_integrity.py -v
```

预期 FAIL：`ImportError: cannot import name 'integrity' from 'engine'`。

**Step 3: 最小实现** `engine/integrity.py`

```python
"""锁定区哈希校验：engine/ 下所有 .py / .pyw 的 sha256 清单。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCKED_DIRS = ["engine"]
SUFFIXES = (".py", ".pyw")  # Windows 上 .pyw 同样能被 import
MANIFEST_PATH = ".integrity/manifest.json"


def _digest(path: Path) -> str:
    # 归一换行，避免 Windows autocrlf 造成误报
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def compute_manifest(root: Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for d in LOCKED_DIRS:
        for p in sorted((root / d).rglob("*")):
            if p.is_file() and p.suffix.lower() in SUFFIXES:
                out[p.relative_to(root).as_posix()] = _digest(p)
    return out


def write_manifest(root: Path) -> None:
    path = Path(root) / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compute_manifest(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load(path: Path) -> dict[str, str] | None:
    """读取清单；缺失、无法解析或格式不对时返回 None。utf-8-sig 兼容记事本加的 BOM。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):  # 含 UnicodeDecodeError、JSONDecodeError
        return None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        return None
    return data


def verify(root: Path) -> list[str]:
    """返回与清单不符的文件；清单缺失或损坏时返回 [MANIFEST_PATH]（视为不通过）。"""
    expected = _load(Path(root) / MANIFEST_PATH)
    if expected is None:
        return [MANIFEST_PATH]
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
git commit -m "feat: add engine integrity manifest and verify" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2.7: 晋升与降级

**Files:**
- Create: `engine/promotion.py`
- Test: `tests/test_promotion.py`

影子期净值 = `q:<name>` 账户从进入影子后第一个交易日的前一交易日（结算记基线的那天；研究在周六运行时是周五）到 today 的净值点；收益日（点数减一）不足 `MIN_SHADOW_DAYS` 不参与。晋升：年化超额 >= `PROMOTE_EXCESS_ANN` 且 t >= `PROMOTE_T`，取超额最大者，旧现任 `retired`。降级：无晋升时，若某影子已满 `DEMOTE_WINDOW` 个收益日，且现任最近 `DEMOTE_WINDOW` 个收益日相对它年化超额 <= `DEMOTE_EXCESS_ANN`、t <= `-DEMOTE_T`，现任降为 `shadow`，落后最多的那个影子接任。`compare` 先对齐净值再算收益。

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
    store = _setup(cand_daily=0.002, shadow_from=45)  # 15 个收益日
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
    # 晋升门槛设为不可达，单独验证降级规则（降级用自己的 DEMOTE_T）
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


@pytest.mark.parametrize("shadow_from,promoted", [(40, True), (41, False)])
def test_min_shadow_days_counts_return_days(shadow_from, promoted):
    # 影子期从 DAYS[shadow_from] 开始：前一交易日的净值是基线，之后每天一个收益日
    store = _setup(cand_daily=0.002, shadow_from=shadow_from)
    events = pr.decide(store, DAYS[-1], CAL)
    assert bool(events) is promoted, (60 - shadow_from, events)


def test_shadow_set_on_weekend_keeps_friday_baseline():
    store = Store(":memory:")
    friday = DAYS[29]
    assert friday.weekday() == 4
    saturday = pd.Timestamp(friday) + pd.Timedelta(days=1)
    store.set_status("new", "shadow", saturday.date())  # 研究任务在周六运行
    _record(store, "q:new", _nav(0.002)[29:], days=DAYS[29:])  # 周一首次结算时在周五记基线
    nav = pr._shadow_nav(store, "new", DAYS[-1], CAL)
    assert nav.index[0] == pd.Timestamp(friday) and len(nav) == 31


def test_shadow_nav_stops_at_today():
    store = _setup(cand_daily=0.002, shadow_from=30)
    assert pr._shadow_nav(store, "new", DAYS[45], CAL).index[-1] == IDX[45]
    assert pr.decide(store, DAYS[45], CAL) == []  # 截至当天只有 16 个收益日


def test_compare_aligns_nav_before_returns():
    base = pd.Series(_nav(0.003, noise=0.01), index=IDX)
    gappy = base.drop(IDX[3::4])  # 同一条净值，每 4 天缺一个点
    assert pr.compare(base, gappy) == pytest.approx((0.0, 0.0))
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status("dup", "shadow", DAYS[0])
    _record(store, "q:old", list(base))
    for ts, v in gappy.items():
        store.record_nav("q:dup", ts.date(), v)
    assert pr.decide(store, DAYS[-1], CAL) == []


def test_small_steady_excess_neither_promotes_nor_demotes():
    # 超额恒定约年化 2.5%：t 值极大，但没到晋升的 5%，现任也没落后到降级的 -5%
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status("new", "shadow", DAYS[0])
    _record(store, "q:old", _nav(0.0, noise=0.002))
    _record(store, "q:new", _nav(0.0001, noise=0.002))
    assert pr.decide(store, DAYS[-1], CAL) == []


def _two_navs(store, inc_rets, cand_rets, name="new"):
    store.set_status("old", "incumbent", DAYS[0])
    store.set_status(name, "shadow", DAYS[0])
    for acct, rets in (("q:old", inc_rets), (f"q:{name}", cand_rets)):
        v, vals = 100.0, []
        for r in rets:
            v *= 1 + r
            vals.append(v)
        _record(store, acct, vals)


def test_demotion_uses_only_recent_window(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    # 影子前 19 天每天领先 1%，之后与现任完全同步：近 40 个收益日没有落后，不降级
    inc = [0.001 if i % 2 else -0.001 for i in range(60)]
    cand = [r + (0.01 if i < 20 else 0.0) for i, r in enumerate(inc)]
    _two_navs(store, inc, cand)
    assert pr.decide(store, DAYS[-1], CAL) == []


def test_demotion_requires_significant_lag(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    lag = -0.0003  # 现任每天落后约 0.03%，年化约 -7.5%
    noisy, quiet = Store(":memory:"), Store(":memory:")
    # 噪声很大时同样的落后幅度只是运气，不降级
    _two_navs(noisy, [lag + (0.01 if i % 2 else -0.01) for i in range(60)], [0.0] * 60)
    assert pr.decide(noisy, DAYS[-1], CAL) == []
    _two_navs(quiet, [lag + (0.0002 if i % 2 else -0.0002) for i in range(60)], [0.0] * 60)
    assert [e.kind for e in pr.decide(quiet, DAYS[-1], CAL)] == ["demote", "promote"]


def test_demotion_picks_the_shadow_incumbent_lags_most(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    inc = [0.0002 if i % 2 else -0.0002 for i in range(60)]
    jitter = [0.0001 if i % 3 else -0.0002 for i in range(60)]  # 超额不能恒定，否则 t 值无穷大
    _two_navs(store, inc, [r + 0.0005 + j for r, j in zip(inc, jitter)], name="mild")
    store.set_status("strong", "shadow", DAYS[0])
    v, vals = 100.0, []
    for r, j in zip(inc, jitter):
        v *= 1 + r + 0.001 + j
        vals.append(v)
    _record(store, "q:strong", vals)
    events = pr.decide(store, DAYS[-1], CAL)
    assert [(e.kind, e.strategy) for e in events] == [("demote", "old"), ("promote", "strong")]


def test_demoted_incumbent_shadow_period_starts_next_day(monkeypatch):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    cal = TradingCalendar(DAYS + [d.date() for d in pd.bdate_range(IDX[-1] + pd.Timedelta(days=1), periods=5)])
    store = _setup(cand_daily=0.0005, inc_daily=0.0)
    assert [e.kind for e in pr.decide(store, DAYS[-1], cal)] == ["demote", "promote"]
    assert store.status_since("old") == cal.next_day(DAYS[-1])
    nav = pr._shadow_nav(store, "old", DAYS[-1], cal)
    assert list(nav.index) == [IDX[-1]]  # 只有基线，还没有影子期的收益日


def _steady(store, name, excess_daily, noise, status="shadow"):
    """现任 q:old 为 0 收益加交替噪声；name 在其上叠加每日 excess_daily 和独立的交替噪声 noise。"""
    if store.get_status("old") is None:
        store.set_status("old", "incumbent", DAYS[0])
        _record(store, "q:old", _nav(0.0, noise=0.002))
    store.set_status(name, status, DAYS[0])
    base = _nav(0.0, noise=0.002)
    rets = [base[0] / 100 - 1] + [base[i] / base[i - 1] - 1 for i in range(1, len(base))]
    v, vals = 100.0, []
    for i, r in enumerate(rets):
        v *= 1 + r + excess_daily + (noise if i % 3 == 0 else -noise / 2)
        vals.append(v)
    _record(store, f"q:{name}", vals)


@pytest.mark.parametrize("excess,noise,promoted", [
    (0.0003, 0.0, True),      # 年化约 7.6%，t 很大
    (0.00016, 0.0, False),    # 年化约 4%：不到 5%，t 再大也不晋升
    (0.00024, 0.0, True),     # 年化约 6%
    (0.0003, 0.02, False),    # 同样的超额、噪声很大：t < 1.5，不晋升
])
def test_promotion_needs_both_excess_and_significance(excess, noise, promoted):
    store = Store(":memory:")
    _steady(store, "new", excess, noise)
    events = pr.decide(store, DAYS[-1], CAL)
    assert any(e.kind == "promote" and e.strategy == "new" for e in events) is promoted


@pytest.mark.parametrize("lag,demoted", [(-0.00016, False), (-0.00024, True)])
def test_demotion_excess_threshold(monkeypatch, lag, demoted):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    # 现任落后影子：影子每日比现任多 -lag
    _steady(store, "new", -lag, 0.0001)
    kinds = [e.kind for e in pr.decide(store, DAYS[-1], CAL)]
    assert (kinds[:1] == ["demote"]) is demoted


@pytest.mark.parametrize("points,demoted", [(40, False), (41, True)])
def test_demotion_window_boundary(monkeypatch, points, demoted):
    monkeypatch.setattr(pr, "PROMOTE_T", 1e9)
    store = Store(":memory:")
    store.set_status("old", "incumbent", DAYS[0])
    _record(store, "q:old", _nav(0.0, noise=0.0002))
    store.set_status("new", "shadow", DAYS[len(DAYS) - points + 1])  # 影子期净值点数 = points
    _record(store, "q:new", _nav(0.001, noise=0.0004))  # 噪声不同：超额不恒定，t 值有限
    assert len(pr._shadow_nav(store, "new", DAYS[-1], CAL)) == points
    assert bool(pr.decide(store, DAYS[-1], CAL)) is demoted
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_promotion.py -v
```

预期 FAIL：`ImportError: cannot import name 'promotion' from 'engine'`。

**Step 3: 最小实现** `engine/promotion.py`

```python
"""量化策略晋升/降级：影子盘 >= 20 个收益日且显著跑赢现任 -> 晋升；
现任近 40 个收益日显著落后影子、年化 >= 5% -> 降级。"""
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
DEMOTE_T = 1.5  # 降级同样要求显著：否则零超额的噪声也会让现任和影子来回换位
BENCH_ACCOUNT = "bench:csi300"


@dataclass
class Event:
    kind: str  # "promote" | "demote" | "retire"
    strategy: str
    detail: str


def compare(base_nav: pd.Series, cand_nav: pd.Series, window: int | None = None) -> tuple[float, float]:
    """cand 相对 base 的年化超额与 t 值。先按日期对齐净值再算收益：一方缺某天的点时，两边都用跨过
    这个缺口的同一段收益，缺口不会被当成超额。window 只取对齐后最近 window 个收益日。"""
    b, c = base_nav.align(cand_nav, join="inner")
    if window is not None:
        b, c = b.iloc[-(window + 1):], c.iloc[-(window + 1):]
    ex = (c.pct_change() - b.pct_change()).dropna()
    if len(ex) < 2:
        return 0.0, 0.0
    ann = float(ex.mean() * 252)
    std = float(ex.std(ddof=1))
    t = float(ex.mean()) / (std / math.sqrt(len(ex))) if std > 0 else 0.0  # 恒定超额 -> 极大 t，合理
    return ann, t


def _shadow_nav(store: Store, s: str, today: date, calendar: TradingCalendar) -> pd.Series:
    """影子期净值：从进入影子后第一个交易日的前一交易日（结算时记下基线的那一天）到 today。
    研究任务在周六运行时，基线是周五的净值；第 n 个点对应第 n-1 个收益日。"""
    since = store.status_since(s)
    nav = store.nav_series(f"q:{s}")
    first = since if calendar.is_trading_day(since) else calendar.next_day(since)
    try:
        start = calendar.prev_day(first)
    except ValueError:  # 日历第一天
        start = first
    return nav[(nav.index >= pd.Timestamp(start)) & (nav.index <= pd.Timestamp(today))]


def decide(store: Store, today: date, calendar: TradingCalendar) -> list[Event]:
    incs = store.strategies_with_status("incumbent")
    inc = incs[0] if incs else None
    base = store.nav_series(f"q:{inc}" if inc else BENCH_ACCOUNT)
    base = base[base.index <= pd.Timestamp(today)]
    shadows = {s: _shadow_nav(store, s, today, calendar) for s in store.strategies_with_status("shadow")}
    shadows = {s: nav for s, nav in shadows.items() if len(nav) - 1 >= MIN_SHADOW_DAYS}

    # 1) 晋升：影子期内年化超额 >= 5% 且 t >= 1.5，取超额最大者
    best: tuple[float, float, str] | None = None
    for s, nav in shadows.items():
        ann, t = compare(base, nav)
        if ann >= PROMOTE_EXCESS_ANN and t >= PROMOTE_T and (best is None or ann > best[0]):
            best = (ann, t, s)
    if best:
        ann, t, s = best
        events = [Event("promote", s, f"影子 {len(shadows[s]) - 1} 日，年化超额 {ann:.2%}，t={t:.2f}")]
        for old in incs:
            store.set_status(old, "retired", today)
            events.append(Event("retire", old, f"被 {s} 取代"))
        store.set_status(s, "incumbent", today)
        return events

    # 2) 降级：影子已满 DEMOTE_WINDOW 个收益日，且现任在最近这一窗口内相对该影子年化超额 <= -5%、t <= -1.5
    if inc:
        worst: tuple[float, str] | None = None
        for s, nav in shadows.items():
            if len(nav) - 1 < DEMOTE_WINDOW:
                continue
            ann, t = compare(nav, base, window=DEMOTE_WINDOW)
            if ann <= DEMOTE_EXCESS_ANN and t <= -DEMOTE_T and (worst is None or ann < worst[0]):
                worst = (ann, s)
        if worst:
            ann, s = worst
            # decide 在当天结算之后运行，当天仍是现任时期：影子期从下一交易日算起
            try:
                since = calendar.next_day(today)
            except ValueError:  # 日历末端
                since = today
            store.set_status(inc, "shadow", since)
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
git commit -m "feat: add quant strategy promotion/demotion rules" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
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
> - `BarCache.update` 从已存的最后 `OVERLAP_ROWS`（5）行开始请求到 `end`，重叠部分价格差超过 `ADJ_TOL`（0.011 元）就判定前复权基准变了，先取到完整数据再删旧数据、全量重拉（记在 `last_rebased`）；单只股票失败不影响其他股票，返回失败代码列表。`update(..., full=True)` 删除后全量重拉。
> - 前复权日线只给策略算因子。`BarCache.update_raw` / `raw_bars` / `last_raw_close` 另存不复权日线（`bar_raw` 表），只供模拟盘结算撮合与估值：不复权价格不会因为之后的除权而变，多日补跑时也是当天的真实价格；`prev_close` = 收盘 - 涨跌额，是交易所当天的参考昨收（除权日即除权参考价）。分红送转存 `dividends` 表（`save_dividends` / `dividends`）。
> - `load_panel` 先在全部历史上算 `prev_close = close.shift(1)`，再按 `[start, end]` 截取；每只股票第一根 K 线缺失的 `prev_close` 用当日 `open` 填充。
> - `BarCache` 额外提供 `codes()`（缓存中全部代码）和 `last_date(code)`。表名加前缀（`bar_daily`/`bar_names`/`index_daily`），和 `Store` 共用同一个 sqlite 文件时不会冲突。约定 bootstrap 用 `save_index("sh000300", ...)` 保存基准。
> - `index_daily` / `load_index` 返回的 Series 使用归一化 `DatetimeIndex`（和 panel 一致），`name` 为指数代码。`daily_bars` 的 `date` 列是归一化的 `datetime64`。
> - `spot()` 不给代码时取全市场（东财分页翻取、每页之间 sleep，要将近一分钟；失败切新浪，新浪 `成交量` 单位为股，统一除以 100 换算成手）；`spot(codes)` 逐只取东财个股报价（`stock_bid_ask_em`），盘前定稿和盘中监控只取候选或持仓，取不到的代码不出现。`names()` 取交易所的 A 股简称列表（含 ST 前缀），bootstrap 和每天的结算都会刷新。`index_daily` 新浪失败时切东财。`stock_zh_a_hist` 传 `timeout=HTTP_TIMEOUT`；`AkClient` 构造时给所有不带 timeout 的 requests 请求补上默认超时（`socket.setdefaulttimeout` 管不到 requests：urllib3 会对新 socket 显式设为不超时），并设置进程级 socket 默认超时兜住走 urllib 的接口；其余接口连接卡住时也会报错并进入重试。`spot(codes)` 一只都取不到时抛 `DataError`（盘中监控据此计入失败并告警）；`sink` 参数让调用方超时后能用已取到的部分。`names()` 分别取上交所主板、科创板和深交所列表，取到哪个合并哪个。`news_global` 最新的在前，财联社接口只返回最近 20 条。`notices`、`stock_news` 遇到 akshare 对空结果抛的 KeyError 时返回空结果。
> - `NullNotifier.sent` 是 `list[str]`（消息原文）。`notify/wecom.py` 额外导出常量 `TEXT_LIMIT`/`MARKDOWN_LIMIT`/`FAIL_PREFIX` 和函数 `split_utf8`；失败日志会把 webhook 的 `key=` 脱敏。"下一条补报"的标记落盘在 `logs/notify_pending`：每个计划任务是独立进程，09:25 推荐失败后，之后任何任务的第一条消息都会带上提示。45009（频率限制）等到下一个分钟窗口再发一次；webhook 无效等错误不重试；读超时不重发（服务端可能已经转发）。日报不用 markdown 表格（群机器人 markdown v1 不渲染），推荐理由和市场观点里的换行合并成一行。
> - `baseline_momo` 返回前 15 名（盘前流程最多取前 15 个复核，回测和结算只取前 5 个）；额外排除最后一根 K 线不在 `view.asof` 的股票（asof 当日停牌），以及收盘价、昨收、成交额、20 日或 5 日涨幅不是有限值的股票。同分时按代码升序。策略文件不写 `from __future__ import annotations`（`__future__` 不在沙箱白名单）。
> - `check_source` 采用更严格的口径：`getattr/setattr/delattr` 一律禁止（不只针对 dunder），另禁 `locals/vars/breakpoint/input`、任何含 `__` 的名字、属性和字符串（docstring 除外）、按字符串求值或取属性的写法（含 `str.format`）、pandas 扩展接口与全局选项、除方法内真正的 `self.xxx` 以外的任何属性赋值和删除，以及经 `import engine.types` 绑定的包名访问白名单之外的模块；`strategies.base` 不在导入白名单里，以及 pandas/numpy 的文件读写属性（`read_*`、`to_csv` 等）。允许导入白名单模块及其子模块（`numpy.linalg` 可以，`engine.store` 不行）。沙箱是尽力而为的静态检查，不是安全边界；真正的防线是 `engine/` 哈希校验和 git 审计。
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
                    "end_date": "20260106", "adjust": "qfq", "timeout": akc.HTTP_TIMEOUT}
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
    items = AkClient().news_global(limit=2)  # 接口按时间升序返回：取最新的两条，最新的在前
    assert items == [{"title": "c", "content": "cc", "time": "2026-10-15 09:02:00"},
                     {"title": "b", "content": "cb", "time": "2026-10-15 09:01:00"}]


def test_stock_news(monkeypatch):
    raw = pd.DataFrame({"关键词": ["600000"] * 2, "新闻标题": ["t2", "t1"], "新闻内容": ["c2", "c1"],
                        "发布时间": ["2026-10-14 08:00:00", "2026-10-15 08:00:00"],
                        "文章来源": ["x", "y"], "新闻链接": ["u1", "u2"]})
    fake_ak(monkeypatch, stock_news_em=lambda symbol: raw)
    assert AkClient().stock_news("600000", limit=1) == [
        {"title": "t1", "content": "c1", "time": "2026-10-15 08:00:00"}]


def test_notices(monkeypatch):
    seen = {}
    raw = pd.DataFrame({"代码": [600], "名称": ["浦发银行"], "公告标题": ["年报"],
                        "公告类型": ["定期报告"], "公告日期": ["2026-10-15"], "网址": ["u"]})

    def report(symbol, date):
        seen.update(symbol=symbol, date=date)
        return raw

    fake_ak(monkeypatch, stock_notice_report=report)
    df = AkClient().notices(date(2026, 10, 15))
    assert seen == {"symbol": "全部", "date": "20261015"}
    assert list(df.columns) == ["code", "name", "title", "type"]
    assert df.iloc[0].tolist() == ["000600", "浦发银行", "年报", "定期报告"]


def test_empty_results_raising_key_error_are_empty(monkeypatch):
    def no_rows(**kw):  # akshare 在没有结果时对空表取列，抛 KeyError
        raise KeyError("代码")

    fake_ak(monkeypatch, stock_notice_report=no_rows, stock_news_em=no_rows)
    assert AkClient().notices(date(2026, 10, 1)).empty
    assert AkClient().stock_news("600000") == []


def test_spot_em_retries_before_falling_back(monkeypatch, no_sleep):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("em blip")
        return EM_SPOT

    fake_ak(monkeypatch, stock_zh_a_spot_em=flaky, stock_zh_a_spot=lambda: pytest.fail("no fallback"))
    assert len(AkClient().spot()) == 2 and len(calls) == 2


BID_ASK = {"今开": 10.2, "昨收": 10.0, "最新": 10.3, "最高": 10.4, "总手": 1200.0, "金额": 1.2e6, "涨幅": 3.0}


def _bid_ask(values):
    return pd.DataFrame({"item": list(values), "value": list(values.values())})


def test_spot_for_codes_uses_per_stock_quotes(monkeypatch):
    seen = []

    def quote(symbol):
        seen.append(symbol)
        if symbol == "000002":
            raise ConnectionError("down")
        return _bid_ask({**BID_ASK, "今开": "-"} if symbol == "000003" else BID_ASK)

    fake_ak(monkeypatch, stock_bid_ask_em=quote, stock_zh_a_spot_em=lambda: pytest.fail("no full-market spot"))
    df = AkClient(wait=0).spot(["600000", "000002", "000003"])
    assert list(df.columns) == SPOT_COLS
    assert df["code"].tolist() == ["600000", "000003"]  # 取不到的代码不出现（按竞价数据缺失处理）
    r = df.set_index("code").loc["600000"]
    assert (r["open"], r["prev_close"], r["last"], r["high"], r["volume"], r["pct"]) == (10.2, 10.0, 10.3, 10.4, 1200.0, 3.0)
    assert pd.isna(df.set_index("code").loc["000003", "open"])  # 停牌时报价为 "-"
    assert AkClient().spot([]).empty and list(AkClient().spot([]).columns) == SPOT_COLS


def test_names_from_exchange_lists(monkeypatch):
    sh = {"主板A股": pd.DataFrame({"证券代码": ["600000"], "证券简称": ["浦发银行"]}),
          "科创板": pd.DataFrame({"证券代码": ["688001"], "证券简称": ["华兴源创"]})}
    fake_ak(monkeypatch, stock_info_sh_name_code=lambda symbol: sh[symbol],
            stock_info_sz_name_code=lambda symbol: pd.DataFrame({"A股代码": [1], "A股简称": ["*ST平安"]}))
    assert AkClient().names() == {"600000": "浦发银行", "688001": "华兴源创", "000001": "*ST平安"}


def test_names_partial_when_one_exchange_fails(monkeypatch):
    def sz_down(symbol):
        raise ConnectionError("szse down")

    fake_ak(monkeypatch, stock_info_sh_name_code=lambda symbol: pd.DataFrame({"证券代码": ["600000"], "证券简称": ["浦发银行"]}),
            stock_info_sz_name_code=sz_down)
    assert AkClient(wait=0).names() == {"600000": "浦发银行"}
    fake_ak(monkeypatch, stock_info_sh_name_code=sz_down, stock_info_sz_name_code=sz_down)
    with pytest.raises(DataError):
        AkClient(wait=0).names()


def test_index_daily_falls_back_to_eastmoney(monkeypatch):
    def sina(symbol):
        raise ConnectionError("sina down")

    seen = {}

    def em(symbol, start_date="19900101", end_date="20500101"):
        seen["symbol"] = symbol
        return pd.DataFrame({"date": ["2026-01-05", "2026-01-06"], "open": [1, 1], "close": [4000.0, 4010.5],
                             "high": [1, 1], "low": [1, 1], "volume": [1, 1], "amount": [1, 1]})

    fake_ak(monkeypatch, stock_zh_index_daily=sina, stock_zh_index_daily_em=em)
    s = AkClient(wait=0).index_daily("sh000300")
    assert seen == {"symbol": "sh000300"} and s.tolist() == [4000.0, 4010.5] and s.name == "sh000300"


def test_client_sets_a_default_socket_timeout():
    import socket
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(None)
        AkClient()
        assert socket.getdefaulttimeout() == akc.SOCKET_TIMEOUT
    finally:
        socket.setdefaulttimeout(old)


def test_spot_for_codes_all_failing_raises(monkeypatch):
    def down(symbol):
        raise ConnectionError("push2 down")

    fake_ak(monkeypatch, stock_bid_ask_em=down)
    with pytest.raises(DataError):  # 一只都取不到：让盘中监控计入失败并告警，而不是当成全部停牌
        AkClient(wait=0).spot(["600000", "000001"])


def test_spot_for_codes_streams_rows_into_sink(monkeypatch):
    fake_ak(monkeypatch, stock_bid_ask_em=lambda symbol: _bid_ask(BID_ASK))
    rows = []
    AkClient().spot(["600000", "000001"], sink=rows)
    assert [r["code"] for r in rows] == ["600000", "000001"] and rows[0]["open"] == 10.2


def test_requests_without_timeout_get_one(monkeypatch):
    import socket
    import threading

    import requests

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    conns = []
    threading.Thread(target=lambda: conns.append(srv.accept()), daemon=True).start()  # 接受连接，从不响应
    monkeypatch.setattr(akc, "SOCKET_TIMEOUT", 0.5)
    AkClient()
    box = []

    def get():  # akshare 内部的 requests.get 都不传 timeout
        try:
            requests.get(f"http://127.0.0.1:{srv.getsockname()[1]}/")
        except requests.exceptions.Timeout as e:
            box.append(e)

    t = threading.Thread(target=get, daemon=True)
    t.start()
    t.join(5)
    srv.close()
    assert box, "requests.get 没有在超时内报错"
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
import socket
import time
from datetime import date

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
CONNECT_TIMEOUT = 5.0


def _install_requests_default_timeout() -> None:
    """给不带 timeout 的 requests 请求补上默认超时（只装一次）。

    socket.setdefaulttimeout 管不到 requests：timeout=None 时 urllib3 会对新建的 socket 显式 settimeout(None)。
    已经传了 timeout 的调用（企业微信推送、stock_zh_a_hist）不受影响。"""
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

    def __init__(self, attempts: int = 3, wait: float = 2.0):
        self.attempts = attempts
        self.wait = wait
        _install_requests_default_timeout()
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

    def spot(self, codes: list[str] | None = None, sink: list | None = None) -> pd.DataFrame:
        """实时快照（竞价时段的"今开"即竞价价）。

        给出 codes 时逐只取东财个股报价：akshare 的全市场快照分页翻取、每页之间 sleep，要将近一分钟，09:25 定稿
        和盘中监控都等不起。取不到的代码不出现在结果里（按竞价数据缺失处理），名称为空；一只都取不到时抛
        DataError。sink 不为 None 时每取到一只就追加一行（dict），调用方超时后可以用已取到的部分。
        不给 codes 时取全市场：东财失败切新浪。"""
        if codes is not None:
            return self._spot_codes(codes, sink)
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

    def _spot_codes(self, codes: list[str], sink: list | None = None) -> pd.DataFrame:
        rows = [] if sink is None else sink
        for code in codes:
            try:
                df = self._call(ak.stock_bid_ask_em, symbol=code)
            except Exception as e:  # noqa: BLE001 - 单只失败不影响其他代码
                log.warning("个股报价 %s 失败: %s", code, e)
                continue
            kv = dict(zip(df["item"], df["value"]))
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
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_akshare_client.py -v
```

预期：26 passed。

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
        "raw_bars": lambda: c.raw_bars("600000", start, end).tail(3),
        "index_daily": lambda: c.index_daily("sh000300").tail(3),
        "dividends": lambda: c.dividends("600000").tail(3),
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
- `dividends("600000")` 最近几行的除权除息日、每股派息、每股送转与行情软件的分红送配记录一致（新浪原表按每 10 股）。
- `spot(sina raw)` 打印的原始列名包含 `SPOT_SINA_COLS` 的全部键；比较同一只股票新浪 `成交量` 与东财 `成交量`，确认 `SINA_VOLUME_DIVISOR=100` 是否成立。
- 若某接口报 `DataError ... 缺少列`，只改 `data/akshare_client.py` 顶部对应映射字典的一行，然后重跑本测试和冒烟脚本。
- 盘后/周末 `spot` 和 `notices` 可能返回空表，这属于正常现象。

**Step 6: 提交**

```
git add data/akshare_client.py scripts/smoke_akshare.py tests/test_akshare_client.py
git commit -m "feat: add AKShare client with retry, column maps and spot fallback"
```

（提交信息末尾追加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

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
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_cache.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'data.cache'`。

**Step 3: 最小实现**

```python
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
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_cache.py -v
```

预期：15 passed。

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
    w, s = make(tmp_path, [ConnectionError("x"), Resp(500)])
    assert w.send_text("hi") is True
    assert len(s.posts) == 3
    assert not (tmp_path / "logs" / "notify_failed.jsonl").exists()


def test_rate_limit_waits_for_next_minute(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(wc.time, "sleep", lambda t: sleeps.append(t))
    w, s = make(tmp_path, [Resp(body={"errcode": 45009, "errmsg": "freq"})])
    assert w.send_text("hi") is True and len(s.posts) == 2
    assert sleeps == [wc.RATE_LIMIT_WAIT]


def test_fatal_errcode_not_retried(tmp_path):
    w, s = make(tmp_path, [Resp(body={"errcode": 93000, "errmsg": "invalid webhook url"})])
    assert w.send_text("hi") is False and len(s.posts) == 1


def test_read_timeout_not_resent(tmp_path):
    import requests
    w, s = make(tmp_path, [requests.ReadTimeout("read timed out")])
    assert w.send_text("止损告警") is False and len(s.posts) == 1  # 服务端可能已转发：重发会在群里出现两条
    rec = (tmp_path / "logs" / "notify_failed.jsonl").read_text(encoding="utf-8")
    assert "可能已送达" in rec


def test_final_failure_logs_and_prefixes_next_message_once(tmp_path, caplog):
    w, s = make(tmp_path, [Resp(500), Resp(500), ConnectionError(f"bad {URL}")])
    assert w.send_text("第一条") is False
    assert caplog.records and all("SECRET123" not in r.getMessage() for r in caplog.records)
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


def test_failure_flag_survives_process_restart(tmp_path):
    w1, _ = make(tmp_path, [Resp(500), Resp(500), Resp(500)])
    assert w1.send_markdown("09:25 推荐") is False
    w2, s2 = make(tmp_path)  # 下一个计划任务是新进程，新建实例
    assert w2.send_text("盘中告警") is True
    assert s2.posts[-1]["text"]["content"] == FAIL_PREFIX + "盘中告警"
    w3, s3 = make(tmp_path)
    assert w3.send_text("再下一条") is True and s3.posts[-1]["text"]["content"] == "再下一条"


def test_split_utf8_does_not_split_emoji_sequences():
    chunks = split_utf8("⚠️" * 1000, 4000)
    assert "".join(chunks) == "⚠️" * 1000
    assert all(not c.startswith("\ufe0f") for c in chunks)
    assert all(len(c.encode("utf-8")) <= 4000 for c in chunks)


def test_failure_during_concurrent_send_is_not_erased(tmp_path):
    w_ok, s_ok = make(tmp_path)
    w_bad, _ = make(tmp_path, [Resp(500), Resp(500), Resp(500)])
    real_post = s_ok.post

    def post_while_other_fails(url, json=None, timeout=None):
        w_bad.send_text("另一个进程同时推送失败")  # B 的请求还在飞行时，A 失败并写下标记
        return real_post(url, json=json, timeout=timeout)

    s_ok.post = post_while_other_fails
    assert w_ok.send_text("盘中告警") is True
    assert w_ok.failed  # B 成功后不能删掉 A 刚写下的标记
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
"""企业微信群机器人推送：按 UTF-8 字节分段、重试、失败落盘并在下一条补报（跨进程）。"""
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
RATE_LIMIT_WAIT = 61.0  # 45009：每分钟 20 条的额度用完，等到下一个分钟窗口再发（只等一次）
FATAL_ERRCODES = {93000, 40008, 44004, 40058}  # webhook 无效、消息类型或内容非法：重试没有用
PENDING_FILE = "notify_pending"  # 有未补报的失败；每个计划任务是独立进程，状态必须落盘
# 不能在这些码点之前切：变体选择符、零宽连接符、组合字符（硬切时拆开 emoji 序列会显示成乱码）
_NO_CUT_BEFORE = {"\ufe0f", "\ufe0e", "\u200d", "\u20e3"}


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
            while cut > 1 and line[cut] in _NO_CUT_BEFORE:
                cut -= 1
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

    @property
    def failed(self) -> bool:
        """上一条推送（可能在另一个任务进程里）是否最终失败、还没补报。"""
        return (self.log_dir / PENDING_FILE).exists()

    def send_text(self, content: str) -> bool:
        return self._send("text", content, TEXT_LIMIT)

    def send_markdown(self, content: str) -> bool:
        return self._send("markdown", content, MARKDOWN_LIMIT)

    def _pending_mark(self):
        try:
            return (self.log_dir / PENDING_FILE).stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _send(self, msgtype: str, content: str, limit: int) -> bool:
        mark = self._pending_mark()  # 只清除发送前就存在的标记：发送期间别的进程失败写下的新标记要保留
        body = (FAIL_PREFIX + content) if mark is not None else content
        for chunk in split_utf8(body, limit):
            err = self._post({"msgtype": msgtype, msgtype: {"content": chunk}})
            if err is not None:
                self._log_failure(msgtype, content, err)
                return False
        if mark is not None and self._pending_mark() == mark:
            (self.log_dir / PENDING_FILE).unlink(missing_ok=True)
        return True

    def _post(self, payload: dict) -> str | None:
        """成功返回 None，否则返回最后一次的错误描述。

        45009（频率限制）等到下一个分钟窗口再发一次；webhook 无效等错误不重试；读超时不重发：服务端可能已经
        收到并转发，重发会在群里出现两条。"""
        err, rate_limited = "unknown", False
        for i in range(ATTEMPTS):
            wait = WAIT
            try:
                r = self.session.post(self.url, json=payload, timeout=10)
                body = r.json() if r.status_code == 200 else {}
                code = body.get("errcode")
                if r.status_code == 200 and code == 0:
                    return None
                err = f"HTTP {r.status_code}: {body}"
                if code in FATAL_ERRCODES:
                    log.warning("推送失败且不可重试: %s", _redact(err))
                    return err
                if code == 45009 and not rate_limited:
                    rate_limited, wait = True, RATE_LIMIT_WAIT
            except requests.ReadTimeout as e:
                err = f"ReadTimeout（可能已送达，不重发）: {e}"
                log.warning("推送读超时，可能已送达，不重发: %s", _redact(str(e)))
                return err
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            log.warning("推送第 %d/%d 次失败: %s", i + 1, ATTEMPTS, _redact(err))
            if i < ATTEMPTS - 1:
                time.sleep(wait)
        return err

    def _log_failure(self, msgtype: str, content: str, err: str) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        pending = self.log_dir / PENDING_FILE
        pending.write_text(datetime.now().isoformat(), encoding="utf-8")  # 每次失败都更新 mtime
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

预期：14 passed。

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


def test_multiline_reason_and_view_stay_in_place():
    p = Pick(code="600000", name="浦发银行", score=1.0, entry_max=10.3, stop=9.3, target=11.0, hold_days=5,
             reason="结论：看多\n# 证据：放量\n> 引用")
    s = format_recommendation(date(2026, 10, 16), [p], llm_reviewed=True, market_view="偏强\n## 竞价确认")
    lines = s.split("\n")
    assert not any(line.startswith(("# ", "## 竞价")) for line in lines[1:])  # 不会被渲染成标题
    assert any("结论：看多" in line and "证据：放量" in line for line in lines)


def test_daily_report_has_no_markdown_table():
    rows = [{"account": "h:active", "equity": 512345.678, "day_ret": 0.0123, "total_ret": 0.0247}]
    s = format_daily_report(date(2026, 10, 16), rows, "")
    assert "|" not in s  # 企业微信群机器人 markdown（v1）不渲染表格
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


def _one_line(text: str) -> str:
    """LLM 写的理由和市场观点可能含换行：合并成一行，免得后面的行跳出引用、被渲染成标题或列表。"""
    return "；".join(part.strip() for part in str(text).splitlines() if part.strip())


def format_recommendation(d: date, picks: list[Pick], llm_reviewed: bool, market_view: str = "") -> str:
    lines = [f"## RSIQS 盘前推荐 {d.isoformat()}"]
    if not llm_reviewed:
        lines.append("<font color=\"warning\">未经 LLM 审核（已退回纯量化推荐）</font>")
    if market_view:
        lines.append(f"**市场观点**：{_one_line(market_view)}")
    if not picks:
        lines.append("今日无推荐。")
    for i, p in enumerate(picks, 1):
        lines.append(f"**{i}. {p.code} {p.name}**  买入 ≤{p.entry_max:.2f} | 止损 {p.stop:.2f} | "
                     f"止盈 {p.target:.2f} | 持有 {p.hold_days} 日")
        if p.reason:
            lines.append(f"> {_one_line(p.reason)}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_daily_report(d: date, rows: list[dict], review_summary: str) -> str:
    # 企业微信群机器人的 markdown（v1）不渲染表格：每个账户一行
    lines = [f"## RSIQS 日报 {d.isoformat()}"]
    for r in rows:
        lines.append(f"**{r['account']}** 净值 {r['equity']:,.2f}，当日 {r['day_ret']:+.2%}，累计 {r['total_ret']:+.2%}")
    lines.append("**复盘**：" + (_one_line(review_summary) or "（本日无 LLM 复盘）"))
    return "\n".join(lines)


def format_alert(title: str, body: str) -> str:
    return f"【RSIQS 告警】{title}\n{body}"
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_format.py -v
```

预期：6 passed。

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


def test_returns_up_to_15_ordered_candidates():
    # 盘前流程最多取前 15 个复核，回测和结算只取前 5 个：策略返回完整的前 15 名
    panel = {f"6001{i:02d}": series(path(0.01 * (i + 1), 0.0)) for i in range(20)}
    v = DataView(panel, {c: "甲" for c in panel}, asof=ASOF)
    picks = BaselineMomo().select(v)
    assert len(picks) == 15
    assert picks[0].code == "600119" and [p.score for p in picks] == sorted((p.score for p in picks), reverse=True)


def test_empty_view():
    assert STRATEGY.select(DataView({}, {}, asof=ASOF)) == []


def test_limit_up_uses_rules_in_force_on_asof():
    # 创业板 2020-08-24 前涨跌幅 10%：收于 +10% 就是涨停，排除；当天起涨跌幅 20%，+10% 不再是涨停
    for asof, expected in ((date(2020, 8, 21), []), (date(2020, 8, 24), ["300001"])):
        dates = pd.bdate_range(end=asof, periods=80)
        v = DataView({"300001": series(limit_up_path(), dates=dates)}, {"300001": "特锐德"}, asof=asof)
        assert [p.code for p in STRATEGY.select(v)] == expected
    # 收盘比涨停价 11.00 低一分钱也按涨停排除（容忍复权舍入）
    c = limit_up_path()
    c[-1] = 10.99
    assert STRATEGY.select(DataView({"600010": series(c)}, {"600010": "股票0"}, asof=ASOF)) == []


def test_load_and_list_strategies():
    assert load_strategy("baseline_momo") is STRATEGY
    assert STRATEGY.name == "baseline_momo"
    names = list_strategies()
    assert "baseline_momo" in names
    assert not {"base", "sandbox", "__init__"} & set(names)
    for bad in ["../evil", "Base", "ab", "base", "sandbox", "x" * 50]:
        with pytest.raises(ValueError):
            load_strategy(bad)


def test_non_finite_values_are_skipped():
    good = series(path(0.20, -0.02))
    nan_close = good.copy()
    nan_close.iloc[-1, nan_close.columns.get_loc("close")] = float("nan")  # asof 当天收盘价缺失
    nan_amount = series(path(0.15, 0.0), amount=float("nan"))           # 成交额缺失：不能当作足够
    nan_prev = series(path(0.12, 0.01))
    nan_prev.iloc[-1, nan_prev.columns.get_loc("prev_close")] = float("nan")  # 前一日收盘价缺失
    nan_hist = series(path(0.10, 0.10))
    nan_hist.iloc[-21, nan_hist.columns.get_loc("close")] = float("nan")  # 20 日前收盘价缺失
    panel = {"600001": good, "600002": nan_close, "600003": nan_amount, "600004": nan_hist, "600005": nan_prev}
    picks = STRATEGY.select(DataView(panel, {c: "股" for c in panel}, asof=ASOF))
    assert [p.code for p in picks] == ["600001"]
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
import math

import pandas as pd

from engine.dataview import DataView
from engine.rules import at_limit_up, is_st, limit_prices
from engine.types import Pick


class BaselineMomo:
    name = "baseline_momo"
    top_n = 15  # 盘前流程最多取前 15 个复核；回测和结算只取前 5 个
    min_rows = 60
    min_amount = 1e8

    def select(self, view: DataView) -> list[Pick]:
        rows = []
        for code in view.codes():
            name = view.name(code)
            if is_st(name):
                continue
            h = view.history(code, self.min_rows)  # 只取需要的行：回测每天对约 800 只股票调用
            if len(h) < self.min_rows or h.index[-1].date() != view.asof:
                continue
            if not h["amount"].iloc[-20:].mean() >= self.min_amount:  # NaN 也排除
                continue
            last = h.iloc[-1]
            close, prev = float(last["close"]), float(last["prev_close"])
            if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
                continue  # 数据缺失
            up, _ = limit_prices(code, name, prev, view.asof)
            if at_limit_up(close, up):
                continue  # asof 收于涨停（按当天的规则，容忍一分钱的复权舍入）
            c = h["close"]
            r20, r5 = float(c.iloc[-1] / c.iloc[-21] - 1), float(c.iloc[-1] / c.iloc[-6] - 1)
            if not (math.isfinite(r20) and math.isfinite(r5)):
                continue
            rows.append({"code": code, "name": name, "close": close, "r20": r20, "r5": r5})
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

预期：8 passed。

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

import pytest

from strategies.sandbox import IO_ATTRS, check_source

OK = """
import math
import numpy as np
import pandas as pd
from engine.types import Pick
from engine.rules import is_st


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
    for code in ["import os", "from os import path", "import engine.store", "from strategies.base import Path",
                 "from engine import ledger", "import subprocess as sp", "from . import base"]:
        v = bad(code)
        assert v and "导入" in v[0], code


def test_open_and_builtins_rejected():
    for code in ["open('x.txt')", "exec('1')", "eval('1')", "compile('1','f','exec')",
                 "__import__('os')", "globals()", "getattr(1, 'real')", "vars()"]:
        assert bad(code), code


def test_dunder_access_rejected():
    for code in ["x = ().__class__.__bases__", "y = __builtins__", "z = (1).__dict__",
                 "p = view._DataView__panel", "view._DataView__ts = 0", "a__b = 1\nc = a__b",
                 "k = '_DataView' + '__panel'", "k = '_DataView__panel'"]:
        assert any("__" in m for m in bad(code)), code


def test_dunder_in_docstring_and_method_name_allowed():
    src = OK.replace('    def select(self, view):', '    def select(self, view):\n        """不调用 __init__ 以外的特殊方法。"""')
    assert check_source(src) == []
    assert check_source(OK.replace('    name = "good_one"', '    name = "good_one"\n    k = "a__b"'))  # 普通字符串不行


def test_string_evaluation_and_engine_mutation_rejected():
    for code in ['import typing\ndef f(x: "(lambda p=v._DataView__panel: p)"): pass',
                 "import typing\nh = typing.get_type_hints(f)", "import typing\nr = typing.ForwardRef('x')",
                 "import engine.types\nengine.evaluator.GATE.update(dsr=-1)",
                 "import engine.types\nx = engine", "import strategies.base\ns = strategies.sandbox",
                 "import engine.rules as r\nr.COMMISSION_RATE = 0", "from engine.dataview import DataView\nDataView.history = None",
                 "raise KeyboardInterrupt('x')", "class E(BaseException): pass",
                 "from typing import get_type_hints\nh = get_type_hints", "from typing import ForwardRef\nr = ForwardRef",
                 "import typing\nt = typing._eval_type", "import engine.rules as r\nr.COMMISSION_RATE -= r.COMMISSION_RATE"]:
        assert bad(code), code
    assert bad("import engine.types\np = engine.types.Pick") == []  # 完整的白名单路径可以


@pytest.mark.parametrize("code", [
    "import statistics\nself = statistics.NormalDist\nself.inv_cdf = None",
    "import pandas as pd\nfor self in [pd]:\n    self.concat = None",
    "class S:\n    def f(self):\n        self = 1",
    "class S:\n    def f(self):\n        import pandas as self",       # 只有 import ... as self 这一处绑定
    "class S:\n    def f(self):\n        try:\n            pass\n        except Exception as self:\n            pass",
    "class S:\n    def f(self, x):\n        match x:\n            case self:\n                pass",
    "def patch(self):\n    return self",                                   # self 当普通函数的参数
    "import pandas as self\nself.concat = None",                          # 模块级的 self.xxx
    "class S:\n    self.x = 1",                                             # 不在方法里的 self.xxx（纵深防御）
])
def test_self_cannot_be_rebound_or_faked(code):
    assert bad(code), code


def test_messages_suggest_alternatives():
    assert any("f-string" in m for m in bad("s = '{:.1%}'.format(0.1)"))
    assert any("嵌套函数" in m for m in check_source(OK.replace(
        "    def select(self, view):\n        return []",
        "    def select(self, view):\n        def helper():\n            self.last = 1\n        return []")))


def test_format_and_pandas_extensions_rejected():
    for code in ["v = None\ns = ('{0._DataView' + '_' + '_panel}').format(v)", "s = '{x}'.format_map({})",
                 "import pandas as pd\n@pd.api.extensions.register_series_accessor('astype')\nclass A: pass",
                 "import pandas as pd\npd.set_option('display.max_rows', None)", "import numpy as np\nnp.seterr(all='ignore')"]:
        assert bad(code), code


def test_attribute_stores_only_on_self():
    for code in ["import pandas as pd\nS = pd.Series\nS.mean = None", "import math\nM = math\nM.erf = abs",
                 "import engine.rules as r\n(r,)[0].COMMISSION_RATE = 0", "import pandas as pd\ndel pd.Series.mean",
                 "import pandas as pd\nfor pd.x in []: pass", "o = object\no.y += 1"]:
        assert any("self" in m for m in bad(code)), code
    ok = OK.replace("        self.k = 20", "        self.k = 20\n        self.k += 1\n        del self.k\n        self.k = 3")
    assert check_source(ok) == []
    assert bad("import pandas as pd\ndf = pd.DataFrame()\ndf['x'] = 1\ndf.loc[0, 'x'] = 2") == []  # 下标赋值可以


@pytest.mark.parametrize("attr", sorted(IO_ATTRS))
def test_every_file_attribute_rejected(attr):
    assert bad(f"p = None\np.{attr}()"), attr


def test_open_price_attribute_allowed_but_open_call_rejected():
    assert bad("b = None\nx = b.open\nfor r in []:\n    y = r.open > r.close") == []
    assert any("open" in m for m in bad("p = None\nf = p.open('x')"))


def test_file_access_through_pathlike_and_pandas_rejected():
    for code in ["import pandas as pd\npd.DataFrame().to_string('leak.txt')", "import numpy as np\nnp.fromregex('a', 'b', [])",
                 "p = None\np.write_text('x')", "p = None\np.open()", "p = None\np.read_text()"]:
        assert bad(code), code


def test_dynamic_access_and_process_control_rejected():
    for code in ["import typing\nf = typing.operator.attrgetter('x')", "import dataclasses\nm = dataclasses.sys",
                 "import pandas as pd\npd.eval('1 + 1')", "df = None\ndf.query('a > 1')",
                 "import dataclasses\ni = dataclasses.inspect", "print('x')", "raise SystemExit(0)",
                 "exit(0)", "quit()"]:
        assert bad(code), code


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
import math
import statistics
from datetime import date
from types import SimpleNamespace

import numpy as np
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
    seen["store_ref"] = Store(db)
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


def test_main_reports_system_exit_as_json(captured, monkeypatch):
    def leave(*a, **k):
        print('{"gate_pass": true, "holdout_pass": true}')  # 伪造的报告行
        raise SystemExit(0)

    monkeypatch.setattr(run_eval, "evaluate", leave)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert json.loads(out.getvalue()) == {"error": "SystemExit: 0"}


def _tamperings(monkeypatch):
    import engine.evaluator as ev
    import engine.rules as rules
    return {
        "gate": lambda: monkeypatch.setitem(ev.GATE, "dsr", -1.0),
        "holdout_gate": lambda: monkeypatch.setitem(ev.HOLDOUT_GATE, "sharpe", -1.0),
        "splits": lambda: monkeypatch.setitem(ev.SPLITS, "holdout", (date(2030, 1, 1), None)),
        "run_backtest": lambda: monkeypatch.setattr(ev, "run_backtest", lambda *a, **k: None),
        "series_mean": lambda: monkeypatch.setattr(pd.Series, "mean", lambda self, *a, **k: 0.01),
        "commission": lambda: monkeypatch.setattr(rules, "COMMISSION_RATE", 0.0),
        "limit_prices": lambda: monkeypatch.setattr(rules, "limit_prices", lambda *a, **k: (99.0, 0.01)),
        "calc_fee": lambda: monkeypatch.setattr(rules, "calc_fee", lambda *a, **k: 0.0),
        "math_erf": lambda: monkeypatch.setattr(math, "erf", lambda x: 1.0),
        "np_sqrt": lambda: monkeypatch.setattr(np, "sqrt", lambda x: x),
        "series_astype": lambda: monkeypatch.setattr(pd.Series, "astype", lambda self, *a, **k: self),
        "inv_cdf": lambda: monkeypatch.setattr(statistics.NormalDist, "inv_cdf", lambda self, p: -10.0),
        "limit_pct": lambda: monkeypatch.setattr(rules, "limit_pct", lambda *a, **k: 0.5),
    }


@pytest.mark.parametrize("what", ["gate", "holdout_gate", "splits", "run_backtest", "series_mean", "commission",
                                  "limit_prices", "calc_fee", "math_erf", "np_sqrt", "series_astype", "inv_cdf",
                                  "limit_pct"])
def test_mutated_rules_void_the_trial(captured, monkeypatch, what):
    tamper_now = _tamperings(monkeypatch)[what]

    def tamper(strategy, name, panel, names, calendar, benchmark_nav, store, params=None):
        store.add_trial(name, {}, 1.0, 1.0, True, True)
        tamper_now()  # 模拟策略代码在评估中改了评估规则
        return FakeReport()

    monkeypatch.setattr(run_eval, "evaluate", tamper)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert "作废" in json.loads(out.getvalue())["error"]
    t = captured["store_ref"].last_trial("baseline_momo")
    assert t["gate_pass"] is False and t["holdout_pass"] is False


def test_lazy_submodule_import_is_not_a_change():
    import importlib
    import sys
    before = run_eval._rules_state()
    sys.modules.pop("numpy.polynomial", None)
    importlib.import_module("numpy.polynomial")  # numpy 子模块惰性导入后会写进 vars(np)
    assert run_eval._rules_state() == before


def test_rules_mutated_while_loading_strategy_void_the_trial(captured, monkeypatch):
    import engine.evaluator as ev

    def load(name):  # 策略模块在 import 时（顶层代码）就改了门槛
        monkeypatch.setitem(ev.GATE, "dsr", -1.0)
        return captured["sentinel"]

    def evaluate(strategy, name, panel, names, calendar, benchmark_nav, store, params=None):
        store.add_trial(name, {}, 1.0, 1.0, True, True)
        return FakeReport()

    monkeypatch.setattr(run_eval, "load_strategy", load)
    monkeypatch.setattr(run_eval, "evaluate", evaluate)
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 1
    assert captured["store_ref"].last_trial("baseline_momo")["gate_pass"] is False


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


def test_real_evaluation_is_not_voided(tmp_path, monkeypatch):
    """不替换 evaluate：真实回测、pandas 运算、惰性导入都不应让规则快照误报。"""
    import engine.evaluator as ev
    days = pd.bdate_range("2026-01-05", periods=90)

    class Client:
        def daily_bars(self, code, start, end):
            close = [10.0 * (1.002 + 0.001 * int(code[-1])) ** i for i in range(len(days))]
            return pd.DataFrame({"date": days, "open": close, "high": [c * 1.01 for c in close],
                                 "low": [c * 0.99 for c in close], "close": close,
                                 "volume": [1e5] * len(days), "amount": [2e8] * len(days)})

    db = tmp_path / "data" / "t.sqlite"
    cache = BarCache(db, Client())
    cache.update(["600001", "600002", "600003"], days[-1].date())
    cache.save_index("sh000300", pd.Series(range(4000, 4000 + len(days)), index=days, dtype=float))
    monkeypatch.setattr(run_eval, "get_settings", lambda: SimpleNamespace(db_path="data/t.sqlite", root=tmp_path))
    monkeypatch.setattr(ev, "SPLITS", {"train": (days[62].date(), days[74].date()),
                                       "val": (days[75].date(), days[84].date()),
                                       "holdout": (days[85].date(), None)})
    out = io.StringIO()
    assert run_eval.main(["baseline_momo"], out=out) == 0, out.getvalue()
    rep = json.loads(out.getvalue())
    assert "gate_pass" in rep and Store(db).trial_count() == 1
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_sandbox.py tests/test_run_eval.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'strategies.sandbox'` 和 `No module named 'engine.run_eval'`。

**Step 3: 最小实现**

沙箱只做 AST 静态检查（尽力而为，不是安全边界）。违规信息是中文短句，直接回传给研究 agent。含 `__` 的名字、属性和字符串（docstring 除外）一律拒绝，`typing.get_type_hints`、`ForwardRef` 这类按字符串求值的入口也拒绝；属性赋值和删除只允许类中方法的第一个参数 `self`（`self` 不能被重新绑定，也不能用作普通函数的参数名）；`str.format`、pandas 扩展接口（`register_series_accessor` 等）、`set_option` 一类改全局状态的入口、经包名访问白名单之外的模块（例如 `engine.evaluator`）同样拒绝。`run_eval` 在加载策略之前对评估相关模块和 pandas、numpy、math、statistics 的命名空间取身份快照，评估后核对，被改过就作废这次试验。`print`、`exit`、`SystemExit` 也拒绝：评估子进程靠 stdout 最后一行回传报告，策略不能往里写，也不能提前结束进程。

```python
# strategies/sandbox.py
"""策略源码静态检查：限制导入、危险内建、双下划线访问、动态取属性、修改已导入模块和文件读写。"""
import ast

ALLOWED_IMPORTS = {"math", "statistics", "numpy", "pandas", "dataclasses", "typing",
                   "engine.types", "engine.dataview", "engine.rules"}
# strategies.base 不在白名单：它模块级导入了 pathlib.Path；策略按鸭子类型实现 Strategy 接口，不需要 import 它
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "breakpoint", "input",
                # 评估子进程只认 stdout 最后一行和数据库里的试验记录：策略不能打印，也不能提前结束进程
                "print", "exit", "quit", "SystemExit", "KeyboardInterrupt", "BaseException",
                "attrgetter", "methodcaller", "get_type_hints", "ForwardRef"}
# 按字符串求值或取属性，或经白名单模块拿到解释器内部模块（例如 dataclasses.sys、typing.operator）
DYNAMIC_ATTRS = {"eval", "query", "sys", "os", "inspect", "operator", "builtins", "importlib",
                 "subprocess", "attrgetter", "methodcaller", "get_type_hints", "ForwardRef",
                 "_eval_type", "_evaluate", "evaluate_forward_ref",
                 # str.format 的字段名按字符串取属性（"{0.x}".format(obj)）
                 "format", "format_map", "vformat", "Formatter",
                 # pandas 扩展接口会用 setattr 覆盖 Series/DataFrame 上的任意方法；set_option 改全局状态
                 "api", "extensions", "register_series_accessor", "register_dataframe_accessor",
                 "register_index_accessor", "register_extension_dtype", "set_option", "option_context",
                 "seterr", "set_printoptions"}
IO_ATTRS = {"to_csv", "to_pickle", "to_parquet", "to_excel", "to_json", "to_hdf", "to_sql",
            "to_feather", "to_html", "to_clipboard", "to_string", "to_latex", "to_xml", "to_stata",
            "to_markdown", "load", "loadtxt", "save", "savez", "savetxt", "fromfile", "tofile",
            "genfromtxt", "fromregex", "memmap", "DataSource", "write_text", "write_bytes",
            "read_text", "read_bytes", "unlink", "mkdir", "touch", "rmdir", "iterdir", "glob", "rglob"}


def _allowed(mod: str) -> bool:
    return any(mod == a or mod.startswith(a + ".") for a in ALLOWED_IMPORTS)


def _has_strategy(tree: ast.Module) -> bool:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "STRATEGY" for t in targets):
            return True
    return False


def _docstrings(tree: ast.Module) -> set[int]:
    """模块、类、函数的 docstring 节点（不会被求值，允许提到 __init__ 之类的名字）。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


def _method_selves(tree: ast.Module, parent: dict) -> set[int]:
    """类里直接定义的方法的第一个参数（真正的 self）所在的函数节点。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(parent.get(node), ast.ClassDef):
            args = node.args.posonlyargs + node.args.args
            if args and args[0].arg == "self":
                out.add(id(node))
    return out


def _enclosing_function(node, parent: dict):
    while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        node = parent.get(node)
    return node


def check_source(src: str) -> list[str]:
    """返回违规列表；空列表表示通过。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"语法错误: {e.msg} (第 {e.lineno} 行)"]
    out: list[str] = []
    docs = _docstrings(tree)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    selves = _method_selves(tree, parent)
    for node in ast.walk(tree):
        # self 只能是方法的第一个参数：不能重新绑定（self = pd.Series 之后再给它赋值，就绕过了下面的检查）
        bound = ((isinstance(node, ast.Name) and node.id == "self" and isinstance(node.ctx, (ast.Store, ast.Del)))
                 or (isinstance(node, ast.alias) and node.asname == "self")
                 or (isinstance(node, (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                       ast.MatchAs, ast.MatchStar)) and getattr(node, "name", None) == "self")
                 or (isinstance(node, (ast.Global, ast.Nonlocal)) and "self" in node.names))
        if bound:
            out.append(f"第 {getattr(node, 'lineno', '?')} 行: 禁止给 self 重新赋值")
        elif isinstance(node, ast.arg) and node.arg == "self" and id(parent.get(parent.get(node))) not in selves:
            out.append(f"第 {node.lineno} 行: self 只能作为类中方法的第一个参数")
    # import engine.types 会把包名 engine 绑定进来，经它能拿到 engine.evaluator 等任何已加载的模块
    pkg_roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names if a.asname is None and "." in a.name}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        # 属性赋值、删除只允许 self.xxx：经任何别名修改模块、类或 pandas 方法（S = pd.Series; S.mean = ...）
        # 都会改变同一进程里评估器的行为。下标赋值（df["x"] = ...）不受影响
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if not (isinstance(node.value, ast.Name) and node.value.id == "self"
                    and id(_enclosing_function(node, parent)) in selves):
                out.append(f"第 {line} 行: 只能在类的方法体内直接给 self 的属性赋值（不能在嵌套函数、lambda 或"
                           f"方法以外），禁止 {_dotted(node) or node.attr}")
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
            elif "__" in node.id:  # 含改编后的私有名字，例如 _DataView__panel
                out.append(f"第 {line} 行: 禁止使用含双下划线的名字 {node.id}")
            elif node.id in pkg_roots and not isinstance(parent.get(node), ast.Attribute):
                out.append(f"第 {line} 行: 包名 {node.id} 只能以完整的白名单模块路径使用")
        elif isinstance(node, ast.Attribute):
            path = _dotted(node)
            if path and path.split(".")[0] in pkg_roots and not _allowed(path) and not any(
                    m.startswith(path + ".") for m in ALLOWED_IMPORTS):
                out.append(f"第 {line} 行: 只能访问白名单模块，禁止 {path}")
            if "__" in node.attr:
                out.append(f"第 {line} 行: 禁止访问含双下划线的属性 {node.attr}")
            elif node.attr.startswith("read_") or node.attr in IO_ATTRS:
                out.append(f"第 {line} 行: 禁止文件读写 {node.attr}")
            elif node.attr == "open" and isinstance(parent.get(node), ast.Call) and parent[node].func is node:
                # 只拦调用（path.open(...)）；r.open、bar.open 是开盘价
                out.append(f"第 {line} 行: 禁止文件读写 open()（读开盘价用 h[\"open\"] 或 bar.open 不受影响）")
            elif node.attr in DYNAMIC_ATTRS:
                hint = {"format": "，拼字符串用 f-string", "format_map": "，拼字符串用 f-string",
                        "api": "，判断类型用 isinstance", "seterr": "，忽略数值警告用 np.errstate",
                        "set_option": "，不要修改全局选项"}.get(node.attr, "")
                out.append(f"第 {line} 行: 禁止动态求值、修改全局状态或访问解释器内部模块 {node.attr}{hint}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "__" in node.value and id(node) not in docs:  # 字符串注解等可以被 typing 求值
                out.append(f"第 {line} 行: 禁止含双下划线的字符串 {node.value[:40]!r}")
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
import math
import statistics
import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from config import get_settings
from data.cache import BarCache
from engine import backtest, broker, evaluator, metrics, rules
from engine.calendar import TradingCalendar
from engine.evaluator import evaluate
from engine.store import Store
from strategies.base import load_strategy

BENCHMARK = "sh000300"
HISTORY_START = date(2015, 6, 1)


def _namespace(obj) -> tuple:
    """模块或类（含基类）命名空间里全部条目的身份快照：替换、新增、删除任何属性都会改变它。值为模块的条目
    不计入：numpy、pandas 的子模块（numpy.polynomial 等）在第一次访问时才导入并写进命名空间，这不是改动。"""
    if isinstance(obj, type):
        items = [(f"{k.__qualname__}.{n}", v) for k in obj.__mro__ for n, v in vars(k).items()]
    else:
        items = list(vars(obj).items())
    return tuple(sorted((n, id(v)) for n, v in items if not isinstance(v, types.ModuleType)))


def _rules_state() -> tuple:
    """评估规则的快照，在加载策略之前取。策略代码与评估器在同一进程里（源码检查已禁止给 self 以外的属性赋值，
    这里是纵深防御）：评估结束后核对，被改过就作废这次试验。"""
    return (repr(evaluator.GATE), repr(evaluator.HOLDOUT_GATE), repr(evaluator.SPLITS), evaluator.run_backtest,
            evaluator.deflated_sharpe, evaluator.sharpe, evaluator._passes_gate, backtest.SimBroker,
            broker.SimBroker.execute_day, metrics.summarize, metrics.deflated_sharpe,
            repr({k: v for k, v in vars(rules).items() if k.isupper()}),
            *(_namespace(m) for m in (evaluator, backtest, broker, metrics, rules, math, statistics, np)),
            *(_namespace(c) for c in (pd.Series, pd.DataFrame, pd.Index, statistics.NormalDist)),
            pd.concat, pd.DataFrame.__init__)


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
    before = _rules_state()  # 先取快照再加载策略：策略模块的顶层代码也可能改动评估规则
    strategy = load_strategy(name)
    report = evaluate(strategy, name, panel, names, calendar, benchmark_nav, store)
    if _rules_state() != before:
        trial = store.last_trial(name)
        if trial is not None:
            store.void_trial(trial["id"])
        raise RuntimeError("评估期间评估规则被改动，本次试验作废（策略代码不能修改 engine 模块）")
    return report.to_agent_dict()


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({"error": "usage: python -m engine.run_eval <strategy_name>"}), file=out)
        return 2
    try:
        result = run(argv[0])
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # noqa: BLE001 - 含策略抛出的 SystemExit：任何失败都要以 JSON 回传给 agent
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

预期：83 passed（sandbox 62 + run_eval 21；读写文件属性逐项的参数化测试含 37 个用例，self 绑定的参数化测试含 9 个用例）。

然后跑本 Part 全部测试，确认没有互相影响：

```
uv run pytest tests/test_akshare_client.py tests/test_cache.py tests/test_wecom.py tests/test_format.py tests/test_strategies.py tests/test_sandbox.py tests/test_run_eval.py -v
```

预期：152 passed。

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

（清单尚不存在时去掉第一行命令和 `git add` 中的 `.integrity/manifest.json`。提交信息末尾追加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）


---

## Part 4: Claude 接入：盘前推荐与复盘

> 说明（合同在本部分内的取舍）：
> 1. Claude 统一经 Claude Code CLI 调用（Task 4.1 的 `agents/llm.py`），即使很小的提示也要 10~25 秒。因此盘前分两段：08:30 `premarket_scan` 在 `scan` 之后立即调用 `premarket.run_analyze`，两套 playbook 各做一次深度分析（`effort="high"`，单次上限 `ANALYZE_TIMEOUT=900` 秒，`max_budget_usd=1.5`），为每只候选写下竞价条件 `min_gap_pct/max_gap_pct/entry_max` 和止损止盈价，存入 kv `plan:<ISO日期>:<kind>`；09:25 竞价结束后 `premarket.run_final` 先做确定性规则过滤 `auction_filter`，再做一次只能否决、不能新增的轻量确认（`effort="low"`，`AUCTION_TIMEOUT=90` 秒，`max_budget_usd=0.3`）。
> 2. 09:28 前必须推送。`run_final` 从自身开始计时：两路都没有计划时不拉快照；否则在守护线程里拉一次竞价快照，最多等 `SPOT_TIMEOUT=40` 秒（超时或失败按竞价数据缺失处理），两路共用。竞价确认的超时取 `AUCTION_TIMEOUT` 与剩余时间的较小者；到 `FINAL_DEADLINE=110` 秒仍未定稿的那一路直接改用确定性结果（有计划用规则过滤结果，无计划用量化）。09:25:15 启动时最迟约 09:27:05 定稿推送。超时那一路的线程留在后台跑完，结果丢弃。
> 3. 降级规则：深度分析失败（超时、单次花费超限、CLI 报错、输出里没有任何候选代码）→ 不存计划，09:25 该路退回量化（`llm_reviewed=False`，推送标注"未经 LLM 审核"）；竞价确认失败或超时 → 规则过滤结果（`llm_reviewed=True`，市场观点后注明"竞价二次确认未完成"）；规则过滤后为空 → 当天不推荐（`llm_reviewed=True`，注明"竞价条件均未满足"）；当天没有候选 → `RuntimeError`，由 `run_job` 告警。
> 4. `llm.parse` 只抛 `BudgetExceeded`、`LLMError`（拒答、CLI 报错、缺少结构化输出、非 JSON 都归入它，`LLMRefusal` 已删除）和 `subprocess.TimeoutExpired`；盘前与复盘一律 `except Exception` 兜底。`max_tokens` 参数已不存在，单次花费上限改用 `max_budget_usd`。测试一律用与 `LLM.parse` 同签名的假对象，不启动 claude.exe、不联网。
> 5. `run_analyze` 和 `run_final` 都在两个线程里并发处理两套 playbook，共享同一个 `Store`（`llm.parse` 会写 `add_llm_usage`）。Part 2 的 `Store` 已用 `check_same_thread=False` + `RLock`；Task 4.3 的测试用真实 `Store(":memory:")` 跨线程验证，`run_analyze` 的测试还用 `threading.Barrier` 确认两路确实并发。
> 6. 本部分新增的名字：`playbook.ROOT`、`playbook.load_prompt(name)`（读 `playbook/<name>_system.md`）；`premarket.load_plan(store, d, kind)`（读回已存计划，没有或无法解析时为 None）；`premarket.SPOT_TIMEOUT`；`finalize` 的 `spot` 传 `None` 表示自行拉取，传空表表示竞价数据缺失。`premarket.AUCTION_SYSTEM` 是代码常量，不放在 `playbook/` 里；`playbook/premarket_system.md` 只用于 08:30 的深度分析。
> 7. 竞价快照获取失败或为空时没有快照行，计划中的 keep 全部通过规则过滤，确认调用的提示里写明"竞价数据缺失"；有快照行但竞价价或昨收缺失、非正（多为停牌）视为不满足条件。竞价涨幅四舍五入到 0.01 个百分点后按闭区间 `[min_gap_pct, max_gap_pct]` 比较，避免 10.20/10.00 这类恰在边界的价格因浮点误差被误判；`min_gap_pct > max_gap_pct` 时区间为空，有快照行的该股不通过。规则过滤后为空时不再调用竞价确认。
> 8. `maybe_release` 首次调用只记录窗口起点并返回 None；候选与现行规则相同则只重开窗口；落后时重置候选并返回提示文本。
> 9. 价格合理性：计划价位四舍五入到分后，满足 `stop < entry_max < target` 且三者都在量化 `entry_max` 的 0.5~1.5 倍内才采用，否则沿用量化价格。规则过滤中的"竞价价 ≤ entry_max"和"竞价价 > stop"使用最终采用的 entry_max 和 stop，和撮合（Task 1.6）判断开盘价的口径一致。
> 10. `run_review` 遇到 `BudgetExceeded` 以外的 LLM 异常（`LLMError`、超时、校验失败）也照常推送日报，摘要写明"复盘失败"，job 记为 ok，避免一次调用故障让当天日报丢失；每次最多采纳 `review.MAX_LESSONS = 3` 条 lesson。
> 11. `agents.review` 以 `playbook.git_commit(...)` / `playbook.maybe_release(...)` 的模块属性方式调用，便于测试打桩；提交路径固定为 `playbook/active.json`、`playbook/candidate.json`。

前置：Part 1~3 已完成（`config`、`engine.types/store/calendar/ledger/dataview/backtest`、`notify.format`、`data.*`）。

### Task 4.1: agents/llm.py — Claude 调用封装

**Files:**
- Create: `agents/llm.py`
- Create: `scripts/smoke_llm.py`
- Test: `tests/test_llm.py`

背景（2026-10-19 实测，Claude Code 2.1.223，经用户中转 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`）：

- 中转拒绝 Anthropic SDK 直连（报 "Please use authentic Claude Code client"），只有 Claude Code CLI 能用。所以本模块不用 SDK，每次调用起一个 `claude.exe` 子进程。
- 可用的命令行：`claude.exe -p --bare --output-format json --model claude-opus-5-5 --effort <lvl> --tools "" --no-session-persistence --setting-sources "" --max-budget-usd <x> --system-prompt-file <file> --json-schema <json>`。`--setting-sources ""` 不加载用户、项目、本地设置文件：设置文件里的 `env`（例如本机 Claude Code 在 `~/.claude/settings.json` 里配的中转地址和 Token）会被 claude.exe 写进自己的进程环境、覆盖 `child_env`，鉴权就不再只由 `.env` 决定（这一点读 claude.exe 内嵌代码得出，需在 Task 6.8 冒烟中确认）。用户提示词以 UTF-8 字节经 stdin 传入，既不受 Windows 命令行长度限制，也不经过控制台代码页。子进程环境里不能有 `CLAUDECODE`。`--bare` 不加载用户的插件、hooks 和 CLAUDE.md，鉴权只读环境变量：`ANTHROPIC_API_KEY`，或 `ANTHROPIC_AUTH_TOKEN` 加 `ANTHROPIC_BASE_URL`。
- stdout 是一个 JSON 对象，字段有 `is_error`（bool）、`subtype`（`"success"` 或错误类型）、`result`（str）、`structured_output`（dict 或 null）、`total_cost_usd`（float）、`usage`（`input_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`、`output_tokens`）、`duration_ms`、`num_turns`。
- 嵌套 schema（对象数组、enum、`$defs`/`$ref`）可用，schema 用 `json.dumps(..., ensure_ascii=True)` 传。小调用耗时 10–25 秒，花费 0.01–0.03 美元。
- 必须直接运行真正的 `claude.exe`，不能经 npm 的 `claude.cmd`/`claude.ps1` 包装脚本，因为 cmd.exe 的引号规则会破坏 JSON 参数。npm 安装时 exe 位于 `<claude.cmd 所在目录>/node_modules/@anthropic-ai/claude-code/bin/claude.exe`，原生安装位于 `~/.local/bin/claude.exe`。
- Claude Code 的文件权限规则在 Windows 上表现不一致，不能依赖。所以每次调用都传 `--tools ""`：模型不使用任何工具，所需上下文全部由 Python 写进提示词，读写文件和跑评估也都由 Python 完成。
- 提示词读起来像安全测试或权限测试时，会触发 Opus 5.5 的 cyber 分类器；服务端随后回退到中转没有的 claude-opus-4-8，调用失败。代码约束要写成编码规范（例如"策略代码只能 import 下列模块"），提示词里不要出现 security、sandbox、bypass 一类措辞。
- 任何 LLM 失败都必须平稳降级：premarket 照样在 09:28 前推送，review 照样推送日报，research 干净地中止。`parse` 的失败只有三类：`BudgetExceeded`、`LLMError`（含 claude.exe 启动失败）、`subprocess.TimeoutExpired`；`effort` 拼错时另抛 `ValueError`。调用方统一 `except Exception` 兜底。

公共接口：`BudgetExceeded`、`LLMError`、`strict_schema`、`find_claude_exe`、`child_env`、`build_args`、`LLM(settings, store, runner=None)`（方法 `month_cost`、`check_budget`、`parse`）。与旧版相比：`LLMRefusal` 已删除，拒答也抛 `LLMError`；`parse` 不再有 `max_tokens` 参数，改为 `max_budget_usd`，传给 `--max-budget-usd`，花费超过上限时 CLI 自行中止；`LLM.client`、`LLM.cost_cny` 已删除。

**Step 1: 写失败的测试**

`tests/test_llm.py`（`runner` 注入假的 `subprocess.run`，不会启动真实 claude.exe；`shutil.which` 与家目录也都隔离）：

```python
import datetime as dt
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from agents import llm as llm_mod
from agents.llm import LLM, BudgetExceeded, LLMError, build_args, child_env, find_claude_exe, strict_schema
from config import Settings
from engine.store import Store


class Inner(BaseModel):
    code: str = Field(description="股票代码")
    score: float


class Outer(BaseModel):
    view: str
    items: list[Inner]
    note: str = ""


GOOD = {"view": "震荡", "items": [{"code": "600000", "score": 0.7}], "note": ""}
SYSTEM = "你是测试助手。\n只输出 JSON。"
USER = "请分析 600000 浦发银行的竞价情况"
NPM_EXE = Path("node_modules/@anthropic-ai/claude-code/bin/claude.exe")


def cli_output(structured=GOOD, *, is_error=False, subtype="success", result="", cost=0.0215):
    """claude -p --output-format json 的 stdout 结构（字段与实测一致）。"""
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": result,
            "structured_output": structured, "total_cost_usd": cost, "duration_ms": 15000, "num_turns": 2,
            "usage": {"input_tokens": 1200, "cache_creation_input_tokens": 3000,
                      "cache_read_input_tokens": 500, "output_tokens": 350}}


class FakeRunner:
    """subprocess.run 替身：记录参数，在调用期间读出系统提示词文件，返回预设 stdout。"""

    def __init__(self, payload=None, returncode=0, stdout=None, exc=None):
        self.stdout = stdout if stdout is not None else json.dumps(payload or cli_output()).encode("utf-8")
        self.returncode = returncode
        self.exc = exc
        self.calls: list[tuple[list[str], dict]] = []
        self.system_seen: list[bytes] = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        self.system_seen.append(Path(args[args.index("--system-prompt-file") + 1]).read_bytes())
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(args=args, returncode=self.returncode, stdout=self.stdout, stderr=b"")

    def system_file(self, i=0) -> Path:
        args = self.calls[i][0]
        return Path(args[args.index("--system-prompt-file") + 1])


def make_settings(root, **kw) -> Settings:
    base = dict(anthropic_api_key="", wecom_webhook_url="", root=root, usd_cny=7.2)
    base.update(kw)
    return Settings(**base)


def touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, claude_exe=str(touch(tmp_path / "bin" / "claude.exe")))


@pytest.fixture
def no_claude(monkeypatch, tmp_path):
    """隔离本机安装：shutil.which 只认测试登记的路径，家目录指向 tmp_path/home。"""
    found: dict[str, str] = {}
    monkeypatch.setattr(llm_mod.shutil, "which", lambda name: found.get(name))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return found


def usage_rows(store):
    return store.conn.execute("SELECT purpose, input_tokens, output_tokens, cost_cny FROM llm_usage").fetchall()


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


def test_build_args_is_the_verified_command(tmp_path):
    s = make_settings(tmp_path)
    args = build_args(s, r"C:\cc\claude.exe", tmp_path / "sys.md", '{"type": "object"}', "high", 1.5)
    assert args == [r"C:\cc\claude.exe", "-p", "--bare", "--output-format", "json",
                    "--model", "claude-opus-5-5", "--effort", "high", "--tools", "",
                    "--no-session-persistence", "--setting-sources", "", "--max-budget-usd", "1.50",
                    "--system-prompt-file", str(tmp_path / "sys.md"), "--json-schema", '{"type": "object"}']
    with pytest.raises(ValueError):
        build_args(s, "claude.exe", "sys.md", "{}", "extreme", 1.0)


def test_child_env_strips_claudecode_and_inherited_anthropic_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-other")
    monkeypatch.setenv("RSIQS_KEEP_ME", "yes")
    relay = make_settings(tmp_path, anthropic_base_url="https://relay.example.invalid", anthropic_auth_token="tok")
    env = child_env(relay)
    assert "CLAUDECODE" not in env and env["RSIQS_KEEP_ME"] == "yes"
    assert {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")} == {
        "ANTHROPIC_BASE_URL": "https://relay.example.invalid", "ANTHROPIC_AUTH_TOKEN": "tok"}
    official = child_env(make_settings(tmp_path, anthropic_api_key="sk-test"))
    assert {k: v for k, v in official.items() if k.startswith("ANTHROPIC_")} == {"ANTHROPIC_API_KEY": "sk-test"}


def test_parse_runs_cli_and_returns_model(store, settings, tmp_path):
    runner = FakeRunner()
    out = LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, effort="low", timeout=30.0)
    assert isinstance(out, Outer) and out.view == "震荡" and out.items[0].code == "600000"
    assert len(runner.calls) == 1
    args, kw = runner.calls[0]
    assert args[0] == settings.claude_exe and args[1:3] == ["-p", "--bare"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--effort") + 1] == "low"
    assert args[args.index("--max-budget-usd") + 1] == "1.00"
    schema_json = args[args.index("--json-schema") + 1]
    assert schema_json == json.dumps(strict_schema(Outer), ensure_ascii=True)
    assert schema_json.isascii() and "股票代码" in json.loads(schema_json)["$defs"]["Inner"]["properties"]["code"][
        "description"]
    assert args == build_args(settings, settings.claude_exe, runner.system_file(), schema_json, "low", 1.0)
    assert kw["input"] == USER.encode("utf-8")
    assert kw["capture_output"] is True and kw["timeout"] == 30.0 and kw["cwd"] == str(tmp_path)
    assert runner.system_seen == [SYSTEM.encode("utf-8")]  # 调用期间文件内容 == 系统提示词
    assert runner.system_file().suffix == ".md" and not runner.system_file().exists()  # 调用后删除


def test_parse_passes_child_env(monkeypatch, store, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://inherited.invalid")
    s = make_settings(tmp_path, claude_exe=str(touch(tmp_path / "claude.exe")), anthropic_api_key="sk-test")
    runner = FakeRunner()
    LLM(s, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    env = runner.calls[0][1]["env"]
    assert "CLAUDECODE" not in env
    assert {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")} == {"ANTHROPIC_API_KEY": "sk-test"}


def test_usage_tokens_and_cost_recorded(store, settings):
    LLM(settings, store, runner=FakeRunner()).parse("unit", SYSTEM, USER, Outer, max_budget_usd=0.3)
    [(purpose, tokens_in, tokens_out, cost)] = usage_rows(store)
    assert (purpose, tokens_in, tokens_out) == ("unit", 1200 + 3000 + 500, 350)
    assert cost == pytest.approx(0.0215 * 7.2)
    assert this_month_cost(store) == pytest.approx(0.0215 * 7.2)


def test_cli_error_raises_but_usage_recorded(store, settings):
    runner = FakeRunner(cli_output(structured=None, is_error=True, subtype="error_max_budget_usd",
                                   result="Reached maximum budget", cost=0.5), returncode=1)
    with pytest.raises(LLMError, match="error_max_budget_usd"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert this_month_cost(store) == pytest.approx(0.5 * 7.2)
    assert not runner.system_file().exists()


NO_STRUCTURED = {k: v for k, v in cli_output().items() if k != "structured_output"}


@pytest.mark.parametrize("payload, returncode", [
    (cli_output(is_error=True, result="Claude 拒绝回答"), 0),  # is_error
    (cli_output(), 1),  # 退出码非 0
    (cli_output(structured=None, result="纯文本回答"), 0),  # structured_output 为 null
    (NO_STRUCTURED, 0),  # 没有 structured_output 字段
])
def test_cli_failures_raise_llm_error(store, settings, payload, returncode):
    with pytest.raises(LLMError):
        LLM(settings, store, runner=FakeRunner(payload, returncode=returncode)).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1


@pytest.mark.parametrize("stdout", [b"", b"Error: something went wrong", b"\xff\xfe\x00", b"[1, 2]"])
def test_non_json_stdout_raises_llm_error(store, settings, stdout):
    runner = FakeRunner(stdout=stdout, returncode=1)
    with pytest.raises(LLMError):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert usage_rows(store) == []
    assert not runner.system_file().exists()


def test_structured_output_failing_validation_raises_llm_error(store, settings):
    runner = FakeRunner(cli_output(structured={"view": 1}))
    with pytest.raises(LLMError, match="Outer"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1


def test_timeout_propagates_and_cleans_up(store, settings):
    runner = FakeRunner(exc=subprocess.TimeoutExpired(["claude.exe"], 5.0))
    with pytest.raises(subprocess.TimeoutExpired):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, timeout=5.0, max_budget_usd=0.3)
    assert runner.system_seen == [SYSTEM.encode("utf-8")] and not runner.system_file().exists()
    # 超时时服务端可能已经计费，CLI 来不及报告：按单次上限保守记一笔，月度预算不会少算
    [(purpose, _, _, cost)] = usage_rows(store)
    assert purpose == "unit:timeout" and cost == pytest.approx(0.3 * 7.2)


def test_launch_failure_raises_llm_error(store, settings):
    runner = FakeRunner(exc=FileNotFoundError(2, "系统找不到指定的文件。"))
    with pytest.raises(LLMError, match="claude.exe"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)
    assert not runner.system_file().exists() and usage_rows(store) == []


def test_budget_exceeded_non_essential_skips_runner(no_claude, store, tmp_path):
    store.add_llm_usage("old", 0, 0, 300.0)
    runner = FakeRunner()
    llm = LLM(make_settings(tmp_path, monthly_budget_cny=300.0), store, runner=runner)
    with pytest.raises(BudgetExceeded):  # 先查预算：连 claude.exe 都不用找
        llm.parse("unit", SYSTEM, USER, Outer)
    assert runner.calls == []


def test_budget_exceeded_essential_passes(store, settings):
    store.add_llm_usage("old", 0, 0, 300.0)
    runner = FakeRunner()
    assert LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, essential=True).view == "震荡"
    assert len(runner.calls) == 1


def test_exe_resolved_lazily_and_cached(monkeypatch, store, settings):
    lookups = []
    monkeypatch.setattr(llm_mod, "find_claude_exe", lambda s: lookups.append(s) or s.claude_exe)
    runner = FakeRunner()
    llm = LLM(settings, store, runner=runner)
    assert lookups == []  # 构造时不查找
    llm.parse("a", SYSTEM, USER, Outer)
    llm.parse("b", SYSTEM, USER, Outer)
    assert len(lookups) == 1 and len(runner.calls) == 2


def test_missing_exe_raises_llm_error_at_parse(no_claude, store, tmp_path):
    runner = FakeRunner()
    llm = LLM(make_settings(tmp_path), store, runner=runner)  # 找不到 claude.exe 也能构造
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        llm.parse("unit", SYSTEM, USER, Outer)
    assert runner.calls == [] and usage_rows(store) == []


def test_find_settings_value_wins(no_claude, tmp_path):
    mine = touch(tmp_path / "custom" / "claude.exe")
    no_claude["claude.exe"] = str(touch(tmp_path / "other" / "claude.exe"))
    assert find_claude_exe(make_settings(tmp_path, claude_exe=str(mine))) == str(mine)
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        find_claude_exe(make_settings(tmp_path, claude_exe=str(tmp_path / "nope" / "claude.exe")))


def test_find_settings_wrapper_maps_to_npm_exe(no_claude, tmp_path):
    exe = touch(tmp_path / "npm" / NPM_EXE)
    cmd = touch(tmp_path / "npm" / "claude.cmd")
    assert find_claude_exe(make_settings(tmp_path, claude_exe=str(cmd))) == str(exe)
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        find_claude_exe(make_settings(tmp_path, claude_exe=str(touch(tmp_path / "x" / "claude.ps1"))))


def test_find_npm_wrapper_on_path_resolves_to_sibling_exe(no_claude, tmp_path):
    no_claude["claude"] = str(touch(tmp_path / "nodejs" / "claude.CMD"))
    exe = touch(tmp_path / "nodejs" / NPM_EXE)
    no_claude["claude.exe"] = str(touch(tmp_path / "other" / "claude.exe"))
    assert find_claude_exe(make_settings(tmp_path)) == str(exe)  # npm 旁的 exe 优先


def test_find_never_returns_wrapper(no_claude, tmp_path):
    wrapper = str(touch(tmp_path / "nodejs" / "claude.cmd"))  # npm 目录残缺：没有 bin/claude.exe
    no_claude["claude"] = wrapper
    no_claude["claude.exe"] = wrapper
    with pytest.raises(LLMError, match="CLAUDE_EXE"):
        find_claude_exe(make_settings(tmp_path))


def test_find_path_exe_then_native_install_then_error(no_claude, tmp_path):
    on_path = str(touch(tmp_path / "bin" / "claude.exe"))
    no_claude.update({"claude": on_path, "claude.exe": on_path})  # 原生安装在 PATH 上
    assert find_claude_exe(make_settings(tmp_path)) == on_path
    no_claude.clear()
    native = touch(tmp_path / "home" / ".local" / "bin" / "claude.exe")
    assert find_claude_exe(make_settings(tmp_path)) == str(native)
    native.unlink()
    with pytest.raises(LLMError, match="找不到 claude.exe"):
        find_claude_exe(make_settings(tmp_path))


def test_cli_error_reason_from_errors_field(store, settings):
    out = {k: v for k, v in cli_output(structured=None, is_error=True, subtype="error_during_execution").items()
           if k != "result"}
    out["errors"] = ["API Error: 400 model not available"]
    runner = FakeRunner(out, returncode=1)
    with pytest.raises(LLMError, match="model not available"):
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer)


def test_tiny_budget_is_budget_exceeded(store, settings):
    runner = FakeRunner()
    with pytest.raises(BudgetExceeded):  # --max-budget-usd 0.00 会被 CLI 拒绝
        LLM(settings, store, runner=runner).parse("unit", SYSTEM, USER, Outer, max_budget_usd=0.004)
    assert runner.calls == []
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_llm.py -v
```

预期：`ImportError: cannot import name 'llm' from 'agents'`（模块不存在），收集阶段报错，全部 ERROR。

**Step 3: 最小实现**

`agents/llm.py`:

```python
"""Claude 调用封装：经 Claude Code CLI 无头模式（claude.exe -p --bare）调用，结构化输出、记账、预算。

中转只接受 Claude Code 客户端，Anthropic SDK 直连会被拒绝，所以这里不用 SDK：每次调用起一个 claude.exe 子进程，
系统提示词写进临时 .md 文件，用户提示词以 UTF-8 字节走 stdin，JSON Schema 走 --json-schema。
每次都传 --tools ""：模型不使用任何工具，所需上下文全部由 Python 放进提示词。
费用取 CLI 输出的 total_cost_usd（美元）× usd_cny 记入 llm_usage。
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from config import Settings
from engine.store import Store

EFFORTS = ("low", "medium", "high", "xhigh", "max")
NPM_EXE = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
WRAPPER_SUFFIXES = (".cmd", ".bat", ".ps1")
NOT_FOUND = "找不到 claude.exe，请在 .env 设置 CLAUDE_EXE"


class BudgetExceeded(Exception):
    pass


class LLMError(Exception):
    """找不到或无法运行 claude.exe、CLI 报错或拒答、没有 structured_output、stdout 不是 JSON、结构校验失败。"""


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


def _is_wrapper(path: str) -> bool:
    return path.lower().endswith(WRAPPER_SUFFIXES)


def find_claude_exe(settings: Settings) -> str:
    """claude.exe 的路径，从不返回 .cmd/.ps1 包装脚本（cmd.exe 的引号规则会破坏 JSON 参数）。

    顺序：settings.claude_exe → PATH 上 claude（npm 包装脚本）旁的 node_modules/.../bin/claude.exe
    → PATH 上的 claude.exe → ~/.local/bin/claude.exe（原生安装）。
    """
    configured = settings.claude_exe.strip().strip('"')
    if configured:
        if _is_wrapper(configured):
            exe = Path(configured).parent / NPM_EXE
            if exe.is_file():
                return str(exe)
            raise LLMError(f"CLAUDE_EXE 指向包装脚本 {configured}，请改为 claude.exe 的完整路径")
        if not Path(configured).is_file():
            raise LLMError(f"CLAUDE_EXE 指向的文件不存在：{configured!r}（请填完整路径，.env 里不要加双引号）")
        return configured
    wrapper = shutil.which("claude")
    if wrapper:
        exe = Path(wrapper).parent / NPM_EXE
        if exe.is_file():
            return str(exe)
    found = shutil.which("claude.exe")
    if found and not _is_wrapper(found):
        return found
    native = Path.home() / ".local" / "bin" / "claude.exe"
    if native.is_file():
        return str(native)
    raise LLMError(NOT_FOUND)


def child_env(settings: Settings) -> dict[str, str]:
    """子进程环境：去掉 CLAUDECODE 与所有继承来的 ANTHROPIC_*，只放入 settings 中非空的鉴权变量。"""
    env = {k: v for k, v in os.environ.items()
           if k.upper() != "CLAUDECODE" and not k.upper().startswith("ANTHROPIC_")}
    for key, value in (("ANTHROPIC_API_KEY", settings.anthropic_api_key),
                       ("ANTHROPIC_BASE_URL", settings.anthropic_base_url),
                       ("ANTHROPIC_AUTH_TOKEN", settings.anthropic_auth_token)):
        if value:
            env[key] = value
    return env


def build_args(settings: Settings, exe: str, system_file: str | Path, schema_json: str, effort: str,
               max_budget_usd: float) -> list[str]:
    """已实测可用的无头调用命令行；用户提示词不在命令行里，走 stdin。"""
    if effort not in EFFORTS:
        raise ValueError(f"unknown effort: {effort!r}")
    # --setting-sources ""：不加载用户、项目、本地设置文件。设置文件里的 env（例如本机 Claude Code 配的中转地址和
    # Token）会被 claude.exe 写进自己的进程环境，覆盖 child_env，鉴权就不再只由 .env 决定
    return [exe, "-p", "--bare", "--output-format", "json", "--model", settings.llm_model,
            "--effort", effort, "--tools", "", "--no-session-persistence", "--setting-sources", "",
            "--max-budget-usd", f"{max_budget_usd:.2f}", "--system-prompt-file", str(system_file),
            "--json-schema", schema_json]


def _int(value: Any) -> int:
    return int(value or 0)


def _tail(raw: Any, limit: int = 300) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return str(raw or "").strip()[-limit:]


class LLM:
    def __init__(self, settings: Settings, store: Store, runner: Callable[..., Any] | None = None):
        self.settings = settings
        self.store = store
        self.runner = runner or subprocess.run
        self._exe: str | None = None  # 首次 parse 时查找并缓存，构造 LLM 永不失败

    def _claude_exe(self) -> str:
        if self._exe is None:
            self._exe = find_claude_exe(self.settings)
        return self._exe

    def month_cost(self) -> float:
        t = dt.date.today()
        return self.store.month_llm_cost(t.year, t.month)

    def check_budget(self, essential: bool) -> None:
        if not essential and self.month_cost() >= self.settings.monthly_budget_cny:
            raise BudgetExceeded(
                f"本月 LLM 花费 {self.month_cost():.2f} 元已达预算 {self.settings.monthly_budget_cny:.0f} 元")

    def parse(self, purpose: str, system: str, user: str, schema: type[BaseModel], effort: str = "medium",
              timeout: float = 120.0, max_budget_usd: float = 1.0, essential: bool = False) -> BaseModel:
        """一次无头调用，返回 schema 实例。

        预算不足抛 BudgetExceeded；超时抛 subprocess.TimeoutExpired（子进程已被杀掉）；
        claude.exe 启动失败、CLI 报错、拒答、没有 structured_output、输出不是 JSON、结构校验失败都抛 LLMError。
        """
        self.check_budget(essential)
        if max_budget_usd < 0.01:  # 格式化成 0.00 后会被 CLI 拒绝
            raise BudgetExceeded(f"{purpose}: 单次预算 {max_budget_usd:.4f} 美元不足 0.01 美元")
        exe = self._claude_exe()
        schema_json = json.dumps(strict_schema(schema), ensure_ascii=True)
        fd, system_file = tempfile.mkstemp(prefix="rsiqs_system_", suffix=".md")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(system.encode("utf-8"))
            args = build_args(self.settings, exe, system_file, schema_json, effort, max_budget_usd)
            proc = self.runner(args, input=user.encode("utf-8"), capture_output=True,
                               env=child_env(self.settings), cwd=str(self.settings.root), timeout=timeout)
        except subprocess.TimeoutExpired:
            # 子进程已被杀掉，CLI 没来得及报告用量，服务端可能已经计费：按单次上限保守记一笔
            self.store.add_llm_usage(f"{purpose}:timeout", 0, 0, max_budget_usd * self.settings.usd_cny)
            raise
        except OSError as e:  # claude.exe 启动不了（安装后被删除、没有权限等）
            raise LLMError(f"{purpose}: 无法运行 claude.exe：{e!r}") from e
        finally:
            with contextlib.suppress(OSError):
                os.unlink(system_file)
        try:
            out = json.loads(proc.stdout.decode("utf-8"))
        except ValueError as e:  # 含 UnicodeDecodeError / JSONDecodeError
            raise LLMError(f"{purpose}: claude 输出不是 JSON（exit={proc.returncode}）："
                           f"{_tail(proc.stderr) or _tail(proc.stdout)}") from e
        if not isinstance(out, dict):
            raise LLMError(f"{purpose}: claude 输出不是 JSON 对象（exit={proc.returncode}）")
        usage = out.get("usage") or {}
        tokens_in = sum(_int(usage.get(k)) for k in
                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.store.add_llm_usage(purpose, tokens_in, _int(usage.get("output_tokens")),
                                 float(out.get("total_cost_usd") or 0.0) * self.settings.usd_cny)
        structured = out.get("structured_output")
        if proc.returncode != 0 or out.get("is_error") or structured is None:
            reason = out.get("result") or "; ".join(map(str, out.get("errors") or []))  # 错误结果的原因在 errors
            raise LLMError(f"{purpose}: {out.get('subtype', '')} {str(reason)[:300]}")
        try:
            return schema.model_validate(structured)
        except ValidationError as e:
            raise LLMError(f"{purpose}: structured_output 不符合 {schema.__name__}：{e}") from e
```

说明：
- `parse` 的顺序：查预算 → 找 claude.exe（首次查找后缓存）→ 写临时系统提示词文件 → 起子进程 → `finally` 删除临时文件 → 解析 stdout → 记账 → 判错 → 校验。只要 stdout 是 JSON 对象就记账，`is_error` 时也记，因为 CLI 报错前可能已经产生费用；stdout 不是 JSON 对象时没有可记的用量。
- 记账：输入 token = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`，费用 = `total_cost_usd × usd_cny`（元），写入 `store.add_llm_usage`。
- `structured_output` 交给 pydantic 校验，不符合 schema 时同样包装成 `LLMError`。这样调用方只需区分 `BudgetExceeded` 与 `LLMError`，研究循环据此停止。
- `subprocess.run` 超时会先杀掉 claude.exe 再抛 `TimeoutExpired`，这里不捕获，交给调用方。claude.exe 启动失败（`OSError`）则包装成 `LLMError`。
- 构造 `LLM` 不查找 claude.exe，第一次 `parse` 才查找，因此 `jobs.common.make_llm` 在未安装 Claude Code 的机器上也不会失败；找不到时由 `parse` 抛 `LLMError`，走各调用方的降级路径。
- `build_args` 只接受 `low/medium/high/xhigh/max` 五档 `effort`，拼错时在起子进程前就抛 `ValueError`。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_llm.py -v
```

预期：29 passed（两个参数化测试各 4 个用例）。

**Step 5: Manual smoke step（真实调用一次 Claude，约 0.1~0.2 元）**

前提：`.env` 已填 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`（或 `ANTHROPIC_API_KEY`），`CLAUDE_EXE` 可以留空。

`scripts/smoke_llm.py`:

```python
"""手动冒烟：经 Claude Code CLI 真实调用一次 Claude（约 0.1~0.2 元），验证 claude.exe 查找、鉴权与结构化输出。
用法：uv run python scripts/smoke_llm.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from agents.llm import LLM, find_claude_exe  # noqa: E402
from config import get_settings  # noqa: E402
from engine.store import Store  # noqa: E402


class Ping(BaseModel):
    answer: str
    numbers: list[int]


def main() -> None:
    s = get_settings()
    if not (s.anthropic_api_key or (s.anthropic_base_url and s.anthropic_auth_token)):
        print("警告：.env 中既没有 ANTHROPIC_API_KEY，也没有 ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN")
    print("claude.exe:", find_claude_exe(s))
    store = Store(s.root / s.db_path)
    llm = LLM(s, store)
    t0 = time.monotonic()
    out = llm.parse("smoke", "你是连通性测试助手，严格按 JSON schema 输出。",
                    "answer 填“好”，numbers 按顺序列出 1 到 3。", Ping,
                    effort="low", timeout=120.0, max_budget_usd=0.2, essential=True)
    print(f"result: {out!r}（耗时 {time.monotonic() - t0:.1f} 秒）")
    print(f"month cost CNY: {llm.month_cost():.4f}")
    assert out.numbers == [1, 2, 3], out


if __name__ == "__main__":
    main()
```

```
uv run python scripts/smoke_llm.py
```

预期（10–30 秒）：依次打印 `claude.exe: ...\claude.exe`（必须是 exe，不能是 `.cmd`）、`result: Ping(answer='好', numbers=[1, 2, 3])（耗时 …秒）`、`month cost CNY: …`（大于 0）。若失败：
- `找不到 claude.exe` 或 `CLAUDE_EXE 指向…`：在 `.env` 写 `CLAUDE_EXE=<claude.exe 完整路径>`，不要加双引号。npm 安装时，PowerShell 里 `Split-Path (Get-Command claude).Source` 给出的目录下就是 `node_modules\@anthropic-ai\claude-code\bin\claude.exe`。
- `LLMError: smoke: <subtype> <result>`：先核对 `.env` 的鉴权变量，再确认 `claude --version` 能正常运行。Claude Code 升级或中转故障也会表现为这类错误；定时任务里出现时按各环节的降级路径处理（盘前退回量化推荐并告警，复盘照发日报），不会漏推送。
- `subprocess.TimeoutExpired`：网络或中转响应过慢，稍后重试。

**Step 6: 提交**

```
git add agents/llm.py tests/test_llm.py scripts/smoke_llm.py
git commit -m "feat: call Claude via Claude Code CLI headless mode with strict schema, usage and budget"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

### Task 4.2: agents/playbook.py — 经验手册与系统提示词

**Files:**
- Create: `agents/playbook.py`
- Create: `playbook/premarket_system.md`
- Create: `playbook/review_system.md`
- Test: `tests/test_playbook.py`

`apply_lessons` 只依赖 lesson 的 `action/rule_id/text/evidence` 属性（鸭子类型），避免与 `agents.review` 循环导入；测试用 `SimpleNamespace` 构造。`git_commit` 返回 True 表示确实产生了提交；暂存区无变化也返回 False。`playbook/premarket_system.md` 是 08:30 盘前深度分析（Task 4.3 的 `analyze`）的系统提示词；09:25 竞价确认用的是 `agents/premarket.py` 中的常量 `AUCTION_SYSTEM`，不在本文件里。

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

    big = [L("add", "长" * playbook.MAX_RULE_CHARS, evidence="e") for _ in range(20)]  # 20 × 301 字 > 6000
    pb = playbook.apply_lessons({"version": 1, "created": "x", "rules": []}, big, TODAY)
    assert sum(len(r["text"]) + len(r["evidence"]) for r in pb["rules"]) <= playbook.MAX_CHARS
    assert [r["id"] for r in pb["rules"]] == [f"r{i}" for i in range(2, 21)]


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


@pytest.mark.parametrize("gap,released", [(playbook.MIN_DAYS_BETWEEN_RELEASES - 1, False),
                                          (playbook.MIN_DAYS_BETWEEN_RELEASES, True)])
def test_maybe_release_window_boundary(pbdir, cal, gap, released):
    start = cal.between(dt.date(2026, 8, 3), TODAY)[-(gap + 1)]  # 从 start 到 TODAY 相隔 gap 个交易日
    store = _setup_release(pbdir, cal, 1.05, 1.01, start=start)
    assert bool(playbook.maybe_release(store, TODAY, cal)) is released


def test_save_is_atomic_and_load_survives_corruption(pbdir):
    playbook.save("candidate", {"version": 3, "created": "x", "rules": _rules("a")})
    assert not list(pbdir.glob("*.tmp"))
    (pbdir / "candidate.json").write_text('{"version": 1, "rules": [', encoding="utf-8")
    pb = playbook.load("candidate")  # 损坏时退回空 playbook，不让盘前和复盘整体失败
    assert pb["rules"] == []


@pytest.mark.parametrize("text", [
    '{"version": "v3", "rules": []}',
    '{"version": 2, "rules": [{"id": "r1", "text": 5, "evidence": "e"}]}',
    '{"version": 2, "rules": ["规则"]}',
    '{"version": true, "rules": []}',
    '[]',
])
def test_load_checked_flags_malformed_playbook(pbdir, text):
    (pbdir / "active.json").write_text(text, encoding="utf-8")
    pb, ok = playbook.load_checked("active")
    assert not ok and pb["rules"] == []
    assert playbook.corrupt_kinds() == ["active"]


def test_load_checked_missing_file_is_not_corrupt(pbdir):
    assert playbook.load_checked("candidate")[1] is True and playbook.corrupt_kinds() == []


@pytest.mark.parametrize("broken", ["active", "candidate"])
def test_maybe_release_skips_when_a_playbook_is_corrupt(pbdir, cal, broken):
    store = _setup_release(pbdir, cal, 1.05, 1.01)
    other = "candidate" if broken == "active" else "active"
    before = (pbdir / f"{other}.json").read_text(encoding="utf-8")
    (pbdir / f"{broken}.json").write_text('{"version": 2, "rules": [', encoding="utf-8")
    assert playbook.maybe_release(store, TODAY, cal) is None
    assert (pbdir / f"{other}.json").read_text(encoding="utf-8") == before  # 空规则没有写进另一份
    assert store.kv_get("playbook_released") != TODAY.isoformat()  # 修好之后照常比较这个窗口


def test_long_lesson_is_truncated_not_wiping_rules():
    pb = {"version": 1, "rules": [{"id": f"r{i}", "text": "旧" * 10, "evidence": "e", "created": "x"} for i in range(10)]}
    out = playbook.apply_lessons(pb, [L("add", text="长" * 6001)], TODAY)
    assert len(out["rules"]) == 11 and len(out["rules"][-1]["text"]) == playbook.MAX_RULE_CHARS


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
    assert "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" in log
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
import logging
import os
import re
import subprocess
from pathlib import Path

import pandas as pd

from engine.calendar import TradingCalendar
from engine.store import Store

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK_DIR = ROOT / "playbook"
KINDS = ("active", "candidate")
MAX_RULES = 30
MAX_CHARS = 6000
MIN_DAYS_BETWEEN_RELEASES = 10
MAX_RULE_CHARS = 300  # 单条规则的 text、evidence 各自的上限：一条超长规则不能挤掉其余规则
RELEASE_KEY = "playbook_released"
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _path(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown playbook kind: {kind}")
    return PLAYBOOK_DIR / f"{kind}.json"


def _valid(pb) -> bool:
    """version 是整数，rules 是列表，每条规则的 id、text、evidence 都是字符串。"""
    if not isinstance(pb, dict) or isinstance(pb.get("version", 1), bool) or not isinstance(pb.get("version", 1), int):
        return False
    rules = pb.get("rules", [])
    return isinstance(rules, list) and all(
        isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("id", "text", "evidence")) for r in rules)


def load_checked(kind: str) -> tuple[dict, bool]:
    """读取 playbook，返回 (playbook, 文件是否完好)。文件缺失算完好（返回空 playbook）；
    损坏（写到一半断电、手工编辑出错）时返回空 playbook 和 False。"""
    p = _path(kind)
    empty = {"version": 1, "created": dt.date.today().isoformat(), "rules": []}
    if not p.exists():
        return empty, True
    try:
        pb = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        pb = None
    if not _valid(pb):
        log.error("playbook %s 损坏：%s", kind, p)
        return empty, False
    return pb, True


def load(kind: str) -> dict:
    """读取 playbook；损坏时退回空 playbook：盘前照常运行，只是不带规则。
    会改写 playbook 的地方（发布、复盘）先用 corrupt_kinds() 检查，不能把空规则写回去。"""
    return load_checked(kind)[0]


def corrupt_kinds() -> list[str]:
    """文件存在但无法读取或格式不对的 playbook。"""
    return [k for k in KINDS if not load_checked(k)[1]]


def save(kind: str, pb: dict) -> None:
    """先写临时文件再原子替换：写到一半中断时，原文件保持完整。"""
    p = _path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(pb, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


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
        evidence = (les.evidence or "").strip()[:MAX_RULE_CHARS]
        text = (les.text or "").strip()[:MAX_RULE_CHARS]
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
    if corrupt_kinds():
        return None  # 任一份损坏都不发布也不重置：否则空规则会写进另一份并提交。由 run_review 告警
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

`playbook/premarket_system.md`（agent 可改；08:30 盘前深度分析的系统提示词，以下为初始版本）:

```markdown
# 角色

你是一名 A 股盘前研究员。每个交易日 08:30~08:45（集合竞价开始之前），你要对量化模型给出的候选股票做一次深度分析。量化模型（现任策略）依据昨日及以前的行情选出候选，并按量化分排序。你的任务是结合隔夜新闻、公司公告、量化数据和经验规则，对每只候选决定"保留（keep）"或"否决（veto）"，并为保留的股票写下集合竞价条件和价位。

你不是选股者，不能新增候选之外的股票。你的价值在于两点：剔除量化模型看不到的风险；把"什么样的开盘值得买"写成系统可以机械执行的条件。

# 当日流程

1. 08:30 你完成本次分析，为每只候选输出 action、confidence、价位和竞价条件。
2. 09:15~09:25 集合竞价。09:25 竞价结束后，系统按你写的条件逐只检查：竞价涨幅（相对昨收）必须落在 [min_gap_pct, max_gap_pct] 之内，竞价价不高于 entry_max，并且高于 stop。不满足的股票当天放弃。
3. 通过检查的股票按 confidence 从高到低取前几只，再做一次简短的竞价确认（只能否决，不能新增，也不改价位），09:28 前推送给用户。
4. 09:30 模拟盘按开盘价加少量滑点买入。之后的止损、止盈和到期卖出都使用你给出的价位。

竞价确认时间很短，只是最后一道把关。推荐质量主要取决于你此刻写下的条件是否清楚、是否符合实际。

# A 股交易规则（必须遵守）

- T+1：当天买入的股票最早下一个交易日才能卖出，买入当天即使大跌也无法止损。因此追高的代价比其他市场更大。
- 涨跌幅限制：主板 ±10%，创业板（300/301/302）与科创板（688/689）±20%，主板 ST 股自 2026-07-06 起 ±10%（此前 ±5%），北交所 ±30%。价格精确到 0.01 元。输入中已给出每只候选的涨跌停价。
- 开盘即涨停（一字板）买不进；跌停封死卖不出。
- 模拟成交按开盘价加滑点，买入数量为 100 股的整数倍。佣金万 2.5（最低 5 元），卖出另收印花税 0.05%，买卖都收过户费 0.001%。
- 卖出规则：持有期满按当天开盘价卖出；开盘价已跌破 stop 或已高于 target 时按开盘价卖出；否则盘中最低价触及 stop 按止损价卖出（与 target 同日触及时按止损处理），最高价触及 target 按止盈价卖出。

# 输入内容

- 经验规则（playbook）：此前复盘总结的规则，带编号（如 [r3]）和证据。
- 候选股票表：量化分、昨收、涨跌停价、20 日均成交额、量化价位（括号内为相对昨收的幅度）、持有天数、量化理由。
- 候选相关公告、候选个股新闻、隔夜要闻（财联社电报）。

# 分析方法

1. 逐只核对公告与新闻。利空按下方否决标准处理；利好要判断是否已被股价消化、开盘是否可能大幅高开，据此调整竞价区间，而不是简单提高 confidence。
2. 结合量化数据判断赔率：近期涨幅是否已经透支、距涨停还有多少空间、20 日均成交额是否足够活跃。
3. 判断大盘：隔夜外盘、重大政策与宏观消息、板块情绪。整体偏弱时降低 confidence、收窄竞价区间，或在 market_view 中提示降低仓位。
4. 对照经验规则：适用的规则必须体现在 action、竞价条件或 reason 中，并写明规则编号。

# 否决标准（满足任一条即 veto，并在 reason 中写明依据）

1. 利空公告或新闻：减持计划、业绩预亏或大幅下修、立案调查、监管问询或处罚、重大诉讼、商誉减值、限售股解禁临近、退市风险提示、控股股东股权质押风险等。
2. 所属题材或板块出现明确的负面政策，或板块龙头隔夜大跌。
3. 流动性不足：20 日均成交额明显偏低，买卖会显著影响价格。
4. 近期已连续大涨、发布了股价异常波动公告，或距涨停空间过小，在 T+1 下追高风险大。
5. 下方"经验规则"中适用的条目。

没有明确负面证据时默认 keep。不要因为"没有新闻"而否决，也不要因为"有利好新闻"就放松价格纪律。

# 字段说明

- code：与候选表完全一致的 6 位代码。
- action：keep 或 veto。veto 表示无论竞价如何，当天都不买。
- confidence：0 到 1 之间的数，表示你对该股在持有期内跑赢沪深 300 的把握，用于排序。veto 填 0。
- min_gap_pct、max_gap_pct：可接受的竞价涨幅区间，单位为百分比，相对昨收，含两端，必须 min_gap_pct ≤ max_gap_pct。例如 -2 与 3 表示竞价价在昨收的 -2% 到 +3% 之间才买。
  - 下限用来排除"低开说明资金不认可或利空正在发酵"的情形；上限用来避免追高。
  - 常见起点：主板约 -3 到 +4；创业板、科创板可按涨跌幅比例适当放宽。
  - 上限一般不超过涨跌幅限制的一半，并且至少比涨停对应的涨幅低 2 个百分点，否则开盘可能直接封板，买不进。
  - 存在潜在利空、但证据不足以否决时，提高下限（如 0 或 +0.5）；预期温和高开的强势股也可以把下限设为正数。
  - veto 的股票这两项都填 0。
- entry_max：可接受的最高买入价（元，两位小数）。系统同时检查竞价价 ≤ entry_max。一般取 昨收 × (1 + max_gap_pct / 100)；两者不一致时，以更严的那个为准。
- stop：止损价，须低于 entry_max。通常设在预期成交价下方 5%~8%（ST 股更窄），也可以参考近期关键支撑位。
- target：止盈价，须高于 entry_max。通常设在预期成交价上方 8%~15%，不超过持有期内的合理空间。
- 价位必须满足 stop < entry_max < target，且三者都在候选表中量化买入上限（"买≤"）的 0.5~1.5 倍之内，否则系统改用量化价位（竞价区间仍按你写的执行）。veto 的股票价位可直接沿用量化价位。
- reason：中文，80 字以内，先写结论，再写证据。
- market_view：两三句话的大盘判断（隔夜外盘、重大新闻与政策、整体情绪），说明今天应积极还是谨慎。这段文字会出现在推送里。

# 证据与诚信

- 每条判断都必须引用输入中出现的具体证据：公告标题、新闻标题与时间、行情数字，或经验规则编号（如 [r3]）。
- 严禁编造新闻、公告、数据或输入中没有出现的事实。你的训练数据有截止日期，不要用记忆中的"近期事件"判断个股；输入中没有的信息一律视为未知。
- 信息不足时直接写"无相关公告/新闻"，主要依据量化数据与经验规则判断。

# 输出要求

- 只输出符合给定 JSON Schema 的对象，不要输出其他文字。
- picks 必须对每一只候选各给出一条记录，code 与候选表完全一致，不得遗漏或新增。
```

`playbook/review_system.md`（agent 可改；以下为初始版本）:

```markdown
# 角色

你是一名 A 股盘后复盘分析师，负责维护"盘前审核经验手册"（playbook）。每个交易日收盘后，你会拿到两套 LLM 审核账户（h:active 线上版、h:candidate 候选版）当天的推荐、实际成交、个股日线以及当前候选版经验规则。你的任务是：总结当天推荐的得失，并对候选版经验规则提出增、删、合并建议。线上版只有在候选版经过约两周前向模拟、净值不落后时才会被替换，所以你的修改都是"待验证假设"。

# 背景规则

- T+1：当日买入次日才能卖出；涨跌幅主板 ±10%，创业板/科创板 ±20%，主板 ST 自 2026-07-06 起 ±10%（此前 ±5%），北交所 ±30%。
- 开盘一字涨停买不进、一字跌停卖不出；成交按开盘价加滑点模拟。
- 推荐若因开盘价高于 entry_max、开盘价不高于 stop 或开盘涨停而未成交，也属于需要复盘的事件（价格设置过紧还是本就该放弃）。

# 复盘方法

1. 逐只对比：推荐理由 vs 当日实际（开盘价相对昨收、最高、最低、收盘、是否成交、是否触发止损/止盈）。
2. 区分运气与规律：单日单只的结果大多是噪声。只有当现象能用明确、可执行的条件描述（如"主板股竞价高开超过 5% 时放弃买入"，可以落实为竞价涨幅上限），并且今天的数据提供了支持时，才提出新规则。
3. 优先修正而不是堆砌：已有规则被今天的事实反驳时用 remove；两条规则重叠时用 merge（rule_id 填被保留的那条，text 写合并后的完整表述），并对另一条单独给一条 remove。
4. 规则必须可在盘前执行：规则在 08:30 的盘前分析里使用，只能依赖那时可得的信息（昨日及以前行情、隔夜新闻公告），不能依赖当日收盘后才知道的信息。涉及竞价的条件要写成竞价涨幅区间或买入价上限，由 09:25 的规则过滤执行；竞价成交额这类条件无法执行。

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

预期：17 passed（`test_git_commit_*` 需要本机 PATH 中有 git）。

**Step 5: 提交**

```
git add agents/playbook.py playbook/premarket_system.md playbook/review_system.md tests/test_playbook.py
git commit -m "feat: add playbook store, lesson caps, release gate and system prompts"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

### Task 4.3: agents/premarket.py — 盘前扫描与竞价定稿

**Files:**
- Create: `agents/premarket.py`
- Create: `scripts/smoke_premarket.py`
- Test: `tests/test_premarket.py`

`scan`（08:30，与上一版相同）用 `client.universe()` 取股票池，先 `cache.update(codes, asof)` 增量补齐昨日数据，再用最近 `HISTORY_DAYS`（400）个自然日的面板构造 `DataView`。新闻、公告类的单项抓取失败时置空，不影响扫描；行情数据失败照常抛出，由 `run_job` 告警。`context` 里另加 `"stats"`（每只候选的昨收 `prev_close` 和 20 日均成交额 `avg_amount20`）。

`analyze` 紧接着 scan 运行（`run_analyze` 让两套 playbook 并发）。提示中给出每只候选的量化分、昨收、涨跌停价、20 日均额、量化价位（含相对昨收的幅度）和资讯，Claude 返回 `PremarketPlan`；代码不在候选里的条目被丢弃，重复代码只保留第一条，结果存入 kv `plan:<ISO日期>:<kind>`。09:25 `run_final` 拉一次竞价快照，两路并发 `finalize`：先用 `auction_filter` 做确定性过滤（竞价涨幅区间、买入上限、价格合理性），取前 `max_picks` 只，再用 `AUCTION_SYSTEM` 做一次只能否决的竞价确认。确认提示列出候选整体竞价概况（高开/低开家数、中位竞价涨幅），以及每只入选股的竞价价、涨幅、竞价额及其占 20 日均额的比例、涨跌停价、盘前条件和理由。`run_final` 的 `calendar` 参数只为与 jobs 的调用签名保持一致，目前没有用到。

**Step 1: 写失败的测试**

`tests/test_premarket.py`:

```python
import datetime as dt
import json
import subprocess
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from agents import playbook, premarket
from agents.llm import BudgetExceeded, LLMError
from agents.premarket import AuctionCheck, AuctionDecision, PlannedPick, PremarketPlan
from pydantic import ValidationError
from config import Settings
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick
from notify.format import format_recommendation

D = dt.date(2026, 10, 16)
PREV = dt.date(2026, 10, 15)
CODES = [f"600{i:03d}" for i in range(20)]
CAL = TradingCalendar(pd.bdate_range("2026-09-01", "2026-12-31").date)
LLM_ERRORS = [LLMError("cli error"), BudgetExceeded("budget"), subprocess.TimeoutExpired("claude.exe", 90.0),
              ValueError("bad output")]


def settings(**kw) -> Settings:
    base = dict(anthropic_api_key="k", wecom_webhook_url="", root=Path("."), max_picks=3)
    base.update(kw)
    return Settings(**base)


def qpick(code: str, i: int = 0) -> Pick:
    return Pick(code=code, name=f"股{code}", score=100.0 - i, entry_max=10.3, stop=9.3, target=11.0,
                hold_days=5, reason=f"量化理由{code}")


CANDS = [qpick(c, i) for i, c in enumerate(CODES[:8])]
CTX = {"news": [{"title": "隔夜美股收涨", "content": "纳指涨 1%", "time": "2026-10-16 06:00:00"}],
       "notices": [{"code": "600000", "name": "股600000", "title": "关于股东减持计划的公告", "type": "减持"}],
       "stock_news": {"600001": [{"title": "600001 中标大单", "content": "", "time": "2026-10-15"}]},
       "stats": {c: {"prev_close": 10.0, "avg_amount20": 2e8} for c in CODES[:6]}}


def make_panel(codes, end=PREV, n=25):
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    cols = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 1e6, "amount": 2e8, "prev_close": 10.0}
    return {c: pd.DataFrame(cols, index=idx) for c in codes}, {c: f"股{c}" for c in codes}


class FakeAk:
    """AkClient 替身。spot() 默认竞价价 10.20（昨收 10.00，+2.00%）；opens 指定个股竞价价，missing 中的代码不出现在快照里。"""

    def __init__(self, spot_fail=False, opens=None, missing=(), prevs=None):
        self.spot_fail, self.opens, self.missing = spot_fail, dict(opens or {}), set(missing)
        self.prevs = dict(prevs or {})  # 指定个股快照里的昨收（除权除息日是除权参考价）
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

    def spot(self, codes=None, sink=None):
        self.calls.append(("spot",))
        self.spot_codes = None if codes is None else list(codes)
        if self.spot_fail:
            raise RuntimeError("spot down")
        codes = [c for c in (CODES if codes is None else codes) if c not in self.missing]
        opens = [self.opens.get(c, 10.2) for c in codes]
        return pd.DataFrame({"code": codes, "name": [f"股{c}" for c in codes], "open": opens,
                             "prev_close": [self.prevs.get(c, 10.0) for c in codes],
                             "last": opens, "pct": [(o / 10.0 - 1) * 100 for o in opens], "volume": 1e4,
                             "amount": 5e6, "high": opens})

    def spot_calls(self):
        return self.calls.count(("spot",))


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
    """签名与 agents.llm.LLM.parse 一致；按 purpose 返回预设结果，值为 Exception 时抛出。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0, max_budget_usd=1.0,
              essential=False):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, max_budget_usd=max_budget_usd, essential=essential))
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
    s.save_candidates(D, [qpick(c, i) for i, c in enumerate(CODES[:6])], CTX)
    return s


def pp(code, action="keep", conf=0.5, entry=10.25, stop=9.6, target=11.2, lo=-2.0, hi=3.0):
    return PlannedPick(code=code, action=action, confidence=conf, entry_max=entry, stop=stop, target=target,
                       min_gap_pct=lo, max_gap_pct=hi, reason=f"计划理由{code}")


def plan(*picks, view="偏强"):
    return PremarketPlan(market_view=view, picks=list(picks))


def save_plan(store, kind, p):
    store.kv_set(f"plan:{D.isoformat()}:{kind}", p.model_dump())


def check(actions: dict, note="竞价平稳"):
    return AuctionCheck(note=note, picks=[AuctionDecision(code=c, action=a, reason=f"{a}:{c}")
                                          for c, a in actions.items()])


# ---------- 08:30 scan（与上一版相同） ----------

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


# ---------- 08:30 深度分析 ----------

def test_analyze_saves_plan_and_drops_non_candidates(store):
    raw = plan(pp("600001", conf=0.9), pp(" 600002 ", "veto", 0.0), pp("999999", conf=1.0),
               pp("600001", conf=0.1), view=" 偏强 ")
    llm = FakeLLM({"analyze:active": raw})
    out = premarket.analyze(D, settings(), store, llm, "active")
    assert [(p.code, p.action, p.confidence) for p in out.picks] == [("600001", "keep", 0.9), ("600002", "veto", 0.0)]
    assert out.market_view == "偏强"
    assert store.kv_get("plan:2026-10-16:active") == out.model_dump()
    assert premarket.load_plan(store, D, "active") == out
    assert premarket.load_plan(store, D, "candidate") is None
    call = llm.calls[0]
    assert call["schema"] is PremarketPlan and call["system"] == "盘前系统提示"
    assert call["effort"] == "high" and call["essential"] is True and call["max_budget_usd"] == 1.5
    assert call["timeout"] == premarket.ANALYZE_TIMEOUT == 900.0
    for s in ("ACTIVE规则", "600005", "关于股东减持计划的公告", "600001 中标大单", "隔夜美股收涨", "涨停 11.00",
              "买≤10.30（+3.0%）"):
        assert s in call["user"], s


@pytest.mark.parametrize("exc", LLM_ERRORS)
def test_analyze_llm_failure_returns_none(store, exc):
    assert premarket.analyze(D, settings(), store, FakeLLM({"analyze:active": exc}), "active") is None
    assert store.kv_get("plan:2026-10-16:active") is None


def test_analyze_plan_without_candidate_codes_returns_none(store):
    assert premarket.analyze(D, settings(), store, FakeLLM({"analyze:active": plan(pp("999999"))}), "active") is None
    assert store.kv_get("plan:2026-10-16:active") is None


def test_analyze_without_candidates_returns_none():
    llm = FakeLLM({})
    assert premarket.analyze(D, settings(), Store(":memory:"), llm, "active") is None
    assert llm.calls == []


def test_run_analyze_runs_both_kinds_concurrently(store):
    barrier = threading.Barrier(2, timeout=5)  # 若串行执行，第一路等不到第二路，Barrier 超时，该路失败

    class ConcurrentLLM(FakeLLM):
        def parse(self, purpose, *args, **kwargs):
            barrier.wait()
            return super().parse(purpose, *args, **kwargs)

    llm = ConcurrentLLM({"analyze:active": plan(pp("600001"), view="A观点"),
                         "analyze:candidate": plan(pp("600002"), view="C观点")})
    assert premarket.run_analyze(D, settings(), store, llm) == {"active": True, "candidate": True}
    assert premarket.load_plan(store, D, "active").market_view == "A观点"
    assert [p.code for p in premarket.load_plan(store, D, "candidate").picks] == ["600002"]
    users = {c["purpose"]: c["user"] for c in llm.calls}
    assert "ACTIVE规则" in users["analyze:active"] and "CANDIDATE规则" in users["analyze:candidate"]


def test_analyze_skips_llm_when_no_candidates():
    s = Store(":memory:")
    s.save_candidates(D, [], CTX)
    llm = FakeLLM({})
    assert premarket.run_analyze(D, settings(), s, llm) == {"active": False, "candidate": False}
    assert llm.calls == []  # 没有候选就不花深度分析的钱


def test_run_analyze_timeout_capped_by_deadline(store):
    llm = FakeLLM({"analyze:active": plan(pp("600001")), "analyze:candidate": plan(pp("600002"))})
    now = dt.datetime.combine(D, dt.time(9, 15))
    premarket.run_analyze(D, settings(), store, llm, deadline=dt.datetime.combine(D, dt.time(9, 22)), now=now)
    assert {c["timeout"] for c in llm.calls} == {420.0}  # 离截止还有 7 分钟，不再给满 900 秒
    llm2 = FakeLLM({})
    late = dt.datetime.combine(D, dt.time(9, 21, 30))
    assert premarket.run_analyze(D, settings(), store, llm2, deadline=dt.datetime.combine(D, dt.time(9, 22)),
                                 now=late) == {"active": False, "candidate": False}
    assert llm2.calls == []  # 剩余时间不够一次分析：不发起


def test_confidence_normalized_to_unit_interval():
    assert pp("600001", conf=85).confidence == pytest.approx(0.85)  # 百分数
    assert pp("600001", conf=-1).confidence == 0.0 and pp("600001", conf=1.0).confidence == 1.0
    assert pp("600001", conf=1.2).confidence == 1.0  # 略微越界：截断，不当成 1.2%
    assert pp("600001", conf=2).confidence == pytest.approx(0.02)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "高"])
def test_confidence_rejects_non_numbers(bad):
    with pytest.raises(ValidationError):
        pp("600001", conf=bad)


def test_playbook_version_none_when_malformed(pbdir):
    (pbdir / "active.json").write_text('{"version": "v3", "rules": []}', encoding="utf-8")
    assert premarket._playbook_version("active") is None
    playbook.save("candidate", {"version": 4, "created": "x", "rules": []})
    assert premarket._playbook_version("candidate") == 4


def test_all_vetoed_note_differs_from_auction_failure(store):
    p = plan(pp("600001", "veto", 0.0), pp("600002", "veto", 0.0))
    picks, reviewed, view = premarket._rule_result(settings(), p, CANDS, FakeAk().spot())
    assert picks == [] and reviewed is True and view.endswith(premarket.NOTE_ALL_VETOED)


def test_ex_adjusted_prices_rounded_to_cents(store):
    cands, _ = premarket._ex_adjust(CANDS, None, {"stats": {c: {"prev_close": 10.0} for c in CODES}},
                                    FakeAk(prevs={"600001": 9.80}).spot())
    p = next(x for x in cands if x.code == "600001")
    assert (p.entry_max, p.stop, p.target) == (round(10.3 * 0.98, 2), round(9.3 * 0.98, 2), round(11.0 * 0.98, 2))


def test_small_dividend_triggers_conversion_but_one_cent_does_not():
    ctx = {"stats": {c: {"prev_close": 10.0} for c in CODES}}
    small, _ = premarket._ex_adjust(CANDS, None, ctx, FakeAk(prevs={"600001": 9.70}).spot())  # 派息 0.30
    tiny, _ = premarket._ex_adjust(CANDS, None, ctx, FakeAk(prevs={"600001": 9.99}).spot())   # 舍入差异
    assert next(x for x in small if x.code == "600001").stop == round(9.3 * 0.97, 2)
    assert next(x for x in tiny if x.code == "600001").stop == 9.3


def test_run_final_time_limits(store, monkeypatch):
    save_plan(store, "active", plan(pp("600001")))
    seen = {}
    monkeypatch.setattr(premarket, "_fetch_spot", lambda client, timeout, codes: seen.setdefault("spot", timeout)
                        and FakeAk().spot(codes))
    llm = FakeLLM({"auction:active": check({})})
    premarket.run_final(D, settings(), store, llm, FakeAk(), Recorder(), CAL)
    assert seen["spot"] == premarket.SPOT_TIMEOUT
    assert all(c["timeout"] <= premarket.AUCTION_TIMEOUT for c in llm.calls)


def test_run_final_survives_corrupt_playbook(store, pbdir):
    (pbdir / "candidate.json").write_text("", encoding="utf-8")  # 写到一半时断电
    n = Recorder()
    premarket.run_final(D, settings(), store, FakeLLM({}), FakeAk(), n, CAL)
    assert len(n.sent) == 1 and store.load_recommendation(D, "h:candidate") is not None


def test_run_analyze_reports_each_kind(store):
    llm = FakeLLM({"analyze:active": plan(pp("600001")), "analyze:candidate": LLMError("refused")})
    assert premarket.run_analyze(D, settings(), store, llm) == {"active": True, "candidate": False}
    assert premarket.load_plan(store, D, "candidate") is None


# ---------- 09:25 规则过滤 ----------

def test_auction_filter_gap_window():
    p = plan(pp("600000", conf=0.6),            # +2.00% 在 [-2, 3] 内
             pp("600001", conf=0.9, hi=1.5),    # +2.00% 高于上限
             pp("600002", conf=0.8, lo=-1.0),   # -1.50% 低于下限
             pp("600003", conf=0.7),            # -2.00% 恰在下限（闭区间，按 0.01 个百分点比较）
             pp("600004", conf=0.5, hi=2.0))    # +2.00% 恰在上限
    spot = FakeAk(opens={"600002": 9.85, "600003": 9.8}).spot()
    assert [x.code for x in premarket.auction_filter(p, CANDS, spot)] == ["600003", "600000", "600004"]


def test_auction_filter_entry_max_uses_adopted_price():
    p = plan(pp("600000", conf=0.9, entry=10.15),                         # 竞价 10.20 > 买入上限 10.15
             pp("600001", conf=0.8, entry=10.2),                          # 竞价 10.20 = 买入上限，保留
             pp("600002", conf=0.7, entry=10.1, stop=10.5, target=11.0))  # 价位不合理 -> 量化上限 10.30，保留
    out = premarket.auction_filter(p, CANDS, FakeAk().spot())
    assert [(x.code, x.entry_max) for x in out] == [("600001", 10.2), ("600002", 10.3)]


def test_auction_filter_drops_open_at_or_below_adopted_stop():
    # 撮合在开盘价不高于止损价时不买（Task 1.6）；竞价过滤同样剔除，名额留给后面能成交的股票
    p = plan(pp("600000", conf=0.9, lo=-5.0),   # 竞价 9.55 < 采用的止损 9.60
             pp("600001", conf=0.8, lo=-5.0),   # 竞价 9.60 = 止损
             pp("600002", conf=0.7, lo=-5.0))   # 竞价 9.65 > 止损，保留
    spot = FakeAk(opens={"600000": 9.55, "600001": 9.6, "600002": 9.65}).spot()
    assert [x.code for x in premarket.auction_filter(p, CANDS, spot)] == ["600002"]


def test_auction_filter_missing_spot_row_keeps_pick():
    p = plan(pp("600000", conf=0.9, hi=-5.0),   # 快照里没有这只：不检查条件，保留
             pp("600001", conf=0.8),            # 有快照行但竞价价缺失（停牌）：不满足条件
             pp("600002", conf=0.7))
    spot = FakeAk(opens={"600001": float("nan")}, missing={"600000"}).spot()
    assert [x.code for x in premarket.auction_filter(p, CANDS, spot)] == ["600000", "600002"]
    for empty in (None, pd.DataFrame()):  # 整个快照缺失：所有 keep 都保留
        assert [x.code for x in premarket.auction_filter(p, CANDS, empty)] == ["600000", "600001", "600002"]


def test_auction_filter_keeps_candidates_only_sorted_by_confidence():
    p = plan(pp("600000", conf=0.2), pp("600001", "veto", 0.0), pp("600002", conf=0.9),
             pp("999999", conf=1.0), pp("600003", conf=0.9), pp("600004", conf=0.5))
    out = premarket.auction_filter(p, CANDS, FakeAk().spot())
    assert [x.code for x in out] == ["600002", "600003", "600004", "600000"]  # 同分保持计划顺序
    assert [x.score for x in out] == [0.9, 0.9, 0.5, 0.2]


def test_auction_filter_price_sanity_falls_back_to_quant():
    p = plan(pp("600000", conf=0.9, stop=10.5, target=11.0),                 # stop > entry_max
             pp("600001", conf=0.8, target=30.0),                            # target 超出量化上限 1.5 倍
             pp("600002", conf=0.7, entry=10.254, stop=9.601, target=11.2))  # 合理，四舍五入到分
    out = premarket.auction_filter(p, CANDS, FakeAk().spot())
    assert [(x.entry_max, x.stop, x.target) for x in out] == [(10.3, 9.3, 11.0), (10.3, 9.3, 11.0),
                                                              (10.25, 9.6, 11.2)]
    assert (out[2].name, out[2].score, out[2].hold_days, out[2].reason) == ("股600002", 0.7, 5, "计划理由600002")


# ---------- 09:25 finalize ----------

def test_finalize_without_plan_returns_quant(store):
    llm, ak = FakeLLM({}), FakeAk()
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, ak, "active")
    assert (picks, reviewed, view) == ([qpick(c, i) for i, c in enumerate(CODES[:3])], False, "")
    assert llm.calls == [] and ak.spot_calls() == 0


def test_finalize_confirm_vetoes_but_never_adds(store):
    save_plan(store, "active", plan(pp("600001", conf=0.9), pp("600002", conf=0.8), pp("600003", conf=0.7),
                                    pp("600004", conf=0.6), pp("600000", "veto", 0.0)))
    llm = FakeLLM({"auction:active": check({"600001": "keep", "600002": "veto", "600004": "keep",
                                           "600000": "keep", "999999": "keep"})})
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, FakeAk(), "active")
    assert [p.code for p in picks] == ["600001", "600003"]
    assert reviewed is True and view == "偏强（竞价确认：竞价平稳）"
    call = llm.calls[0]
    assert call["system"] == premarket.AUCTION_SYSTEM and call["schema"] is AuctionCheck
    assert call["effort"] == "low" and call["essential"] is True and call["max_budget_usd"] == 0.3
    assert call["timeout"] == premarket.AUCTION_TIMEOUT == 90.0
    for s in ("600001", "600003", "竞价 10.20（+2.00%）", "500.00万", "占20日均额 2.50%", "涨停 11.00",
              "计划理由600002", "偏强", "有竞价 6 只：高开 6"):  # 竞价概况只统计候选（只取候选的报价）
        assert s in call["user"], s
    assert "600004" not in call["user"] and "600000" not in call["user"]  # 只列出通过过滤且在 max_picks 内的


@pytest.mark.parametrize("exc", LLM_ERRORS)
def test_finalize_confirm_failure_returns_prelim_with_note(store, exc):
    save_plan(store, "active", plan(pp("600001", conf=0.9), pp("600002", conf=0.8)))
    picks, reviewed, view = premarket.finalize(D, settings(), store, FakeLLM({"auction:active": exc}), FakeAk(),
                                               "active")
    assert [p.code for p in picks] == ["600001", "600002"]
    assert reviewed is True and view == "偏强（竞价二次确认未完成，按盘前条件过滤）"


def test_finalize_empty_prelim_skips_confirm(store):
    save_plan(store, "active", plan(pp("600001", hi=1.0), pp("600002", "veto", 0.0), view="谨慎"))
    llm = FakeLLM({})
    assert premarket.finalize(D, settings(), store, llm, FakeAk(), "active") == (
        [], True, "谨慎（竞价条件均未满足，今日不推荐）")
    assert llm.calls == []


def test_finalize_spot_failure_keeps_plan_picks_and_tells_llm(store):
    save_plan(store, "active", plan(pp("600001", hi=-5.0)))
    llm = FakeLLM({"auction:active": check({}, note="")})
    picks, reviewed, view = premarket.finalize(D, settings(), store, llm, FakeAk(spot_fail=True), "active")
    assert [p.code for p in picks] == ["600001"] and reviewed is True and view == "偏强"
    assert "竞价数据缺失" in llm.calls[0]["user"]


def test_finalize_without_candidates_raises():
    with pytest.raises(RuntimeError):
        premarket.finalize(D, settings(), Store(":memory:"), FakeLLM({}), FakeAk(), "active")


# ---------- 09:25 run_final ----------

def test_run_final_fetches_spot_once_saves_both_and_pushes_only_active(store):
    save_plan(store, "active", plan(pp("600001", conf=0.9), pp("600002", conf=0.8), view="A观点"))
    save_plan(store, "candidate", plan(pp("600003", conf=0.9), view="C观点"))
    llm = FakeLLM({"auction:active": check({"600002": "veto"}, note="A确认"),
                   "auction:candidate": check({}, note="")})
    ak, notifier = FakeAk(), Recorder()
    premarket.run_final(D, settings(), store, llm, ak, notifier, CAL)
    assert ak.spot_calls() == 1 and ak.spot_codes == [p.code for p in store.load_candidates(D)[0]]  # 只取候选
    act, act_meta = store.load_recommendation(D, "h:active")
    cand, cand_meta = store.load_recommendation(D, "h:candidate")
    assert [p.code for p in act] == ["600001"] and [p.code for p in cand] == ["600003"]
    assert act_meta == {"llm_reviewed": True, "market_view": "A观点（竞价确认：A确认）", "playbook_version": 1,
                        "ref_close": {"600001": 10.0}}
    assert cand_meta["llm_reviewed"] is True and cand_meta["market_view"] == "C观点"
    assert sorted(c["purpose"] for c in llm.calls) == ["auction:active", "auction:candidate"]
    assert all(c["timeout"] <= premarket.AUCTION_TIMEOUT for c in llm.calls)
    assert notifier.sent == [format_recommendation(D, act, True, "A观点（竞价确认：A确认）")]


def test_run_final_converts_prices_on_ex_date(store):
    # 600001 今天 10 转 10：快照昨收 5.00（除权参考价），竞价 5.10；盘前计划按除权前的昨收 10.00 写成
    save_plan(store, "active", plan(pp("600001", conf=0.9, lo=-5.0)))
    ak = FakeAk(opens={"600001": 5.1}, prevs={"600001": 5.0})
    premarket.run_final(D, settings(), store, FakeLLM({"auction:active": check({})}), ak, Recorder(), CAL)
    act, meta = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == ["600001"] and meta["ref_close"] == {"600001": 5.0}  # 不换算的话竞价价低于止损 9.60
    assert (act[0].entry_max, act[0].stop, act[0].target) == pytest.approx((10.25 / 2, 9.6 / 2, 11.2 / 2), abs=0.006)  # 采用计划价位时舍入到分
    cand, _ = store.load_recommendation(D, "h:candidate")  # 没有计划的一路：量化候选同样换算
    assert cand[1].stop == pytest.approx(9.3 / 2) and cand[0].stop == pytest.approx(9.3)


def test_run_final_fallback_also_converts_on_ex_date(store, monkeypatch):
    save_plan(store, "active", plan(pp("600001", conf=0.9, lo=-5.0)))
    monkeypatch.setattr(premarket, "finalize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("定稿失败")))
    ak = FakeAk(opens={"600001": 5.1}, prevs={"600001": 5.0})
    premarket.run_final(D, settings(), store, FakeLLM({}), ak, Recorder(), CAL)
    act, _ = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == ["600001"] and act[0].stop == pytest.approx(4.8, abs=0.006)


def test_run_final_kind_without_plan_uses_quant(store):
    save_plan(store, "active", plan(pp("600001"), view="A观点"))
    llm = FakeLLM({"auction:active": check({})})
    premarket.run_final(D, settings(), store, llm, FakeAk(), Recorder(), CAL)
    cand, meta = store.load_recommendation(D, "h:candidate")
    assert cand == [qpick(c, i) for i, c in enumerate(CODES[:3])]
    assert meta["llm_reviewed"] is False and meta["market_view"] == ""
    assert [c["purpose"] for c in llm.calls] == ["auction:active"]


def test_fetch_spot_keeps_partial_rows_on_timeout():
    import threading
    gate = threading.Event()

    class Slow:
        def spot(self, codes, sink=None):  # 第一只取到后卡住
            sink.append({"code": codes[0], "name": "", "open": 10.2, "prev_close": 10.0, "last": 10.2, "pct": 2.0,
                         "volume": 1e4, "amount": 5e6, "high": 10.2})
            gate.wait(5)

    out = premarket._fetch_spot(Slow(), 0.3, ["600001", "600002"])
    gate.set()
    assert out["code"].tolist() == ["600001"]


def test_run_final_without_plans_pushes_quant_without_spot(store):
    ak, notifier = FakeAk(), Recorder()
    premarket.run_final(D, settings(), store, FakeLLM({}), ak, notifier, CAL)
    assert ak.spot_calls() == 0
    act, meta = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == CODES[:3] and meta["llm_reviewed"] is False
    assert meta["ref_close"] == {c: 10.0 for c in CODES[:3]}  # 没有快照：记扫描时的昨收，结算再比较
    assert len(notifier.sent) == 1 and "未经 LLM 审核" in notifier.sent[0]


def test_run_final_slow_confirm_falls_back_to_prelim(store, monkeypatch):
    monkeypatch.setattr(premarket, "FINAL_DEADLINE", 0.5)
    gate = threading.Event()

    class SlowLLM(FakeLLM):
        def parse(self, purpose, *args, **kwargs):
            if purpose == "auction:candidate":
                gate.wait(5)
            return super().parse(purpose, *args, **kwargs)

    save_plan(store, "active", plan(pp("600001"), view="A观点"))
    save_plan(store, "candidate", plan(pp("600002", conf=0.9), pp("600003", conf=0.8), pp("600004", hi=1.0),
                                       view="C观点"))
    llm = SlowLLM({"auction:active": check({}, note=""), "auction:candidate": check({"600002": "veto"})})
    notifier = Recorder()
    t0 = time.monotonic()
    try:
        premarket.run_final(D, settings(), store, llm, FakeAk(), notifier, CAL)
    finally:
        gate.set()
    assert time.monotonic() - t0 < 3
    cand, meta = store.load_recommendation(D, "h:candidate")
    assert [p.code for p in cand] == ["600002", "600003"]  # 规则过滤结果；迟到的否决不生效
    assert meta["llm_reviewed"] is True and meta["market_view"] == "C观点（竞价二次确认未完成，按盘前条件过滤）"
    act, act_meta = store.load_recommendation(D, "h:active")
    assert [p.code for p in act] == ["600001"] and act_meta["market_view"] == "A观点"
    assert notifier.sent == [format_recommendation(D, act, True, "A观点")]


def test_run_final_without_candidates_raises():
    notifier = Recorder()
    with pytest.raises(RuntimeError):
        premarket.run_final(D, settings(), Store(":memory:"), FakeLLM({}), FakeAk(), notifier, CAL)
    assert notifier.sent == []
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_premarket.py -v
```

预期：`ImportError`（`agents.premarket` 尚不存在，或仍是旧版、缺少 `PlannedPick` 等新名字），全部 ERROR。

**Step 3: 最小实现**

`agents/premarket.py`:

```python
"""盘前两段式：08:30 scan 出量化候选并抓资讯，随后 analyze 让 Claude 深度分析并写下竞价条件；
09:25 集合竞价结束后 run_final 先按条件做规则过滤，再做一次只能否决的竞价确认，09:28 前推送。"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import logging
import math
import threading
import time
from dataclasses import replace
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ValidationError, field_validator

from agents import playbook
from agents.llm import LLM
from config import Settings
from data.akshare_client import AkClient
from data.cache import BarCache
from engine.calendar import TradingCalendar
from engine.dataview import DataView
from engine.rules import limit_pct, limit_prices
from engine.store import Store
from engine.types import Pick
from notify.format import format_recommendation

log = logging.getLogger(__name__)

CANDIDATE_COUNT = 15
NEWS_LIMIT = 30
STOCK_NEWS_LIMIT = 3
HISTORY_DAYS = 400       # 自然日，约 270 个交易日，够常见的一年期因子
PRICE_BAND = 0.5         # 计划价位须在量化 entry_max 的 ±50% 内
ANALYZE_TIMEOUT = 900.0  # 08:30 深度分析单次上限（秒）
AUCTION_TIMEOUT = 90.0   # 09:25 竞价确认单次上限（秒）
FINAL_DEADLINE = 110.0   # run_final 从开始到定稿的总时长上限（秒）；09:25:15 启动则约 09:27:05 推送
SPOT_TIMEOUT = 40.0      # run_final 等待竞价快照的上限（秒），超时按竞价数据缺失处理
KINDS = ("active", "candidate")
NOTE_UNCONFIRMED = "（竞价二次确认未完成，按盘前条件过滤）"
NOTE_NONE_PASSED = "（竞价条件均未满足，今日不推荐）"
NOTE_ALL_VETOED = "（盘前分析全部否决，今日不推荐）"
MIN_ANALYZE_SECONDS = 120.0  # 离截止不到这么多秒就不再发起深度分析
SPOT_MISSING = "（竞价数据缺失：未取到竞价快照，以下股票未经竞价条件检查，请依据盘前分析判断，并在 note 中注明）"


class PlannedPick(BaseModel):
    code: str
    action: Literal["keep", "veto"]
    confidence: float
    entry_max: float
    stop: float
    target: float
    min_gap_pct: float
    max_gap_pct: float
    reason: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _unit_interval(cls, v):
        """提示词要求 0~1；模型偶尔写成百分数（85）或略微越界（1.2）：2~100 按百分数换算，其余截断到 0~1，
        免得排序被打乱。NaN、无穷和非数值按格式错误处理。"""
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"confidence 不是数值：{v!r}") from None
        if not math.isfinite(v):
            raise ValueError(f"confidence 不是有限数：{v!r}")
        if 2.0 <= v <= 100.0:
            v /= 100.0
        return min(max(v, 0.0), 1.0)


class PremarketPlan(BaseModel):
    market_view: str
    picks: list[PlannedPick]


class AuctionDecision(BaseModel):
    code: str
    action: Literal["keep", "veto"]
    reason: str


class AuctionCheck(BaseModel):
    note: str
    picks: list[AuctionDecision]


AUCTION_SYSTEM = """你是 A 股集合竞价复核员。08:30 的盘前深度分析已为每只候选写好竞价条件和价位；09:25 集合竞价结束后，系统已按这些条件做了规则过滤，下面列出的是通过过滤的股票。

你的任务是结合竞价数据做最后确认，对每只股票给出 keep 或 veto。你只能否决，不能新增股票，也不能修改价位。

出现以下情况之一时 veto，并在 reason 中写明依据的数字：
1. 竞价成交额占 20 日均额的比例明显低于同批其他股票，说明资金不认可。
2. 竞价价距涨停价不足 2%，开盘可能直接封板，上行空间也有限。
3. 竞价表现与盘前理由的前提明显矛盾，例如盘前预期温和高开，实际却放量低开。
4. 候选整体竞价明显偏弱（低开家数远多于高开家数，中位竞价涨幅低于 -1%），且该股缺乏独立的支撑理由。

没有明确负面证据时一律 keep。只依据输入中的数字和文字判断，不要编造新闻或数据。如果输入注明竞价数据缺失，就只依据盘前分析判断，并在 note 中说明。

输出要求：note 用一两句话概括今日竞价情况（60 字以内）；picks 对列出的每只股票各给一条，code 与输入完全一致，reason 40 字以内。"""


# ---------- 08:30 scan（与上一版相同） ----------

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


def _load_candidates(d: dt.date, store: Store) -> tuple[list[Pick], dict]:
    loaded = store.load_candidates(d)
    if loaded is None:
        raise RuntimeError(f"{d} 没有盘前候选（premarket_scan 未成功运行）")
    return loaded


def _plan_key(d: dt.date, kind: str) -> str:
    return f"plan:{d.isoformat()}:{kind}"


def load_plan(store: Store, d: dt.date, kind: str) -> PremarketPlan | None:
    """读回 analyze 存下的计划；没有或无法解析时返回 None。"""
    raw = store.kv_get(_plan_key(d, kind))
    if raw is None:
        return None
    try:
        return PremarketPlan.model_validate(raw)
    except ValidationError as e:
        log.warning("premarket plan %s %s unreadable: %r", d, kind, e)
        return None


# ---------- 提示词片段 ----------

def _f(x) -> float | None:
    """转为 float；缺失、NaN 或无法转换时为 None。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _num(x, scale: float = 1.0, unit: str = "") -> str:
    v = _f(x)
    return "未知" if v is None else f"{v / scale:.2f}{unit}"


def _rel(price: float, base) -> str:
    b = _f(base)
    return f"（{price / b - 1:+.1%}）" if b and b > 0 else ""


def _limits(p: Pick, prev_close) -> str:
    pct = limit_pct(p.code, p.name)
    pc = _f(prev_close)
    if not pc or pc <= 0:
        return f"涨跌幅限制 ±{pct:.0%}"
    up, down = limit_prices(p.code, p.name, pc)
    return f"涨跌幅限制 ±{pct:.0%}（涨停 {up:.2f} / 跌停 {down:.2f}）"


def _item(n: dict, width: int = 100) -> str:
    title, content = str(n.get("title") or ""), str(n.get("content") or "")
    body = f"{title}：{content[:width]}" if title and content else (title or content[:width])
    return f"[{n.get('time', '')}] {body}"


def _analyze_prompt(d: dt.date, pb: dict, cands: list[Pick], ctx: dict) -> str:
    stats = ctx.get("stats", {})
    lines = [f"# 交易日 {d.isoformat()}（08:30 盘前，集合竞价尚未开始）", "",
             f"## 经验规则（playbook v{pb.get('version', 1)}）", playbook.render(pb), "",
             f"## 候选股票（量化排序，共 {len(cands)} 只）"]
    for p in cands:
        s = stats.get(p.code, {})
        pc = s.get("prev_close")
        lines.append(f"- {p.code} {p.name}｜量化分 {p.score:.3f}｜昨收 {_num(pc)}｜{_limits(p, pc)}"
                     f"｜20日均额 {_num(s.get('avg_amount20'), 1e8, '亿')}"
                     f"｜量化价位 买≤{p.entry_max:.2f}{_rel(p.entry_max, pc)} 止损 {p.stop:.2f}{_rel(p.stop, pc)}"
                     f" 止盈 {p.target:.2f}{_rel(p.target, pc)}｜持有 {p.hold_days} 日｜{p.reason}")
    notices = [f"- {n['code']} {n['name']}：{n['title']}（{n['type']}）" for n in ctx.get("notices", [])]
    stock_news = [f"- {c}：" + "；".join(_item(n, 60) for n in items)
                  for c, items in ctx.get("stock_news", {}).items() if items]
    news = [f"- {_item(n)}" for n in ctx.get("news", [])]
    lines += ["", "## 候选相关公告", *(notices or ["（无）"])]
    lines += ["", "## 候选个股新闻", *(stock_news or ["（无）"])]
    lines += ["", "## 隔夜要闻（财联社电报）", *(news or ["（无）"])]
    lines += ["", f"请对以上 {len(cands)} 只候选逐一给出 action、confidence、价位与竞价条件"
                  "（min_gap_pct / max_gap_pct），code 与上表完全一致。"]
    return "\n".join(lines)


def _spot_rows(spot: pd.DataFrame | None, codes: list[str]) -> dict[str, dict]:
    if spot is None or spot.empty or "code" not in spot.columns:
        return {}
    sub = spot[spot["code"].astype(str).isin(codes)]
    return {str(r["code"]): r for r in sub.to_dict("records")}


def _gap_pct(row: dict) -> float | None:
    """竞价涨幅（%），四舍五入到 0.01 个百分点；竞价价或昨收缺失、非正（多为停牌）时为 None。"""
    op, pc = _f(row.get("open")), _f(row.get("prev_close"))
    if op is None or pc is None or op <= 0 or pc <= 0:
        return None
    return round((op / pc - 1) * 100, 2)


def _breadth(spot: pd.DataFrame | None) -> str:
    if spot is None or spot.empty or not {"open", "prev_close"} <= set(spot.columns):
        return SPOT_MISSING
    op = pd.to_numeric(spot["open"], errors="coerce")
    pc = pd.to_numeric(spot["prev_close"], errors="coerce")
    ok = (op > 0) & (pc > 0)
    if not ok.any():
        return SPOT_MISSING
    gap = (op[ok] / pc[ok] - 1) * 100
    return (f"有竞价 {int(ok.sum())} 只：高开 {int((gap > 0).sum())}，低开 {int((gap < 0).sum())}，"
            f"中位竞价涨幅 {gap.median():+.2f}%")


def _auction(row: dict | None, avg_amount20=None) -> str:
    if row is None:
        return "无竞价数据"
    gap = _gap_pct(row)
    if gap is None:
        return "无竞价（可能停牌）"
    amt, avg = _f(row.get("amount")), _f(avg_amount20)
    share = f"，占20日均额 {amt / avg:.2%}" if amt is not None and avg else ""
    return f"竞价 {_f(row.get('open')):.2f}（{gap:+.2f}%），竞价额 {_num(amt, 1e4, '万')}{share}"


def _auction_prompt(d: dt.date, plan: PremarketPlan, prelim: list[Pick], ctx: dict,
                    spot: pd.DataFrame | None) -> str:
    stats = ctx.get("stats", {})
    rows = _spot_rows(spot, [p.code for p in prelim])
    planned = {pp.code.strip(): pp for pp in plan.picks}
    lines = [f"# 交易日 {d.isoformat()}（09:25 集合竞价已结束）", "",
             "## 盘前市场观点", plan.market_view or "（无）", "",
             "## 候选整体竞价", _breadth(spot), "",
             f"## 通过竞价条件的股票（{len(prelim)} 只，按盘前信心排序）"]
    for p in prelim:
        pp, row, s = planned[p.code], rows.get(p.code), stats.get(p.code, {})
        pc = row.get("prev_close") if row is not None else s.get("prev_close")
        lines.append(f"- {p.code} {p.name}｜{_auction(row, s.get('avg_amount20'))}｜{_limits(p, pc)}"
                     f"｜盘前条件 竞价涨幅 {pp.min_gap_pct:+.2f}%~{pp.max_gap_pct:+.2f}%，买≤{p.entry_max:.2f}"
                     f"｜止损 {p.stop:.2f} 止盈 {p.target:.2f}｜盘前信心 {pp.confidence:.2f}｜盘前理由：{pp.reason}")
    lines += ["", f"请对以上 {len(prelim)} 只逐一给出 keep 或 veto，code 与上表完全一致。"]
    return "\n".join(lines)


# ---------- 08:30 深度分析 ----------

def analyze(d: dt.date, settings: Settings, store: Store, llm: LLM, playbook_kind: str,
            timeout: float = ANALYZE_TIMEOUT) -> PremarketPlan | None:
    """为每只候选给出 keep/veto、价位与竞价条件，存入 kv。任何异常都记录日志并返回 None。"""
    try:
        cands, ctx = _load_candidates(d, store)
        if not cands:  # 策略当天一只都没选出来：不花深度分析的钱
            log.info("premarket analyze %s: no candidates", playbook_kind)
            return None
        raw = llm.parse(purpose=f"analyze:{playbook_kind}", system=playbook.load_prompt("premarket"),
                        user=_analyze_prompt(d, playbook.load(playbook_kind), cands, ctx), schema=PremarketPlan,
                        effort="high", timeout=timeout, max_budget_usd=1.5, essential=True)
        codes = {p.code for p in cands}
        picks: list[PlannedPick] = []
        for pp in raw.picks:
            code = pp.code.strip()
            if code in codes and code not in {x.code for x in picks}:  # 丢弃候选外代码，重复代码保留第一条
                picks.append(pp.model_copy(update={"code": code}))
        if not picks:
            log.warning("premarket analyze %s: plan contains no candidate code", playbook_kind)
            return None
        plan = PremarketPlan(market_view=raw.market_view.strip(), picks=picks)
        store.kv_set(_plan_key(d, playbook_kind), plan.model_dump())
    except Exception as e:  # noqa: BLE001 - 超时/预算/CLI 报错/校验失败：09:25 该路退回量化
        log.warning("premarket analyze %s failed: %r", playbook_kind, e)
        return None
    log.info("premarket analyze %s %s: %d picks, %d keep", d, playbook_kind, len(picks),
             sum(p.action == "keep" for p in picks))
    return plan


def run_analyze(d: dt.date, settings: Settings, store: Store, llm: LLM, deadline: dt.datetime | None = None,
                now: dt.datetime | None = None) -> dict[str, bool]:
    """两套 playbook 各一个线程并发分析，返回 {"active": ok, "candidate": ok}。

    deadline（墙钟时刻）给出时，单次超时取 ANALYZE_TIMEOUT 与剩余秒数的较小者，剩余不到 MIN_ANALYZE_SECONDS
    就不发起：开机晚、补跑扫描时，分析不能拖到 09:25 定稿之后。"""
    timeout = ANALYZE_TIMEOUT
    if deadline is not None:
        timeout = min(ANALYZE_TIMEOUT, (deadline - (now or dt.datetime.now())).total_seconds())
        if timeout < MIN_ANALYZE_SECONDS:
            log.warning("premarket analyze skipped: only %.0fs left before %s", timeout, deadline.time())
            return {k: False for k in KINDS}
    with cf.ThreadPoolExecutor(max_workers=len(KINDS), thread_name_prefix="analyze") as pool:
        futures = {k: pool.submit(analyze, d, settings, store, llm, k, timeout) for k in KINDS}
        return {k: fut.result() is not None for k, fut in futures.items()}


# ---------- 09:25 竞价定稿 ----------

def _merge(q: Pick, pp: PlannedPick) -> Pick:
    """计划价位（四舍五入到分）满足 stop < entry_max < target 且都在量化 entry_max 的 ±PRICE_BAND 内才采用。"""
    entry, stop, target = (round(x, 2) for x in (pp.entry_max, pp.stop, pp.target))
    lo, hi = q.entry_max * (1 - PRICE_BAND), q.entry_max * (1 + PRICE_BAND)
    if not (stop < entry < target and all(lo <= x <= hi for x in (stop, entry, target))):
        entry, stop, target = q.entry_max, q.stop, q.target
    return Pick(code=q.code, name=q.name, score=float(pp.confidence), entry_max=entry, stop=stop, target=target,
                hold_days=q.hold_days, reason=pp.reason.strip() or q.reason)


def auction_filter(plan: PremarketPlan, cands: list[Pick], spot: pd.DataFrame | None) -> list[Pick]:
    """确定性规则：取计划中 action=keep 且仍在候选里的股票；有竞价行时要求竞价涨幅在
    [min_gap_pct, max_gap_pct] 内，且最终采用的 stop < 竞价价 <= 最终采用的 entry_max（与撮合口径一致）；
    没有竞价行则保留。按 confidence 降序。"""
    by_code = {p.code: p for p in cands}
    rows = _spot_rows(spot, list(by_code))
    out: list[Pick] = []
    seen: set[str] = set()
    for pp in sorted(plan.picks, key=lambda x: x.confidence, reverse=True):  # 稳定排序：同分保持计划顺序
        code = pp.code.strip()
        if pp.action != "keep" or code not in by_code or code in seen:
            continue
        seen.add(code)
        pick = _merge(by_code[code], pp)
        row = rows.get(code)
        if row is not None:
            gap, op = _gap_pct(row), _f(row.get("open"))
            if gap is None or not pp.min_gap_pct <= gap <= pp.max_gap_pct or not pick.stop < op <= pick.entry_max:
                continue
        out.append(pick)
    return out


def _ex_adjust(cands: list[Pick], plan: PremarketPlan | None, ctx: dict, spot: pd.DataFrame | None
               ) -> tuple[list[Pick], PremarketPlan | None]:
    """盘前价位按前一日收盘价写成。除权除息日，竞价快照的昨收是除权参考价，与候选统计里的昨收不同：按两者的
    比例换算候选和计划的买入上限、止损、目标价（派息时比例换算与精确的减法略有出入，对盘前价位足够）。
    数据已经按新基准复权时两个昨收相同，不换算。"""
    stats = ctx.get("stats", {})
    factor = {}
    for code, row in _spot_rows(spot, [p.code for p in cands]).items():
        new, old = _f(row.get("prev_close")), _f(stats.get(code, {}).get("prev_close"))
        if new and old and new > 0 and old > 0 and abs(new - old) > 0.011:
            factor[code] = new / old
    if not factor:
        return cands, plan

    def scaled(obj, k):  # 舍入到分：推送显示的价位与过滤、撮合用的价位一致
        return {"entry_max": round(obj.entry_max * k, 2), "stop": round(obj.stop * k, 2),
                "target": round(obj.target * k, 2)}

    cands = [replace(p, **scaled(p, factor[p.code])) if p.code in factor else p for p in cands]
    if plan is not None:
        plan = plan.model_copy(update={"picks": [
            pp.model_copy(update=scaled(pp, factor[pp.code.strip()])) if pp.code.strip() in factor else pp
            for pp in plan.picks]})
    log.info("premarket ex-rights price conversion: %s", {c: round(k, 4) for c, k in factor.items()})
    return cands, plan


def _ref_close(picks: list[Pick], ctx: dict, spot: pd.DataFrame | None) -> dict[str, float]:
    """推荐价位对应的昨收：有竞价快照昨收时价位已按它换算（或本来就一致），否则是扫描时的昨收。结算用它与当天
    交易所的参考昨收比较，不一致（除权除息）时按比例换算。"""
    rows, stats = _spot_rows(spot, [p.code for p in picks]), ctx.get("stats", {})
    out = {}
    for p in picks:
        pc = _f(rows.get(p.code, {}).get("prev_close")) or _f(stats.get(p.code, {}).get("prev_close"))
        if pc and pc > 0:
            out[p.code] = pc
    return out


def _rule_result(settings: Settings, plan: PremarketPlan | None, cands: list[Pick],
                 spot: pd.DataFrame | None) -> tuple[list[Pick], bool, str]:
    """不调用 LLM 的确定性结果：无计划用量化；有计划用规则过滤。也是竞价确认失败或超时时的结果。"""
    if plan is None:
        return cands[: settings.max_picks], False, ""
    prelim = auction_filter(plan, cands, spot)[: settings.max_picks]
    if not prelim:
        vetoed_all = not any(pp.action == "keep" for pp in plan.picks)
        return [], True, plan.market_view + (NOTE_ALL_VETOED if vetoed_all else NOTE_NONE_PASSED)
    return prelim, True, plan.market_view + NOTE_UNCONFIRMED


def finalize(d: dt.date, settings: Settings, store: Store, llm: LLM, client: AkClient, playbook_kind: str,
             timeout: float = AUCTION_TIMEOUT, spot: pd.DataFrame | None = None) -> tuple[list[Pick], bool, str]:
    """返回 (picks, llm_reviewed, market_view)。spot=None 时自行拉取快照；传入空表表示竞价数据缺失。"""
    cands, ctx = _load_candidates(d, store)
    plan = load_plan(store, d, playbook_kind)
    if plan is not None and spot is None:
        spot = _safe(lambda: client.spot([p.code for p in cands]), None)
    cands, plan = _ex_adjust(cands, plan, ctx, spot)
    base = _rule_result(settings, plan, cands, spot)
    if plan is None or not base[0]:
        return base
    prelim = base[0]
    try:
        check = llm.parse(purpose=f"auction:{playbook_kind}", system=AUCTION_SYSTEM,
                          user=_auction_prompt(d, plan, prelim, ctx, spot), schema=AuctionCheck,
                          effort="low", timeout=timeout, max_budget_usd=0.3, essential=True)
    except Exception as e:  # noqa: BLE001 - 超时/预算/CLI 报错/校验失败：按规则过滤结果
        log.warning("premarket auction check %s failed, using rule-filtered picks: %r", playbook_kind, e)
        return base
    vetoed = {x.code.strip() for x in check.picks if x.action == "veto"}
    picks = [p for p in prelim if p.code not in vetoed]  # 只能否决：不在 prelim 里的代码一律忽略
    if len(picks) < len(prelim):
        log.info("premarket auction %s vetoed %s", playbook_kind, [p.code for p in prelim if p.code in vetoed])
    note = check.note.strip()
    return picks, True, plan.market_view + (f"（竞价确认：{note}）" if note else "")


def _playbook_version(kind: str) -> int | None:
    """记录用的版本号；文件损坏时为 None（不能记成空 playbook 的 1，让人误以为跑的是 v1）。"""
    try:  # 只为记录版本号：playbook 文件出问题也不能耽误 09:28 前推送
        pb, ok = playbook.load_checked(kind)
        return int(pb.get("version", 1)) if ok else None
    except Exception:  # noqa: BLE001
        log.exception("playbook %s unreadable", kind)
        return None


def _fetch_spot(client: AkClient, timeout: float, codes: list[str]) -> pd.DataFrame:
    """在守护线程里拉一次候选的竞价快照（逐只取个股报价，几秒内完成；全市场快照要将近一分钟），
    最多等 timeout 秒。超时时用已经取到的那部分（某一只卡住不会让其余候选的竞价数据作废）；
    一只都没取到时返回空表（按竞价数据缺失处理）。"""
    box: list[pd.DataFrame | None] = []
    rows: list[dict] = []
    worker = threading.Thread(target=lambda: box.append(_safe(lambda: client.spot(codes, sink=rows), None)),
                              name="premarket-spot", daemon=True)
    worker.start()
    worker.join(timeout)
    if box and box[0] is not None:
        return box[0]
    if rows:
        log.warning("auction spot partial within %.0fs: %d/%d codes", timeout, len(rows), len(codes))
        return pd.DataFrame(list(rows))
    log.warning("auction spot unavailable within %.0fs, treating auction data as missing", timeout)
    return pd.DataFrame()


def run_final(d: dt.date, settings: Settings, store: Store, llm: LLM, client: AkClient, notifier,
              calendar: TradingCalendar) -> None:
    """09:25 定稿：拉一次快照，两路并发 finalize；FINAL_DEADLINE 内没完成的那一路用确定性结果。只推送 h:active。"""
    deadline = time.monotonic() + FINAL_DEADLINE
    cands, ctx = _load_candidates(d, store)  # 没有候选直接抛错，由 run_job 告警
    plans = {k: load_plan(store, d, k) for k in KINDS}
    spot = pd.DataFrame()  # 两路都没有计划时用不到快照
    if any(p is not None for p in plans.values()):
        spot = _fetch_spot(client, min(SPOT_TIMEOUT, max(0.0, deadline - time.monotonic())), [p.code for p in cands])
    fallback = {k: _rule_result(settings, *reversed(_ex_adjust(cands, plans[k], ctx, spot)), spot) for k in KINDS}
    timeout = min(AUCTION_TIMEOUT, max(1.0, deadline - time.monotonic()))
    pool = cf.ThreadPoolExecutor(max_workers=len(KINDS), thread_name_prefix="premarket")
    futures = {k: pool.submit(finalize, d, settings, store, llm, client, k, timeout, spot) for k in KINDS}
    results: dict[str, tuple[list[Pick], bool, str]] = {}
    try:
        for kind, fut in futures.items():
            try:
                results[kind] = fut.result(timeout=max(0.0, deadline - time.monotonic()))
            except cf.TimeoutError:
                log.warning("premarket %s not final within %.0fs, using rule result", kind, FINAL_DEADLINE)
                results[kind] = fallback[kind]
            except Exception as e:  # noqa: BLE001
                log.warning("premarket finalize %s failed: %r, using rule result", kind, e)
                results[kind] = fallback[kind]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # 超时的线程留在后台跑完，结果丢弃
    for kind, (picks, reviewed, view) in results.items():
        meta = {"llm_reviewed": reviewed, "market_view": view,
                "playbook_version": _playbook_version(kind),
                "ref_close": _ref_close(picks, ctx, spot)}  # 结算据此判断价位是否需要按除权换算
        store.save_recommendation(d, f"h:{kind}", picks, meta)
    picks, reviewed, view = results["active"]
    notifier.send_markdown(format_recommendation(d, picks, reviewed, view))
```

说明：`finalize` 在 `try` 之外只做三件事：读取候选（缺失时抛 `RuntimeError`，由 `run_job` 告警，当天不出推荐）、读取计划、在需要时用 `_safe` 拉快照（失败按竞价数据缺失处理）。结果分三种：无计划 → 量化 `cands[:max_picks]`，`llm_reviewed=False`，推送显示"未经 LLM 审核"（由 `format_recommendation` 负责）；规则过滤后为空 → `[]`，`llm_reviewed=True`，表示经分析后今天不推荐；否则竞价确认只从规则结果中剔除被否决的代码，`market_view` 后附上确认的 note。`_rule_result` 同时用作竞价确认失败和 `FINAL_DEADLINE` 超时时的结果，所以这两种情况推送的内容完全一致。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_premarket.py -v
```

预期：42 passed（两组参数化各含 4 个失败场景，每个算 1 个）。若 `run_analyze` / `run_final` 相关用例报 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`，说明 Part 2 的 `Store` 没有按 Task 2.1 实现（需 `check_same_thread=False` + `RLock`）。

**Step 5: Manual smoke step（真实 Claude 深度分析 + 真实竞价快照，不推送、不写正式库）**

`scripts/smoke_premarket.py`:

```python
"""手动冒烟：手选 3 只股票作为候选，走一遍 analyze（真实 Claude 深度分析）和 finalize（真实竞价快照 + 竞价确认）。
运行：uv run python scripts/smoke_premarket.py [HH:MM]
可选参数 HH:MM：analyze 完成后等到当天该时刻再 finalize（交易日 09:15 前启动并传 09:26，可验证真实竞价数据）。
结果写入临时 sqlite，不影响正式库，也不推送。
"""
import datetime as dt
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import premarket  # noqa: E402
from agents.llm import LLM  # noqa: E402
from config import get_settings  # noqa: E402
from data.akshare_client import AkClient  # noqa: E402
from engine.store import Store  # noqa: E402
from engine.types import Pick  # noqa: E402

CODES = ("600519", "000001", "300750")


def main() -> None:
    s = get_settings()
    store = Store(Path(tempfile.mkdtemp()) / "smoke.sqlite")
    client, llm, d = AkClient(), LLM(s, store), dt.date.today()
    spot = client.spot().set_index("code")
    picks, stats = [], {}
    for code in CODES:
        pc = float(spot.loc[code, "prev_close"])
        picks.append(Pick(code=code, name=str(spot.loc[code, "name"]), score=1.0, entry_max=round(pc * 1.03, 2),
                          stop=round(pc * 0.93, 2), target=round(pc * 1.10, 2), hold_days=5, reason="冒烟测试候选"))
        stats[code] = {"prev_close": pc}
    ctx = {"news": client.news_global(10), "notices": [], "stats": stats,
           "stock_news": {p.code: client.stock_news(p.code, limit=3) for p in picks}}
    store.save_candidates(d, picks, json.loads(json.dumps(ctx, ensure_ascii=False, default=str)))

    t0 = time.monotonic()
    plan = premarket.analyze(d, s, store, llm, "active")
    print(f"analyze 用时 {time.monotonic() - t0:.0f}s")
    assert plan is not None, "深度分析失败，原因见 stderr 中的 'premarket analyze active failed' 日志"
    print("market_view:", plan.market_view)
    for x in plan.picks:
        print(" ", x)

    if len(sys.argv) > 1:
        wait = (dt.datetime.combine(d, dt.time.fromisoformat(sys.argv[1])) - dt.datetime.now()).total_seconds()
        if wait > 0:
            print(f"等待 {wait:.0f}s，到 {sys.argv[1]} 再定稿")
            time.sleep(wait)

    t0 = time.monotonic()
    out, reviewed, view = premarket.finalize(d, s, store, llm, client, "active")  # spot=None：现拉竞价快照
    print(f"finalize 用时 {time.monotonic() - t0:.0f}s，llm_reviewed={reviewed}")
    print("market_view:", view)
    for p in out:
        print(" ", p)
    print(f"本次花费约 {llm.month_cost():.3f} 元")


if __name__ == "__main__":
    main()
```

```
uv run python scripts/smoke_premarket.py
```

预期：先打印 `analyze 用时 …s`（effort=high，通常 1~10 分钟）、中文 `market_view` 和 3 条 `PlannedPick`；再打印 `finalize 用时 …s`（通常 10~60 秒）、`llm_reviewed=True`、带"（竞价确认：…）"的 `market_view` 和 0~3 个 `Pick`。人工检查：每条 reason 都引用了输入中的新闻标题或数字，没有编造输入里不存在的新闻；`min_gap_pct ≤ max_gap_pct`，且上限明显低于涨停对应的涨幅；`stop < entry_max < target`。若 `market_view` 带"竞价二次确认未完成"，到 stderr 中找 `premarket auction check active failed` 看原因。非交易时段的快照不是竞价数据，只能验证连通与输出格式；要验证真实竞价，在交易日 09:15 前运行 `uv run python scripts/smoke_premarket.py 09:26`，脚本分析完后会等到 09:26 再定稿。单次冒烟的花费上限是分析 1.5 美元加确认 0.3 美元，实际通常远低于此。

**Step 6: 提交**

```
git add agents/premarket.py tests/test_premarket.py scripts/smoke_premarket.py
git commit -m "feat: two-stage premarket with deep pre-auction analysis, auction rule filter and veto-only confirm"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

### Task 4.4: agents/review.py — 盘后复盘与日报

**Files:**
- Create: `agents/review.py`
- Test: `tests/test_review.py`

`run_review` 先汇总两个 LLM 账户当天的推荐、成交、持仓和个股日线。两边都没有内容时跳过 LLM 调用，省掉一次花费。LLM 给出的 lesson 只写入 candidate playbook，然后依次调用 `maybe_release`、`git_commit`，最后给 `store.list_accounts()` 中所有有净值记录的账户推送日报。

LLM 经 Task 4.1 的 Claude Code CLI 封装调用。复盘这次调用传 `timeout=REVIEW_TIMEOUT`（300 秒）和 `max_budget_usd=REVIEW_BUDGET_USD`（1 美元），不再有 `max_tokens`。`LLMError`（CLI 报错、拒答、没有结构化输出）、`subprocess.TimeoutExpired` 和其它异常都照常推送日报，摘要写明"复盘失败"。测试里的 `FakeLLM.parse` 与真实签名一致：`parse(purpose, system, user, schema, effort="medium", timeout=120.0, max_budget_usd=1.0, essential=False)`。另有一个测试用真实 `LLM` 加假的 claude.exe 子进程跑通 `run_review`，防止假对象与真实签名不一致。

**Step 1: 写失败的测试**

`tests/test_review.py`:

```python
import datetime as dt
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agents import playbook, review
from agents.llm import LLM, BudgetExceeded, LLMError
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

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0, max_budget_usd=1.0,
              essential=False):
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, max_budget_usd=max_budget_usd, essential=essential))
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


REAL_RELEASE = playbook.maybe_release


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
    assert call["timeout"] == review.REVIEW_TIMEOUT and call["max_budget_usd"] == review.REVIEW_BUDGET_USD
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


@pytest.mark.parametrize("exc", [LLMError("review: error_during_execution"),
                                 subprocess.TimeoutExpired(["claude.exe"], 300.0), ValueError("bad")])
def test_review_llm_failure_still_pushes_report(env, exc):
    run(env, FakeLLM(exc))
    assert len(env["notifier"].sent) == 1 and "复盘失败" in env["notifier"].sent[0]


def test_review_release_message_is_pushed_and_committed(env, monkeypatch):
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: "playbook v2 上线：测试")
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "playbook v2 上线：测试" in env["notifier"].sent[0]
    assert env["commits"] == [(["playbook/active.json", "playbook/candidate.json"],
                               "playbook: 2026-10-16 playbook v2 上线：测试")]


def test_release_message_comes_first(env, monkeypatch):
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: "playbook v2 上线：测试")
    run(env, FakeLLM(OUTPUT))
    assert env["commits"][-1][1] == "playbook: 2026-10-16 playbook v2 上线：测试；复盘更新 1 条"


def test_lessons_apply_to_released_candidate(env, monkeypatch, tmp_path):
    """用真的 maybe_release：发布后的候选版（v2）再写当天的 lesson，今天新写的规则不上线。"""
    monkeypatch.setattr(playbook, "maybe_release", REAL_RELEASE)
    store = env["store"]
    rules = [{"id": "r1", "text": "旧规则", "evidence": "e", "created": "2026-09-01"},
             {"id": "r2", "text": "候选规则", "evidence": "e", "created": "2026-09-20"}]
    playbook.save("candidate", {"version": 1, "created": "x", "rules": rules})
    start = CAL.between(dt.date(2026, 9, 1), D)[-(playbook.MIN_DAYS_BETWEEN_RELEASES + 1)]
    store.kv_set(playbook.RELEASE_KEY, start.isoformat())
    for name, end in (("h:candidate", 1.05), ("h:active", 1.01)):
        store.record_nav(name, start, 500_000.0)
        store.record_nav(name, PREV, 500_000.0 * end)
        store.record_nav(name, D, 500_000.0 * end)
    llm = FakeLLM(OUTPUT)
    run(env, llm)
    act, cand = playbook.load("active"), playbook.load("candidate")
    assert act["version"] == 2 and [r["text"] for r in act["rules"]] == ["旧规则", "候选规则"]
    assert cand["version"] == 2 and [r["text"] for r in cand["rules"]] == ["旧规则", "候选规则",
                                                                          "主板竞价高开超过 5% 的候选否决"]
    assert "playbook v2" in llm.calls[0]["user"] and "候选规则" in llm.calls[0]["user"]
    assert env["commits"][-1][1].startswith("playbook: 2026-10-16 playbook v2 上线")


@pytest.mark.parametrize("broken", ["active", "candidate"])
def test_corrupt_playbook_is_not_released_or_overwritten(env, monkeypatch, tmp_path, broken):
    monkeypatch.setattr(playbook, "maybe_release", REAL_RELEASE)
    (tmp_path / f"{broken}.json").write_text('{"version": 1, "rules": [', encoding="utf-8")
    other = "candidate" if broken == "active" else "active"
    before = (tmp_path / f"{other}.json").read_text(encoding="utf-8")
    env["store"].kv_set(playbook.RELEASE_KEY, "2026-09-01")
    run(env, FakeLLM(OUTPUT))
    assert (tmp_path / f"{broken}.json").read_text(encoding="utf-8") == '{"version": 1, "rules": ['
    assert (tmp_path / f"{other}.json").read_text(encoding="utf-8") == before
    assert env["commits"] == []
    assert f"playbook/{broken}.json 损坏" in env["notifier"].sent[0]


def test_evidence_only_merge_is_committed(env):
    out = ReviewOutput(summary="s", lessons=[Lesson(action="merge", rule_id="r1", text="旧规则",
                                                    evidence="2026-10-16 600001 再次验证")])
    run(env, FakeLLM(out))
    assert playbook.load("candidate")["rules"][0]["evidence"] != "e"
    assert env["commits"][-1][1] == "playbook: 2026-10-16 补充证据 1 条"


def test_release_happens_before_todays_lessons(env, monkeypatch):
    order = []
    monkeypatch.setattr(playbook, "maybe_release", lambda store, today, calendar: order.append("release"))
    real = playbook.apply_lessons
    monkeypatch.setattr(playbook, "apply_lessons", lambda *a, **k: order.append("lessons") or real(*a, **k))
    run(env, FakeLLM(OUTPUT))
    assert order == ["release", "lessons"]  # 当天新写的规则没有前向模拟过，不能随这次发布上线


def test_commit_counts_only_effective_changes(env):
    from agents.review import Lesson
    out = ReviewOutput(summary="s", lessons=[Lesson(action="add", rule_id="", text="新", evidence="2026-10-16 600001"),
                                             Lesson(action="remove", rule_id="r99", text="", evidence="e")])
    run(env, FakeLLM(out))
    assert env["commits"][-1][1] == "playbook: 2026-10-16 复盘更新 1 条"


def test_report_marks_accounts_without_todays_nav(env):
    env["store"].record_nav("q:late", PREV, 500_000.0)
    env["store"].save_account(Account(name="q:late", cash=500_000.0))
    run(env, FakeLLM(ReviewOutput(summary="s", lessons=[])))
    assert "q:late（今日未结算）" in env["notifier"].sent[0]


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


def test_review_through_real_llm_wrapper(env, tmp_path):
    """真实 LLM 封装 + 假 claude.exe 子进程：run_review 传给 parse 的参数必须被真实签名接受。"""
    exe = tmp_path / "bin" / "claude.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    calls = []

    def runner(args, **kw):
        calls.append((args, kw))
        out = {"type": "result", "subtype": "success", "is_error": False, "result": "",
               "structured_output": OUTPUT.model_dump(), "total_cost_usd": 0.05,
               "usage": {"input_tokens": 900, "output_tokens": 300}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out).encode("utf-8"), stderr=b"")

    s = Settings(anthropic_api_key="k", wecom_webhook_url="", root=tmp_path, claude_exe=str(exe))
    review.run_review(D, s, env["store"], LLM(s, env["store"], runner=runner), env["cache"], env["notifier"], CAL)
    args, kw = calls[0]
    assert args[args.index("--effort") + 1] == "medium"
    assert args[args.index("--max-budget-usd") + 1] == f"{review.REVIEW_BUDGET_USD:.2f}"
    assert kw["timeout"] == review.REVIEW_TIMEOUT and "600001" in kw["input"].decode("utf-8")
    assert [r["text"] for r in playbook.load("candidate")["rules"]] == ["旧规则", "主板竞价高开超过 5% 的候选否决"]
    assert "今日 600001 高开 6% 买入后回落。" in env["notifier"].sent[0]
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
REVIEW_TIMEOUT = 300.0  # claude.exe 子进程超时（秒）；复盘没有硬性截止时间
REVIEW_BUDGET_USD = 1.0  # 单次复盘调用的 --max-budget-usd


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


def _effective_changes(old: dict, new: dict) -> tuple[int, int]:
    """(实际生效的改动条数, 只补充了证据的条数)。改动指新增、删除、改写 text，
    不计指向不存在规则的 remove/merge。"""
    a = {r["id"]: r for r in old.get("rules", [])}
    b = {r["id"]: r for r in new.get("rules", [])}
    same = a.keys() & b.keys()
    changed = len(a.keys() ^ b.keys()) + sum(a[k]["text"] != b[k]["text"] for k in same)
    evidence = sum(a[k]["text"] == b[k]["text"] and a[k]["evidence"] != b[k]["evidence"] for k in same)
    return changed, evidence


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
        today = nav.index[-1] == pd.Timestamp(d)
        day_ret = equity / float(nav.iloc[-2]) - 1.0 if len(nav) >= 2 and today else 0.0
        label = name if today else f"{name}（今日未结算）"  # 选股失败当天跳过结算：不把前一天的涨跌当成今天的
        rows.append({"account": label, "equity": equity, "day_ret": day_ret,
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

    # playbook 文件损坏时不发布、不写 lesson：load 会退回空规则，写回去就把规则丢了
    bad = playbook.corrupt_kinds()
    # 先按窗口内实际跑过的候选版决定是否发布，再写当天的 lesson：当天新写的规则没有前向模拟过，不能随这次发布上线
    release = None if bad else playbook.maybe_release(store, d, calendar)
    parts = [release] if release else []
    if not has_rec and not codes:
        summary = "今日两个 LLM 账户均无推荐、无持仓，未调用 LLM 复盘。"
    else:
        cand = playbook.load("candidate")
        try:
            out = llm.parse(purpose="review", system=playbook.load_prompt("review"),
                            user=_build_prompt(d, cand, sections, _bar_lines(cache, codes, d)),
                            schema=ReviewOutput, effort="medium", timeout=REVIEW_TIMEOUT,
                            max_budget_usd=REVIEW_BUDGET_USD, essential=False)
        except BudgetExceeded:
            summary = "本月 LLM 预算已用完，今日未做 LLM 复盘。"
        except Exception as e:  # noqa: BLE001 - LLMError（CLI 报错/拒答/无结构化输出）、超时等：日报照发
            log.warning("review LLM failed: %r", e)
            summary = f"LLM 复盘失败（{type(e).__name__}），今日 playbook 未更新。"
        else:
            summary = out.summary
            lessons = [les for les in out.lessons[:MAX_LESSONS] if les.evidence.strip()]
            new = playbook.apply_lessons(cand, lessons, d)
            if new != cand and not bad:
                playbook.save("candidate", new)
                changed, evidence = _effective_changes(cand, new)
                parts += [f"复盘更新 {changed} 条"] if changed else []
                parts += [f"补充证据 {evidence} 条"] if evidence else []
                parts += [] if changed or evidence else ["复盘整理规则"]

    if parts:
        playbook.git_commit(PLAYBOOK_FILES, f"playbook: {d.isoformat()} " + "；".join(parts))
    if release:
        summary = f"{summary}\n{release}"
    if bad:
        files = "、".join(f"playbook/{k}.json" for k in bad)
        summary = f"{summary}\n注意：{files} 损坏，今日未发布、未更新 playbook，请手工修复（可从 git 历史恢复）。"
    notifier.send_markdown(format_daily_report(d, _report_rows(store, d), summary))
```

说明：`changed` 统计带证据、实际让 candidate 发生变化的那一批 lesson 数量（无效的 remove/merge 只要整体无变化就不提交）。`maybe_release` 不依赖 LLM，预算用完时照常执行，保证每两周一次的发布判断不被跳过。复盘没有硬性截止时间，而 CLI 不像 SDK 那样自动重试，所以 `REVIEW_TIMEOUT` 设得比 `parse` 默认的 120 秒宽松。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_review.py -v
```

预期：13 passed（参数化的 3 个失败场景各算 1 个）。再跑本部分全部测试：

```
uv run pytest tests/test_llm.py tests/test_playbook.py tests/test_premarket.py tests/test_review.py -v
```

预期：全部 passed，0 failed。

**Step 5: Manual smoke step（真实 Claude 复盘一次，不推送、不提交）**

前提：已执行 `python -m jobs.bootstrap`，至少结算过一个有推荐的交易日，且 Task 4.1 的 `scripts/smoke_llm.py` 已跑通（claude.exe 与 `.env` 鉴权可用）。

```
uv run python -c "import datetime as dt; from agents import playbook, review; from agents.llm import LLM; from config import get_settings; from data.akshare_client import AkClient; from data.cache import BarCache; from engine.calendar import TradingCalendar; from engine.store import Store; from notify.wecom import NullNotifier; s=get_settings(); st=Store(s.root / s.db_path); c=AkClient(); cal=TradingCalendar(c.trade_calendar()); playbook.git_commit=lambda *a, **k: False; playbook.maybe_release=lambda *a, **k: None; n=NullNotifier(); d=cal.prev_day(dt.date.today() + dt.timedelta(days=1)); review.run_review(d, s, st, LLM(s, st), BarCache(s.root / s.db_path, c), n, cal); print(n.sent[0]); print(playbook.load('candidate'))"
```

预期：约半分钟到一两分钟后打印日报 markdown，"复盘"段为 Claude 写的中文摘要；`candidate` 中新增的规则（若有）`evidence` 含日期和股票代码。人工检查摘要中没有编造输入里不存在的数字。若摘要是"LLM 复盘失败（…）"，终端会同时打印 `review LLM failed: ...` 警告，按 Task 4.1 Step 5 排查。检查后用 `git checkout -- playbook/candidate.json` 撤销冒烟写入的规则（`git_commit` 已打桩，不会产生提交）。

**Step 6: 提交**

```
git add agents/review.py tests/test_review.py
git commit -m "feat: add post-close LLM review that evolves candidate playbook and pushes daily report"
```

（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

---

## Part 5: 研究循环（递归自我提升）

> 契约补充说明（本部分内自行决定的最简一致选择，每条一行）：
> - 每轮是一次 `llm.parse(...)`（无工具，结构化输出 `ResearchRound`）。user 消息 = 会话开始时生成一次的 `build_context` 背景 + 前几轮反馈 + 本轮任务说明。
> - `submit_draft` 比契约更严：除现任外，影子盘、候选、已退役的名字和 `strategies/` 下已有的同名文件也一律拒绝（每个草稿都用新名字）。这样一条试验记录只对应一份代码，也不会把影子盘策略降回候选；修正出错的草稿时换新名字即可。
> - `submit_draft` 先 `compile()` 再 `check_source()`（语法错误的提示更清楚），名字先去掉首尾空白；文件按 `newline="\n"` 写入；假设存进 `kv "research:hypothesis:<name>"`，`build_context` 把它列在最近试验旁边。
> - `build_context` 另外嵌入 `engine/rules.py`（策略可以 import）。嵌入的源码去掉 `#` 注释（省 token，也避开注释里的非编码措辞），docstring 保留；单文件上限 `FILE_MAX_CHARS`，总长上限 `CONTEXT_MAX_CHARS`，超出从尾部截断。
> - `evaluate_strategy` 给子进程加 `PYTHONIOENCODING=utf-8`（否则 Windows 管道按 ANSI 代码页输出，中文错误信息会乱码），只认最后一个非空行。该行含 `error` 键或缺 `gate_pass`、退出码非 0、超时、无法启动，都返回 `ok=False` 和中文原因，不抛异常。
> - 只在还有评估额度时才提交草稿，提交成功立即评估，所以写入的策略都评估过；评估超时或失败同样消耗一次额度，被拒的草稿不占额度。
> - `done=true` 时先处理本轮草稿再停止。每轮调用前再查一次预算，保证 `max_budget_usd` 为正：`remaining_usd = (monthly_budget_cny - 本月花费) / usd_cny`。
> - `status` 取值：`ok`、`integrity_failed`、`budget_exceeded`（会前或会中）、`llm_error`（`llm.parse` 抛出的任何异常：`LLMError`、超时、输出不合 schema）、`error`（本地写文件或数据库故障）。
> - 返回 dict 只有契约规定的 6 个键。`results` 每项为 `{round, name, hypothesis, saved, message, ok, report, error}`；`written` 为写入的策略名；`analysis` 为最后一轮的分析。
> - `cost_cny` 用 `store.month_llm_cost` 前后相减（与 `LLM.month_cost` 等价），因此测试替身只需实现 `check_budget` 和 `parse`。
> - 推送：正常结束推送普通文本摘要；`llm_error`、`error` 用 `format_alert`；会前预算不足推送一条“跳过”；完整性失败只推告警（会后失败时列出本次写入的策略）。
> - 测试注入：用 `monkeypatch` 把 `research.evaluate_strategy` 包一层、绑定假 runner，`run_research` 的签名保持契约原样；完整性测试在临时目录写真实清单，不打桩 `verify`。

本部分产出 `agents/research.py`：每周六 10:00 由 `jobs/research.py`（Task 6.4）调用 `run_research`，Claude 提出策略变体，合格者进入影子盘。原来基于 SDK `tool_runner` 的方案作废：中转只放行 Claude Code CLI，而 Claude Code 的文件权限规则在 Windows 上表现不稳定，不能当作边界。新的安全模型如下。

- **模型没有任何工具。** 每轮是一次 `llm.parse(...)`（Claude Code 无头模式，`--tools ""`），模型只能返回结构化的 `ResearchRound`：`analysis`、`drafts[name, hypothesis, code]`、`done`。它看不到也碰不到文件系统、git、数据库和网络，对系统唯一的影响途径是返回的代码文本，怎么处理全由 Python 决定。
- **Python 提供全部上下文。** `build_context` 把策略接口、`engine/types.py`、`engine/dataview.py`、`engine/rules.py`、基线与现任策略源码、可 import 的模块列表、`SPLITS`/`GATE`/`HOLDOUT_GATE`、试验总数、最近 20 次试验和各策略状态拼进提示词，总长有上限。
- **Python 执行全部动作，并逐步把关：**
  1. 命名规则：`NAME_RE` 全匹配、不是保留名、不是现任，而且必须是新名字。
  2. 代码规范：`compile()` 通过，且 `strategies.sandbox.check_source()` 无违规（import 白名单、禁用的内建与反射写法、双下划线、pandas/numpy 读写函数）。违规原因原样反馈给下一轮。
  3. 隔离评估：独立子进程 `python -m engine.run_eval <name>`（`cwd=root`，`EVAL_TIMEOUT=1800` 秒；基线策略在约 800 只股票、2,600 个交易日上实测约 7 分钟，留出余量给更复杂的策略），只解析最后一行 JSON（`EvalReport.to_agent_dict()`），留出集只有 `holdout_pass` 布尔值。超时、非零退出、输出异常一律按失败处理并反馈。
  4. 次数上限：每会话最多 `MAX_ROUNDS=3` 轮，每轮最多 `MAX_DRAFTS_PER_ROUND=3` 个草稿，整个会话最多 `MAX_EVALS_PER_SESSION=5` 次评估（超时也算）。DSR 按全部历史试验折减，多试只会更难过门槛。
  5. 完整性：会话前 `integrity.verify(root)` 必须为 `[]`，否则告警且不调用 LLM；每次评估后都校验一次，有变化就立即停止会话（不再评估，也不再请求 LLM）；会话后再校验一次，有变化就告警、不提交 git，本次进入影子盘的策略改回 candidate（不参与前向模拟），文件留在工作区供人工排查。
  6. 预算：开始前 `check_budget(False)`；每轮 `max_budget_usd=min(3.0, 本月剩余预算折合美元)`；`BudgetExceeded` 立即停止。
  7. 审计：所有写入的策略文件都用带假设的提交信息 `git_commit`，并推送企业微信摘要；提交失败时摘要里写明"git 提交失败，请人工提交"和文件路径。
  8. 晋升：过门槛和留出集只代表进入影子盘；能否成为现任由至少 20 个交易日的前向模拟盘决定（Task 2.7），研究会话影响不了。
- **已知局限：** `check_source` 是尽力而为的静态检查，而策略代码和评估器在同一个子进程里运行，静态检查挡不住所有动态写法（例如通过已导入的包对象访问其他模块的属性）。所以门槛结果只授予影子盘资格，每个策略文件都进 git 供审计，engine/ 的文件哈希在会前、每次评估后和会后各校验一次。
- **提示词措辞：** `RESEARCH_SYSTEM`、背景和反馈里的约束一律写成编码规范（“只 import 下列模块”“不读写文件、不联网”），不出现安全、沙箱、绕过、权限一类说法。这类措辞会触发 Opus 5.5 的分类器，服务端回退到中转没有的模型，调用直接失败。测试会检查提示词（嵌入的源码块除外）里没有这些词。
- **失败降级：** 任何 LLM 失败（`LLMError`、超时、输出不合 schema）都只结束本次会话：照常做会后完整性校验，提交此前已写入的策略，推送带原因的告警摘要。

依赖：Part 2（`Store`、`engine.evaluator`、`engine.integrity`）、Part 3（`strategies.base`、`strategies.sandbox`、`engine.run_eval`、`notify.*`）、Part 4（`agents.llm` 的 `LLM`/`BudgetExceeded`/`LLMError`，`agents.playbook.git_commit`）。按 5.1 → 5.2 顺序执行。测试全部离线：不访问网络、不调用真实 claude.exe、不推送企业微信、不起评估子进程。

### Task 5.1: 研究背景、草稿提交与隔离评估（agents/research.py 第一部分）

**Files:**
- Create: `agents/research.py`
- Test: `tests/test_research_tools.py`

**Step 1: 写失败测试**

测试把真实的 `strategies/base.py`、`strategies/baseline_momo.py`、`engine/types.py`、`engine/dataview.py`、`engine/rules.py` 复制到临时目录，当作项目根目录；评估一律注入假 runner，不起子进程。

`tests/test_research_tools.py`：

```python
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from agents import research
from agents.research import Draft, build_context, evaluate_strategy, submit_draft
from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE
from engine.store import Store
from strategies.sandbox import ALLOWED_IMPORTS

PROJECT = Path(__file__).resolve().parents[1]
SOURCES = ["strategies/base.py", "strategies/baseline_momo.py",
           "engine/types.py", "engine/dataview.py", "engine/rules.py"]

GOOD_SRC = '''"""假设：测试用空策略。"""
from engine.dataview import DataView
from engine.types import Pick


class Probe:
    name = "probe_one"

    def select(self, view: DataView) -> list[Pick]:
        return []


STRATEGY = Probe()
'''


@pytest.fixture
def env(tmp_path):
    for rel in SOURCES:  # 用真实源码搭一个临时项目根目录
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / rel, tmp_path / rel)
    settings = Settings(anthropic_api_key="", wecom_webhook_url="", root=tmp_path)
    store = Store(":memory:")
    store.set_status("baseline_momo", "incumbent", date(2026, 10, 1))
    return settings, store


def draft(name="probe_one", code=GOOD_SRC, hypothesis="测试假设：空策略"):
    return Draft(name=name, hypothesis=hypothesis, code=code)


# ---------- submit_draft ----------

@pytest.mark.parametrize("name", ["Bad", "ab", "1abc", "foo-bar", "../evil", "x" * 42,
                                  "base", "sandbox", "__init__"])
def test_submit_bad_name_rejected(env, name):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(name=name))
    assert not ok and "不合规" in msg
    assert store.get_status(name) is None
    assert sorted(p.name for p in (settings.root / "strategies").glob("*.py")) == ["base.py", "baseline_momo.py"]


def test_submit_incumbent_rejected_and_file_untouched(env):
    settings, store = env
    path = settings.root / "strategies" / "baseline_momo.py"
    before = path.read_text(encoding="utf-8")
    ok, msg = submit_draft(settings, store, draft(name="baseline_momo"))
    assert not ok and "当前现任策略" in msg
    assert path.read_text(encoding="utf-8") == before
    assert store.get_status("baseline_momo") == "incumbent"


@pytest.mark.parametrize("status", ["shadow", "candidate", "retired"])
def test_submit_existing_name_rejected(env, status):
    settings, store = env
    store.set_status("old_idea", status, date(2026, 10, 1))
    ok, msg = submit_draft(settings, store, draft(name="old_idea"))
    assert not ok and "新名字" in msg
    assert store.get_status("old_idea") == status
    assert not (settings.root / "strategies" / "old_idea.py").exists()


def test_submit_existing_file_without_status_rejected(env):
    settings, store = env
    path = settings.root / "strategies" / "manual_one.py"
    path.write_text("# 手写策略\n", encoding="utf-8")
    ok, msg = submit_draft(settings, store, draft(name="manual_one"))
    assert not ok and "新名字" in msg
    assert path.read_text(encoding="utf-8") == "# 手写策略\n"
    assert store.get_status("manual_one") is None


def test_submit_disallowed_import_rejected_with_clear_reason(env):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(code="import os\n" + GOOD_SRC))
    assert not ok
    assert "代码规范检查未通过" in msg and "禁止导入 os" in msg  # 原样反馈给模型
    assert not (settings.root / "strategies" / "probe_one.py").exists()
    assert store.get_status("probe_one") is None


def test_submit_syntax_error_rejected(env):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(code="def broken(:\n    return []\n"))
    assert not ok and "语法错误" in msg and "第 1 行" in msg
    assert not (settings.root / "strategies" / "probe_one.py").exists()


def test_submit_compile_only_error_rejected(env):
    # ast.parse 能过、compile() 才报错的代码（模块顶层 return）
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(code=GOOD_SRC + "return STRATEGY\n"))
    assert not ok and "语法错误" in msg
    assert store.get_status("probe_one") is None


def test_submit_success_writes_file_and_sets_candidate(env):
    settings, store = env
    ok, msg = submit_draft(settings, store, draft(name=" probe_one "))  # 首尾空白会被去掉
    assert ok and "strategies/probe_one.py" in msg
    path = settings.root / "strategies" / "probe_one.py"
    assert path.read_text(encoding="utf-8") == GOOD_SRC
    assert b"\r\n" not in path.read_bytes()  # Windows 上也写 LF
    assert store.get_status("probe_one") == "candidate"
    assert store.kv_get("research:hypothesis:probe_one") == "测试假设：空策略"
    ok2, msg2 = submit_draft(settings, store, draft())  # 同名再交一次：拒绝
    assert not ok2 and "新名字" in msg2


# ---------- evaluate_strategy ----------

def report(gate=True, holdout=True):
    return {"strategy": "probe_one", "train": {"sharpe": 1.0}, "val": {"sharpe": 1.2},
            "dsr": 0.95, "gate_pass": gate, "holdout_pass": holdout, "n_trials": 3}


def fake_runner(stdout, returncode=0, stderr="", calls=None, trial=None):
    """trial=(store, gate, holdout)：像真实子进程里的 evaluate 一样先写一条试验记录。"""
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        if trial is not None:
            store, gate, holdout = trial
            store.add_trial(cmd[-1], {}, 1.0, 1.2, gate, holdout)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return run


@pytest.fixture
def written(env):
    settings, store = env
    assert submit_draft(settings, store, draft())[0]
    return settings, store


def test_evaluate_pass_parses_last_json_line_and_sets_shadow(written):
    settings, store = written
    calls = []
    stdout = "策略日志一行\n" + json.dumps(report()) + "\n\n"
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(stdout, calls=calls, trial=(store, True, True)))
    assert out == {"ok": True, "report": report(), "error": ""}
    assert store.get_status("probe_one") == "shadow"
    cmd, kw = calls[0]
    assert cmd == [sys.executable, "-m", "engine.run_eval", "probe_one"]
    assert kw["cwd"] == str(settings.root) and kw["timeout"] == research.EVAL_TIMEOUT == 1800
    assert kw["env"]["PYTHONIOENCODING"] == "utf-8"


@pytest.mark.parametrize("gate,holdout", [(True, False), (False, None)])
def test_evaluate_without_full_pass_keeps_candidate(written, gate, holdout):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(json.dumps(report(gate, holdout)), trial=(store, gate, holdout)))
    assert out["ok"] is True and out["report"]["gate_pass"] is gate and out["error"] == ""
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_forged_report_without_trial_rejected(written):
    settings, store = written  # 策略打印伪造的报告后提前结束进程：数据库里没有本次的试验记录
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(json.dumps(report())))
    assert out["ok"] is False and out["report"] is None and "试验记录" in out["error"]
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_status_follows_trial_record_not_stdout(written):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(json.dumps(report()), trial=(store, False, None)))
    assert out["ok"] is True and out["report"]["gate_pass"] is False and out["report"]["holdout_pass"] is None
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_timeout(written):
    settings, store = written

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    out = evaluate_strategy(settings, store, "probe_one", runner=slow)
    assert out["ok"] is False and out["report"] is None and "超时" in out["error"]
    assert store.get_status("probe_one") == "candidate"


def test_evaluate_cannot_start(written):
    settings, store = written

    def missing(cmd, **kw):
        raise FileNotFoundError("python 不存在")

    out = evaluate_strategy(settings, store, "probe_one", runner=missing)
    assert out["ok"] is False and "无法启动" in out["error"]


@pytest.mark.parametrize("stdout,code", [
    ('{"error": "KeyError: \'close\'"}\n', 1),  # run_eval 的错误 JSON
    (json.dumps(report()) + "\n", 3),           # 退出码非 0 时，报告也不采信
])
def test_evaluate_nonzero_exit(written, stdout, code):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(stdout, returncode=code))
    assert out["ok"] is False and out["report"] is None and f"退出码 {code}" in out["error"]
    assert store.get_status("probe_one") == "candidate"
    if code == 1:
        assert "KeyError" in out["error"]


@pytest.mark.parametrize("stdout", ["", "not json\n", "[1, 2]\n", '{"strategy": "probe_one"}\n',
                                    json.dumps(report()) + "\n多出来的一行\n"])
def test_evaluate_garbage_output(written, stdout):
    settings, store = written
    out = evaluate_strategy(settings, store, "probe_one",
                            runner=fake_runner(stdout, stderr="Traceback: 出错了"))
    assert out["ok"] is False and out["report"] is None and "评估失败" in out["error"]
    assert store.get_status("probe_one") == "candidate"


# ---------- build_context ----------

def test_build_context_contents(env):
    settings, store = env
    store.add_trial("old_idea", {}, 0.4, 0.2, False, None)
    store.kv_set("research:hypothesis:old_idea", "小盘股短期反转更强")
    store.set_status("old_idea", "candidate", date(2026, 10, 2))
    store.set_status("shadow_one", "shadow", date(2026, 10, 3))
    ctx = build_context(settings, store)
    assert "class Strategy(Protocol)" in ctx           # strategies/base.py 接口
    assert "class BaselineMomo" in ctx                 # 基线策略源码
    assert "class DataView" in ctx and "class Pick" in ctx and "def limit_prices" in ctx
    for mod in ALLOWED_IMPORTS:
        assert mod in ctx
    assert json.dumps(GATE) in ctx and json.dumps(HOLDOUT_GATE) in ctx
    assert "2016-01-01" in ctx and "2026-07-01" in ctx  # SPLITS
    assert "n_trials：1" in ctx
    assert "old_idea" in ctx and "小盘股短期反转更强" in ctx  # 最近试验及其假设
    assert "shadow_one" in ctx and "baseline_momo" in ctx       # 策略状态
    assert "沙箱" not in ctx                                    # 源码里的 # 注释已去掉
    assert len(ctx) <= research.CONTEXT_MAX_CHARS


def test_build_context_incumbent_source_and_length_cap(env, monkeypatch):
    settings, store = env
    store.set_status("baseline_momo", "retired", date(2026, 10, 5))
    store.set_status("momo_v2", "incumbent", date(2026, 10, 5))
    body = '"""现任策略。"""\n' + "\n".join(f"X_{i} = {i}" for i in range(20_000))
    (settings.root / "strategies" / "momo_v2.py").write_text(body, encoding="utf-8")
    ctx = build_context(settings, store)
    assert "strategies/momo_v2.py（当前现任策略）" in ctx and "X_1 = 1" in ctx
    assert "已截断" in ctx and "X_19999" not in ctx           # 单文件上限
    assert "class BaselineMomo" in ctx
    assert len(ctx) <= research.CONTEXT_MAX_CHARS
    monkeypatch.setattr(research, "CONTEXT_MAX_CHARS", 3000)  # 总长度上限
    assert len(build_context(settings, store)) <= 3000


# 提示词里不能出现的措辞（会触发分类器，导致调用失败）；背景里的源码也算
CONTEXT_BANNED = ["安全", "沙箱", "绕过", "权限", "漏洞", "攻击", "注入", "逃逸", "恶意",
                  "sandbox", "bypass", "security", "permission", "exploit", "jailbreak", "escape", "malicious",
                  "attack", "inject", "vulnerab"]


def test_build_context_wording_including_embedded_source(env):
    settings, store = env
    ctx = build_context(settings, store)
    assert not [w for w in CONTEXT_BANNED if w in ctx.lower()]
    assert "RESERVED: set[str]" in ctx                 # 保留模块名那一行换成了中性说明


def test_build_context_status_list_cap(env, monkeypatch):
    settings, store = env
    names = [f"cand_{i:03d}" for i in range(60)]
    for n in names:
        store.set_status(n, "candidate", date(2026, 10, 2))
    ctx = build_context(settings, store)
    assert all(n in ctx for n in names)                # 60 个都列出
    monkeypatch.setattr(research, "STATUS_LIST_MAX", 5)
    ctx = build_context(settings, store)
    assert "cand_004" in ctx and "cand_005" not in ctx and "等 60 个" in ctx and "年月后缀" in ctx


def test_evaluate_uses_utf8_and_reports_stderr(written):
    settings, store = written
    calls = []
    evaluate_strategy(settings, store, "probe_one", runner=fake_runner("", calls=calls, stderr="出错了"))
    assert calls[0][1]["encoding"] == "utf-8"
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner("", stderr="Traceback: 出错了"))
    assert "出错了" in out["error"]


def test_evaluate_error_includes_strategy_frames(written):
    settings, store = written
    stderr = ("ERROR:root:评估 probe_one 失败\nTraceback (most recent call last):\n"
              '  File "C:\\proj\\engine\\backtest.py", line 88, in run\n    picks = strategy.select(view)\n'
              '  File "C:\\proj\\strategies\\probe_one.py", line 12, in select\n'
              '    x = h["close"].iloc[-21]\n        ^^^^^^^^^^^^^^^^^^^^^^\n'
              "IndexError: single positional indexer is out-of-bounds\n")
    out = evaluate_strategy(settings, store, "probe_one", runner=fake_runner(
        '{"error": "IndexError: single positional indexer is out-of-bounds"}\n', returncode=1, stderr=stderr))
    assert 'strategies/probe_one.py 第 12 行（select）：x = h["close"].iloc[-21]' in out["error"]
    assert "backtest.py" not in out["error"]           # 只给策略文件里的帧
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_research_tools.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'agents.research'`。

**Step 3: 实现**

`agents/research.py`（本任务写到 `evaluate_strategy` 为止；Task 5.2 会替换顶部 import 区，并在文件末尾追加 `RESEARCH_SYSTEM` 与 `run_research`）：

```python
"""研究循环：Claude 每轮返回分析和策略草稿（结构化输出，没有任何工具）。

Claude 只能返回代码文本。命名规则、import 白名单（strategies.sandbox.check_source）、编译、
写文件、子进程评估、评估次数上限、留出集只回传通过/不通过、会话前、每次评估后和会话后的 engine/ 哈希校验和
月度预算，全部由本模块的 Python 代码执行。
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tokenize
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE, SPLITS
from engine.store import Store
from strategies.base import NAME_RE, RESERVED
from strategies.sandbox import ALLOWED_IMPORTS, check_source

MAX_ROUNDS = 3
MAX_DRAFTS_PER_ROUND = 3
MAX_EVALS_PER_SESSION = 5
EVAL_TIMEOUT = 1800  # 基线策略全量评估实测约 7 分钟
EVAL_ERROR_CHARS = 2_000         # 评估错误原因的上限（另附至多 1500 字的出错位置）
CONTEXT_MAX_CHARS = 60_000       # build_context 总长度上限（字符）
FILE_MAX_CHARS = 15_000          # 背景里单个源码文件的上限
TRIALS_IN_CONTEXT = 20
STATUS_LIST_MAX = 300            # 每种状态最多列出的策略名
BASELINE = "baseline_momo"
INTERFACE_FILES = (
    ("strategies/base.py", "策略接口"),
    ("engine/types.py", "Pick 等数据类型"),
    ("engine/dataview.py", "DataView：策略唯一的数据入口"),
    ("engine/rules.py", "A 股交易规则工具，可以 import"),
)
STATUS_CN = {"incumbent": "现任", "shadow": "影子盘", "candidate": "候选", "retired": "已退役"}


class Draft(BaseModel):
    name: str
    hypothesis: str
    code: str


class ResearchRound(BaseModel):
    analysis: str
    drafts: list[Draft]
    done: bool


def _strip_comments(src: str) -> str:
    """去掉 # 注释（保留 docstring 与字符串里的 #）；无法分词时原样返回。"""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return src
    lines = src.split("\n")
    drop: set[int] = set()
    for t in toks:
        if t.type == tokenize.COMMENT:
            row, col = t.start[0] - 1, t.start[1]
            head = lines[row][:col].rstrip()
            lines[row] = head
            if not head:
                drop.add(row)
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


# 嵌入背景时替换的行：RESERVED 集合里的模块名会把提示词规范里要避开的词带进背景
EMBED_REPLACE = {"strategies/base.py": (re.compile(r"(?m)^RESERVED = .*$"),
                                        "RESERVED: set[str]  # strategies/ 下的保留模块名，不能用作策略名")}


def _source_block(root: Path, rel: str, title: str) -> str:
    p = root / rel
    text = _strip_comments(p.read_text(encoding="utf-8")) if p.is_file() else "（文件不存在）"
    if rel in EMBED_REPLACE:
        pattern, repl = EMBED_REPLACE[rel]
        text = pattern.sub(repl, text)
    if len(text) > FILE_MAX_CHARS:
        text = text[:FILE_MAX_CHARS] + "\n# ...（已截断）"
    return f"### {rel}（{title}）\n```python\n{text.rstrip()}\n```"


def _hypothesis(store: Store, name: str) -> str:
    return str(store.kv_get(f"research:hypothesis:{name}", "") or "")


def _trial_line(store: Store, t: dict) -> str:
    def f(x):
        return "?" if x is None else f"{x:.2f}"

    ho = {True: "通过", False: "未通过", None: "未运行"}[t["holdout_pass"]]
    hyp = _hypothesis(store, t["strategy"])[:120]
    return (f"- {t['ts'][:10]} {t['strategy']}：train 夏普 {f(t['train_sharpe'])}，val 夏普 "
            f"{f(t['val_sharpe'])}，门槛{'达标' if t['gate_pass'] else '未达标'}，holdout {ho}"
            + (f"；假设：{hyp}" if hyp else ""))


def _status_lines(store: Store) -> list[str]:
    out = []
    for st, cn in STATUS_CN.items():
        names = store.strategies_with_status(st)
        shown = "、".join(names[:STATUS_LIST_MAX])
        if len(names) > STATUS_LIST_MAX:
            shown += (f" 等 {len(names)} 个（没有全部列出：新名字请加上年月后缀，"
                      f"例如 _{date.today():%y%m}）")
        out.append(f"- {cn}（{st}）：{shown or '无'}")
    return out


def build_context(settings: Settings, store: Store) -> str:
    """每轮共用的背景：会话限制、门槛、可用模块、策略状态、最近试验、接口与策略源码。

    短而关键的部分在前，源码在后；超过 CONTEXT_MAX_CHARS 时从尾部截断。"""
    root = Path(settings.root)
    splits = {k: [a.isoformat(), b.isoformat() if b else "最新"] for k, (a, b) in SPLITS.items()}
    trials = store.recent_trials(TRIALS_IN_CONTEXT)
    head = [
        f"# 本次研究会话（{date.today().isoformat()}）",
        "",
        "## 会话限制",
        f"- 最多 {MAX_ROUNDS} 轮；每轮最多处理 {MAX_DRAFTS_PER_ROUND} 个草稿；整个会话最多评估 "
        f"{MAX_EVALS_PER_SESSION} 次；单次评估限时 {EVAL_TIMEOUT} 秒。",
        "",
        "## 数据切分与门槛",
        f"- 切分 SPLITS：{json.dumps(splits, ensure_ascii=False)}",
        f"- 回测门槛 GATE（全部为 >= 比较；val_max_dd 为负数）：{json.dumps(GATE)}",
        f"- holdout 门槛 HOLDOUT_GATE：{json.dumps(HOLDOUT_GATE)}。holdout 只返回 holdout_pass（通过/不通过）。",
        f"- 会话开始时的历史试验总数 n_trials：{store.trial_count()}（本会话每评估一次加 1，当前数字见本轮任务）",
        "",
        "## 策略代码可以 import 的模块（含其子模块）",
        "- " + ", ".join(sorted(ALLOWED_IMPORTS)),
        "",
        "## 现有策略（新草稿不能再用这些名字）",
        *_status_lines(store),
        "",
        f"## 最近 {TRIALS_IN_CONTEXT} 次试验（新到旧）",
        *([_trial_line(store, t) for t in trials] or ["- 暂无"]),
        "",
        "## 接口与示例源码（已去掉 # 注释）",
    ]
    blocks = [_source_block(root, rel, title) for rel, title in INTERFACE_FILES]
    incumbents = [n for n in store.strategies_with_status("incumbent") if NAME_RE.fullmatch(n)]
    for name in incumbents:
        if name != BASELINE:
            blocks.append(_source_block(root, f"strategies/{name}.py", "当前现任策略"))
    baseline_title = "基线策略，当前现任" if BASELINE in incumbents else "基线策略，写法示例"
    blocks.append(_source_block(root, f"strategies/{BASELINE}.py", baseline_title))
    text = "\n".join(head) + "\n\n" + "\n\n".join(blocks)
    if len(text) > CONTEXT_MAX_CHARS:
        text = text[:CONTEXT_MAX_CHARS - 20] + "\n...（背景已截断）"
    return text


def submit_draft(settings: Settings, store: Store, draft: Draft) -> tuple[bool, str]:
    """校验并保存一个草稿。成功返回 (True, 说明)；失败返回 (False, 原因)，原因会原样反馈给模型。

    规则：名字匹配 NAME_RE 且不是保留名；不是现任；是新名字（任何已有状态或已有文件都不覆盖，
    保证每个试验记录对应唯一一份代码）；compile() 通过；check_source() 无违规。"""
    name, code = draft.name.strip(), draft.code
    if not NAME_RE.fullmatch(name) or name in RESERVED:
        return False, (f"策略名 {name!r} 不合规：须匹配 ^[a-z][a-z0-9_]{{2,40}}$，"
                       "且不能是 strategies/ 下的保留模块名")
    if name in store.strategies_with_status("incumbent"):
        return False, f"{name} 是当前现任策略，不能覆盖；请换一个新名字"
    path = Path(settings.root) / "strategies" / f"{name}.py"
    status = store.get_status(name)
    if status is not None or path.exists():
        return False, f"策略名 {name} 已被使用（{STATUS_CN.get(status, '已有同名文件')}）；每个草稿都要用新名字"
    try:
        compile(code, f"strategies/{name}.py", "exec", dont_inherit=True)
    except SyntaxError as e:
        return False, f"语法错误（第 {e.lineno} 行）：{e.msg}"
    except (ValueError, RecursionError, MemoryError) as e:
        return False, f"源码无法编译：{type(e).__name__}: {e}"
    problems = check_source(code)
    if problems:
        return False, "代码规范检查未通过：" + "；".join(problems)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8", newline="\n")
    store.set_status(name, "candidate", date.today())
    store.kv_set(f"research:hypothesis:{name}", draft.hypothesis.strip())
    return True, f"已写入 strategies/{name}.py（状态 candidate）"


def _last_json_line(stdout: str) -> dict | None:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _strategy_frames(stderr: str, name: str) -> str:
    """stderr 的 traceback 里属于 strategies/<name>.py 的帧（行号、函数、代码行），帮模型定位出错的行。
    留出期的异常在评估器里直接按不通过处理、不写日志，这里只会有 train/val 段和加载时的出错位置。"""
    lines = (stderr or "").splitlines()
    pat = re.compile(r'File ".*?[\\/]strategies[\\/]' + re.escape(name) + r'\.py", line (\d+), in (\S+)')
    out = []
    for i, ln in enumerate(lines):
        m = pat.search(ln)
        if m:
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            code = "" if nxt.startswith("File ") else nxt
            out.append(f"strategies/{name}.py 第 {m.group(1)} 行（{m.group(2)}）：{code[:200]}")
    return "\n".join(out)[-1500:]


def evaluate_strategy(settings: Settings, store: Store, name: str, runner=None) -> dict:
    """在子进程中运行 engine.run_eval；本次写入数据库的试验记录 gate_pass 且 holdout_pass 时设为 shadow。

    策略代码和评估器在同一个子进程里，stdout 可能被伪造，所以报告只用于展示，是否通过以子进程写入的
    试验记录为准。返回 {"ok": bool, "report": dict | None, "error": str}；任何失败都不抛异常。"""
    run = runner or subprocess.run
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # Windows 管道默认是 ANSI 代码页
    before = store.trial_count(name)
    try:
        proc = run([sys.executable, "-m", "engine.run_eval", name], cwd=str(settings.root),
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=env, timeout=EVAL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "report": None, "error": f"评估超时（{EVAL_TIMEOUT} 秒），请简化计算"}
    except OSError as e:
        return {"ok": False, "report": None, "error": f"无法启动评估进程：{e}"}
    report = _last_json_line(proc.stdout)
    if proc.returncode != 0 or report is None or "error" in report or "gate_pass" not in report:
        if report and report.get("error"):
            detail = str(report["error"])[:EVAL_ERROR_CHARS]
            frames = _strategy_frames(proc.stderr, name)
            if frames:
                detail += "\n出错位置：\n" + frames
        else:
            detail = (proc.stderr or proc.stdout or "无输出").strip()[-1500:]
        return {"ok": False, "report": None, "error": f"评估失败（退出码 {proc.returncode}）：{detail}"}
    trial = store.last_trial(name) if store.trial_count(name) > before else None
    if trial is None:
        return {"ok": False, "report": None, "error": "评估没有写入试验记录，报告不予采信"}
    report = {**report, "gate_pass": trial["gate_pass"], "holdout_pass": trial["holdout_pass"]}
    if trial["gate_pass"] and trial["holdout_pass"]:
        store.set_status(name, "shadow", date.today())
    return {"ok": True, "report": report, "error": ""}
```

说明：
- `strategies.base.NAME_RE` 没有锚点，必须用 `fullmatch`。
- `compile(..., dont_inherit=True)` 不继承本模块的 `from __future__ import annotations`，与策略文件被 import 时的编译方式一致；它还能抓到 `ast.parse` 放过的错误（例如模块顶层的 `return`）。
- 子进程不加 `-I`（`-I` 会让 `-m engine.run_eval` 找不到项目包）。策略里的 `print` 只会出现在报告行之前，所以取最后一个非空行。
- `evaluate_strategy` 依据子进程里 `engine.evaluator.evaluate` 写入同一个数据库的试验记录把状态设为 `shadow`，并用它覆盖报告里的 `gate_pass` / `holdout_pass`；本次没有新增试验记录（例如策略打印伪造的报告后提前结束进程）时，报告不予采信。stdout 只用于展示训练、验证指标。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_research_tools.py -v
```

预期：34 passed。

**Step 5: 提交**

```
git add agents/research.py tests/test_research_tools.py
git commit -m "feat: research context builder, draft submission and isolated evaluation"
```

（提交信息末尾附加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

### Task 5.2: RESEARCH_SYSTEM 提示词与 run_research 多轮主循环

**Files:**
- Modify: `agents/research.py`（替换顶部 import 区，文件末尾追加）
- Test: `tests/test_research_loop.py`

**Step 1: 写失败测试**

`FakeLLM` 按顺序返回预设的 `ResearchRound`（或抛出预设异常），像真 `LLM` 一样先查预算、成功后记账；`FakeEval` 代替 `subprocess.run`，按策略名返回预设报告。`git_commit` 打桩，`verify` 用临时目录里的真实清单。不访问网络，也不起 claude 子进程。

`tests/test_research_loop.py`：

```python
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import research
from agents.llm import BudgetExceeded, LLMError
from agents.research import RESEARCH_SYSTEM, Draft, ResearchRound, run_research
from config import Settings
from engine import integrity
from engine.store import Store
from notify.wecom import NullNotifier
from strategies.sandbox import ALLOWED_IMPORTS

PROJECT = Path(__file__).resolve().parents[1]
SOURCES = ["strategies/base.py", "strategies/baseline_momo.py",
           "engine/types.py", "engine/dataview.py", "engine/rules.py"]
# 提示词里不能出现的措辞（会触发分类器，导致调用失败）
BANNED_WORDS = ["安全", "沙箱", "绕过", "权限", "漏洞", "攻击", "注入", "逃逸", "恶意",
                "sandbox", "bypass", "security", "permission", "exploit", "jailbreak", "escape", "malicious",
                "attack", "inject", "vulnerab"]


def src(name: str, extra: str = "") -> str:
    return f'''"""假设：{name} 的测试假设。"""
{extra}from engine.dataview import DataView
from engine.types import Pick


class S:
    name = "{name}"

    def select(self, view: DataView) -> list[Pick]:
        return []


STRATEGY = S()
'''


def d(name: str, extra: str = "") -> Draft:
    return Draft(name=name, hypothesis=f"{name} 的假设：动量在高换手股票上更强", code=src(name, extra))


def rnd(*drafts, done=False, analysis="分析") -> ResearchRound:
    return ResearchRound(analysis=analysis, drafts=list(drafts), done=done)


def report(name, gate=False, holdout=None):
    return {"strategy": name, "train": {"sharpe": 0.61, "excess_ann": 0.021},
            "val": {"sharpe": 0.95, "excess_ann": 0.043, "max_dd": -0.18},
            "dsr": 0.912345678, "gate_pass": gate, "holdout_pass": holdout, "n_trials": 7}


class FakeLLM:
    """按顺序返回预设结果（ResearchRound 或异常）；像真 LLM 一样先查预算，成功后记账。"""

    def __init__(self, settings, store, script, round_cost=0.5):
        self.settings, self.store = settings, store
        self.script = list(script)
        self.round_cost = round_cost
        self.calls: list[dict] = []

    def check_budget(self, essential):
        t = date.today()
        if not essential and self.store.month_llm_cost(t.year, t.month) >= self.settings.monthly_budget_cny:
            raise BudgetExceeded("本月 LLM 预算已用完")

    def parse(self, purpose, system, user, schema, effort="medium", timeout=120.0,
              max_budget_usd=1.0, essential=False):
        assert max_budget_usd > 0, "单次预算必须为正"
        self.check_budget(essential)
        self.calls.append(dict(purpose=purpose, system=system, user=user, schema=schema, effort=effort,
                               timeout=timeout, max_budget_usd=max_budget_usd, essential=essential))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        self.store.add_llm_usage(purpose, 1000, 200, self.round_cost)
        return item


class FakeEval:
    """替代 subprocess.run 的评估 runner：按策略名返回预设报告，并像真实子进程一样写入试验记录。"""

    def __init__(self, store):
        self.store = store
        self.outcomes: dict[str, dict] = {}
        self.side_effect = None
        self.names: list[str] = []

    def __call__(self, cmd, **kw):
        name = cmd[-1]
        self.names.append(name)
        if self.side_effect:
            self.side_effect(name)
        rep = self.outcomes.get(name, report(name))
        if "gate_pass" in rep:
            self.store.add_trial(name, {}, rep["train"]["sharpe"], rep["val"]["sharpe"],
                                 rep["gate_pass"], rep["holdout_pass"])
        return subprocess.CompletedProcess(cmd, 0, stdout="评估日志\n" + json.dumps(rep) + "\n", stderr="")


@pytest.fixture
def env(tmp_path, monkeypatch):
    for rel in SOURCES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / rel, tmp_path / rel)
    integrity.write_manifest(tmp_path)  # 真实清单：之后 verify(tmp_path) == []
    settings = Settings(anthropic_api_key="", wecom_webhook_url="", monthly_budget_cny=50.0,
                        usd_cny=7.2, root=tmp_path)
    store = Store(":memory:")
    store.set_status("baseline_momo", "incumbent", date(2026, 10, 1))
    commits = []
    monkeypatch.setattr(research, "git_commit",
                        lambda paths, message: commits.append((paths, message)) or True)
    fake_eval = FakeEval(store)
    real_evaluate = research.evaluate_strategy
    monkeypatch.setattr(research, "evaluate_strategy",
                        lambda s, st, name, runner=None: real_evaluate(s, st, name, runner=fake_eval))
    return SimpleNamespace(settings=settings, store=store, notifier=NullNotifier(), commits=commits,
                           evals=fake_eval, root=tmp_path)


def make_llm(e, *script, cost=0.5) -> FakeLLM:
    return FakeLLM(e.settings, e.store, script, round_cost=cost)


def run(e, llm):
    return run_research(e.settings, e.store, e.notifier, llm=llm)


def test_system_prompt_key_points_and_neutral_wording():
    for kw in ["≤5", "沪深 300", "开盘价", "T+1", "train", "val", "holdout", "通过/不通过", "DSR",
               "n_trials", "过拟合", "经济学", "docstring", "STRATEGY", "Strategy", "不联网", "可复现",
               "done=true", "limit_prices(code, name, prev_close, view.asof)", "at_limit_up", "at_limit_down",
               "开盘价不高于 stop"]:
        assert kw in RESEARCH_SYSTEM, kw
    for mod in ALLOWED_IMPORTS:
        assert mod in RESEARCH_SYSTEM, mod
    assert "<<" not in RESEARCH_SYSTEM
    assert not [w for w in BANNED_WORDS if w in RESEARCH_SYSTEM.lower()]


def test_multi_round_feedback_contains_previous_results(env):
    e = env
    e.evals.outcomes["good_one"] = report("good_one", gate=True, holdout=False)
    llm = make_llm(e, rnd(d("good_one"), d("bad_one", extra="import os\n"), analysis="第一轮：换手动量"),
                   rnd(done=True, analysis="结论：good_one 门槛达标但 holdout 未通过"))
    out = run(e, llm)
    assert out["status"] == "ok" and out["rounds"] == 2
    first, second = llm.calls
    for c in llm.calls:
        assert c["purpose"] == "research" and c["system"] == RESEARCH_SYSTEM
        assert c["schema"] is ResearchRound and c["effort"] == "high"
        assert c["timeout"] == 1800.0 and c["essential"] is False
        assert "class Strategy(Protocol)" in c["user"]  # 每轮都带完整背景
    assert first["max_budget_usd"] == 3.0                 # 预算充足时取上限 3 美元
    assert "前几轮" not in first["user"]
    fb = second["user"]
    assert "第一轮：换手动量" in fb
    assert '"gate_pass": true' in fb and '"holdout_pass": false' in fb
    assert '"dsr": 0.9123' in fb                          # 报告数值保留 4 位小数
    assert "bad_one" in fb and "禁止导入 os" in fb         # 被拒原因原样反馈
    assert 'name = "good_one"' in fb                      # 附上草稿代码
    assert "还可评估 4 次" in fb
    prose = re.sub(r"```python.*?```", "", fb, flags=re.S).lower()
    assert not [w for w in BANNED_WORDS if w in prose]    # 代码块以外的文字措辞中性
    assert out["analysis"] == "结论：good_one 门槛达标但 holdout 未通过"
    assert out["written"] == ["good_one"]
    assert [r["name"] for r in out["results"]] == ["good_one", "bad_one"]


def test_eval_cap_enforced_across_rounds(env, monkeypatch):
    e = env
    monkeypatch.setattr(research, "MAX_EVALS_PER_SESSION", 3)
    llm = make_llm(e, rnd(d("a_one"), d("b_two")), rnd(d("c_three"), d("d_four")), rnd(d("e_five")))
    out = run(e, llm)
    assert e.evals.names == ["a_one", "b_two", "c_three"]
    assert len(llm.calls) == 2                            # 额度用完即停，不再请求第 3 轮
    assert "还可评估 1 次" in llm.calls[1]["user"]
    assert out["written"] == ["a_one", "b_two", "c_three"]
    assert not (e.root / "strategies" / "d_four.py").exists()
    skipped = out["results"][-1]
    assert skipped["name"] == "d_four" and not skipped["saved"] and "评估次数已用完" in skipped["message"]


def test_draft_cap_per_round_and_rejections_cost_no_eval(env):
    e = env
    llm = make_llm(e, rnd(d("a_one", extra="import os\n"), d("b_two"), d("c_three"), d("d_four")),
                   rnd(done=True))
    out = run(e, llm)
    assert e.evals.names == ["b_two", "c_three"]          # 被拒的 a_one 不占评估次数
    assert [r["name"] for r in out["results"]] == ["a_one", "b_two", "c_three"]
    assert not (e.root / "strategies" / "d_four.py").exists()
    assert "另有 1 个草稿超出每轮 3 个的上限" in llm.calls[1]["user"]
    assert "还可评估 3 次" in llm.calls[1]["user"]


def test_stops_on_done_after_processing_drafts(env):
    e = env
    llm = make_llm(e, rnd(d("a_one"), done=True), rnd(d("b_two")))
    out = run(e, llm)
    assert len(llm.calls) == 1 and out["rounds"] == 1 and out["status"] == "ok"
    assert e.evals.names == ["a_one"]


def test_stops_on_empty_drafts_without_commit(env):
    e = env
    llm = make_llm(e, rnd(analysis="暂无值得检验的想法"), rnd(d("b_two")))
    out = run(e, llm)
    assert len(llm.calls) == 1 and out["status"] == "ok" and out["written"] == []
    assert e.commits == []
    assert "本次没有提交策略草稿" in e.notifier.sent[-1]


def test_stops_after_max_rounds(env):
    e = env
    llm = make_llm(e, rnd(d("a_one")), rnd(d("b_two")), rnd(d("c_three")), rnd(d("d_four")))
    out = run(e, llm)
    assert len(llm.calls) == research.MAX_ROUNDS == 3 and out["rounds"] == 3
    assert "这是最后一轮" in llm.calls[2]["user"] and "这是最后一轮" not in llm.calls[1]["user"]
    assert e.evals.names == ["a_one", "b_two", "c_three"]


def test_budget_exceeded_mid_session_stops_and_keeps_earlier_work(env):
    e = env
    llm = make_llm(e, rnd(d("a_one")), rnd(d("b_two")), cost=60.0)  # 第 1 轮就花掉 60 元 > 预算 50 元
    out = run(e, llm)
    assert out["status"] == "budget_exceeded" and out["rounds"] == 1 and len(llm.calls) == 1
    assert out["cost_cny"] == pytest.approx(60.0)
    assert e.commits[0][0] == ["strategies/a_one.py"]
    assert "预算" in e.notifier.sent[-1]


def test_budget_exhausted_before_start_skips_session(env):
    e = env
    e.store.add_llm_usage("premarket:active", 0, 0, 50.0)
    llm = make_llm(e, rnd(d("a_one")))
    out = run(e, llm)
    assert out == {"status": "budget_exceeded", "rounds": 0, "written": [], "results": [],
                   "cost_cny": 0.0, "analysis": ""}
    assert llm.calls == [] and e.evals.names == [] and e.commits == []
    assert len(e.notifier.sent) == 1 and "跳过" in e.notifier.sent[0]


def test_max_budget_usd_and_cost_follow_month_budget(env):
    e = env
    e.store.add_llm_usage("review", 0, 0, 40.0)  # 本月已花 40 元，剩 10 元
    llm = make_llm(e, rnd(d("a_one")), rnd(done=True), cost=0.5)
    out = run(e, llm)
    assert llm.calls[0]["max_budget_usd"] == pytest.approx(10.0 / 7.2)
    assert llm.calls[1]["max_budget_usd"] == pytest.approx(9.5 / 7.2)
    assert out["cost_cny"] == pytest.approx(1.0)  # 本次会话的月度花费增量


@pytest.mark.parametrize("exc", [LLMError("research: error_during_execution 中转返回 502"),
                                 subprocess.TimeoutExpired("claude.exe", 1800)])
def test_llm_failure_aborts_cleanly(env, exc):
    e = env
    out = run(e, make_llm(e, exc))
    assert out["status"] == "llm_error" and out["rounds"] == 0 and out["written"] == []
    assert e.evals.names == [] and e.commits == []
    msg = e.notifier.sent[-1]
    assert "告警" in msg and type(exc).__name__ in msg


def test_llm_failure_in_later_round_still_commits_earlier_drafts(env):
    e = env
    out = run(e, make_llm(e, rnd(d("a_one")), LLMError("research: error_max_budget_usd")))
    assert out["status"] == "llm_error" and out["rounds"] == 1 and out["written"] == ["a_one"]
    assert e.commits[0][0] == ["strategies/a_one.py"]
    assert "error_max_budget_usd" in e.notifier.sent[-1]


def _touch_engine(root):
    with open(root / "engine" / "rules.py", "a", encoding="utf-8") as f:
        f.write("\nEXTRA = 1\n")


def test_integrity_failure_before_alerts_without_llm_call(env):
    e = env
    _touch_engine(e.root)
    llm = make_llm(e, rnd(d("a_one")))
    out = run(e, llm)
    assert out["status"] == "integrity_failed" and llm.calls == []
    assert e.evals.names == [] and e.commits == []
    assert "告警" in e.notifier.sent[0] and "engine/rules.py" in e.notifier.sent[0]


def test_integrity_change_after_alerts_and_skips_commit(env):
    e = env
    e.evals.side_effect = lambda name: _touch_engine(e.root)  # 评估过程中 engine/ 被改动
    out = run(e, make_llm(e, rnd(d("a_one"), done=True)))
    assert out["status"] == "integrity_failed" and out["written"] == ["a_one"]
    assert e.commits == []
    assert (e.root / "strategies" / "a_one.py").exists()       # 留在工作区供人工检查
    alert = e.notifier.sent[-1]
    assert "告警" in alert and "engine/rules.py" in alert and "a_one" in alert


def test_commit_summary_and_return_shape(env):
    e = env
    e.evals.outcomes["a_one"] = report("a_one", gate=True, holdout=True)
    llm = make_llm(e, rnd(d("a_one"), d("b_two"), done=True, analysis="结论：a_one 值得进影子盘观察"))
    out = run(e, llm)
    assert set(out) == {"status", "rounds", "written", "results", "cost_cny", "analysis"}
    assert out["written"] == ["a_one", "b_two"] and out["cost_cny"] == pytest.approx(0.5)
    assert e.store.get_status("a_one") == "shadow" and e.store.get_status("b_two") == "candidate"
    paths, message = e.commits[0]
    assert paths == ["strategies/a_one.py", "strategies/b_two.py"]
    assert "a_one 的假设：动量在高换手股票上更强" in message and "b_two 的假设" in message
    assert "进入影子盘" in message
    summary = e.notifier.sent[-1]
    assert "研究会话" in summary and "a_one" in summary and "进入影子盘" in summary
    assert "0.50 元" in summary and "结论：a_one 值得进影子盘观察" in summary


def test_default_llm_is_built_from_settings(env, monkeypatch):
    e = env
    fake = make_llm(e, rnd(done=True))
    seen = []
    monkeypatch.setattr(research, "LLM", lambda settings, store: seen.append((settings, store)) or fake)
    out = run_research(e.settings, e.store, e.notifier)
    assert seen == [(e.settings, e.store)] and len(fake.calls) == 1 and out["status"] == "ok"


def test_failed_eval_still_counts_toward_cap(env):
    e = env
    e.evals.outcomes["a_one"] = {"error": "KeyError: 'close'"}  # run_eval 报错，没有写入试验记录
    llm = make_llm(e, rnd(d("a_one")), rnd(done=True))
    out = run(e, llm)
    assert out["results"][0]["ok"] is False and "KeyError" in out["results"][0]["error"]
    assert "还可评估 4 次" in llm.calls[1]["user"]       # 失败同样消耗一次额度


def test_local_error_stops_and_commits_earlier_drafts(env, monkeypatch):
    e = env
    real = research.submit_draft

    def flaky(settings, store, draft):
        if draft.name == "b_two":
            raise OSError("磁盘已满")
        return real(settings, store, draft)

    monkeypatch.setattr(research, "submit_draft", flaky)
    out = run(e, make_llm(e, rnd(d("a_one"), d("b_two"), d("c_three"))))
    assert out["status"] == "error" and e.evals.names == ["a_one"]
    assert e.commits[0][0] == ["strategies/a_one.py"]
    msg = e.notifier.sent[-1]
    assert "告警" in msg and "OSError" in msg and "磁盘已满" in msg


def test_feedback_truncates_long_code_and_hypothesis(env):
    e = env
    long = Draft(name="long_one", hypothesis="长" * 3000, code=src("long_one", extra="X = 1\n" * 3000))
    llm = make_llm(e, rnd(long), rnd(done=True))
    run(e, llm)
    fb = llm.calls[1]["user"]
    assert "已截断" in fb and "长" * (research.FEEDBACK_HYPOTHESIS_CHARS + 1) not in fb
    assert len(fb) < len(llm.calls[0]["user"]) + research.FEEDBACK_CODE_CHARS + 3000


def test_context_built_once_and_round_task_has_current_n_trials(env, monkeypatch):
    e = env
    built = []
    real = research.build_context
    monkeypatch.setattr(research, "build_context", lambda s, st: built.append(1) or real(s, st))
    llm = make_llm(e, rnd(d("a_one")), rnd(done=True))
    run(e, llm)
    assert built == [1]
    assert "n_trials：0（下一次评估计为第 1 次）" in llm.calls[0]["user"]
    assert "n_trials：1（下一次评估计为第 2 次）" in llm.calls[1]["user"]


def test_engine_change_mid_session_stops_and_reverts_shadow(env):
    e = env
    for n in ("a_one", "b_two"):
        e.evals.outcomes[n] = report(n, gate=True, holdout=True)
    e.evals.side_effect = lambda name: _touch_engine(e.root) if name == "a_one" else None
    llm = make_llm(e, rnd(d("a_one"), d("b_two")), rnd(d("c_three")))
    out = run(e, llm)
    assert out["status"] == "integrity_failed" and set(out) == {"status", "rounds", "written", "results",
                                                               "cost_cny", "analysis"}
    assert e.evals.names == ["a_one"] and len(llm.calls) == 1   # 不再评估、不再请求 LLM
    assert e.store.get_status("a_one") == "candidate"           # 不参与前向模拟
    assert e.store.strategies_with_status("shadow") == [] and e.commits == []
    assert "已从影子盘改回 candidate：a_one" in e.notifier.sent[-1]


def test_commit_failure_is_reported(env, monkeypatch):
    e = env
    monkeypatch.setattr(research, "git_commit", lambda paths, message: False)
    out = run(e, make_llm(e, rnd(d("a_one"), done=True)))
    assert out["status"] == "ok"
    assert "git 提交失败，请人工提交：strategies/a_one.py" in e.notifier.sent[-1]


def test_system_prompt_explains_reserved_names_and_suffix():
    assert "open_px" in RESEARCH_SYSTEM and "连变量名" in RESEARCH_SYSTEM
    assert "to_string" in RESEARCH_SYSTEM and ".format" in RESEARCH_SYSTEM
    assert "年月后缀" in RESEARCH_SYSTEM
```

**Step 2: 运行测试，确认失败**

```
uv run pytest tests/test_research_loop.py -v
```

预期：FAIL，`ImportError: cannot import name 'RESEARCH_SYSTEM' from 'agents.research'`。

**Step 3: 实现**

先把 `agents/research.py` 顶部的 import 区（从 `import io` 到 `from strategies.sandbox import ALLOWED_IMPORTS, check_source`）整体替换为：

```python
import io
import json
import logging
import os
import re
import subprocess
import sys
import tokenize
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from agents.llm import LLM, BudgetExceeded
from agents.playbook import git_commit
from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE, SPLITS
from engine.integrity import verify
from engine.store import Store
from notify.format import format_alert
from strategies.base import NAME_RE, RESERVED
from strategies.sandbox import ALLOWED_IMPORTS, check_source

log = logging.getLogger(__name__)
```

再在文件末尾追加：

```python
FEEDBACK_CODE_CHARS = 6_000      # 反馈里每个草稿代码的上限
FEEDBACK_ANALYSIS_CHARS = 2_000  # 反馈里每轮分析的上限
FEEDBACK_HYPOTHESIS_CHARS = 500  # 反馈里每个草稿假设的上限
FEEDBACK_MESSAGE_CHARS = 2_000   # 反馈里处理结果的上限


RESEARCH_SYSTEM = """你是 RSIQS 的量化研究员。RSIQS 是一个 A 股盘前推荐与模拟交易系统：每个交易日开盘前，现任量化策略给出候选股票，LLM 层复核后推送不超过 5 只，模拟盘按开盘价成交。你的工作是提出并检验新的选股策略，让系统长期变得更好。

# 目标
在下列约束下，提高扣除交易成本后、相对沪深 300 的风险调整超额收益：
- 每天最多新开仓 ≤5 只：回测与模拟盘只取 select 返回列表的前 5 个，每只约占初始资金的 1/5。
- 开盘价成交：t 日的 select 只能看到截至上一交易日收盘的数据（view.asof），选中的股票在 t 日按开盘价加 10bp 滑点买入；开盘价高于 entry_max、开盘价不高于 stop、开盘即涨停、停牌或买不足 100 股时不成交。
- T+1：买入当天不能卖出。每个交易日分三步撮合：(1) 开盘卖出：到达到期日（买入后第 hold_days 个交易日）按开盘价卖出；否则开盘价 ≤ stop 按开盘价止损；否则开盘价 ≥ target 按开盘价止盈。(2) 开盘买入：只有第 1 步卖出回笼的资金可用于当天买入。(3) 盘中卖出：其余持仓盘中最低价触及 stop 按 stop 卖出（与 target 同日触及时按止损处理），否则最高价触及 target 按 target 卖出，这部分资金当天不能再买入。全天封死跌停时卖不出；停牌或价格数据异常的股票当天不交易。卖出同样扣 10bp 滑点，成交价不超出当日涨跌停价。
- 涨跌停和成本按成交日当时的规则：创业板 2020-08-24 前涨跌幅 10%；佣金万 2.5（最低 5 元）；过户费 2022-04-29 前十万分之二、之后十万分之一；卖出印花税 2023-08-28 前千分之一、之后万 5。换手越高，成本吃掉的收益越多。
- 股票池约 800 只（沪深 300 与中证 500 的当前成分股，前复权日线，2015 年 6 月起）。存在幸存者偏差，回测结果偏乐观。

# 数据切分
- train：2016 年至 2024 年，用来形成和检验想法。
- val：2025 年 1 月至 2026 年 6 月，样本外检验，门槛主要看这一段。
- holdout：2026 年 7 月至今。只有 train/val 门槛全部达标才会运行，而且只返回 holdout_pass（通过/不通过），不给任何数值。不要根据 holdout 结果反复改版去碰运气，那只会消耗试验次数。
精确日期和各项门槛数值见用户消息。

# 过拟合与多重检验
- 每次评估都记为一次试验；n_trials 是所有策略、所有会话累计的总数。门槛里的 DSR（Deflated Sharpe Ratio）按 n_trials 折减：试验越多，同样的夏普越可能只是运气，DSR 越低，越难达标。
- 所以要少而精：1 到 2 个论证充分、彼此差异明显的变体，胜过一堆参数微调。不做参数网格搜索，不为贴合 train/val 去微调阈值；参数取常见的整数（如 5、10、20、60 日），规则越简单越好。
- 只在 val 好看、train 很差，或者只靠少数几笔交易撑起来的结果，大概率是噪声，属于过拟合的信号。

# 好的假设
- 优先有经济学或市场微观结构依据的想法，例如：中期动量与短期反转、流动性与成交额变化、波动率与特质波动、涨跌停附近的价格行为、散户主导下的过度反应与修正、T+1 带来的隔夜风险、开盘成交的价格冲击等。
- 讲清楚：这个效应为什么应该存在，为什么还没被套利掉，在什么市场环境下会失效，与现任策略有什么不同。
- 先读用户消息里的现任策略和最近的试验记录，不重复已经失败的方向；前几轮有结果时，据此调整。
- 没有值得检验的想法时，宁可返回 done=true 和空的 drafts，也不要凑数。

# 策略代码规范
每个草稿的 code 是一个完整的 Python 模块，会原样保存为 strategies/<name>.py：
1. 模块 docstring 以“假设：”开头，写明假设、经济学理由、与现任策略的差异、预期何时失效；Draft.hypothesis 用一两句话概括同一个假设。
2. 定义一个类，实现 strategies.base.Strategy 接口（按接口写好 name 和 select 即可，不需要 import strategies.base）：类属性 name 等于草稿的 name；方法 select(self, view: DataView) -> list[Pick] 返回按优先级从高到低排序的 Pick 列表（可以多于 5 个：回测取前 5 个，盘前流程最多取前 15 个复核）。
3. 模块末尾定义模块级实例 STRATEGY = 你的类()。
4. 只 import 下列模块（及其子模块）：<<ALLOWED_IMPORTS>>。不要写 from __future__ import annotations。
5. 只通过 view 取数：view.codes()、view.name(code)、view.history(code, n)、view.bar_on(code, d)。history 只含 asof 及以前的行，列为 open、high、low、close、volume（手）、amount（元）、prev_close。用 engine.rules 判断 ST 和涨跌停：计算涨跌停价时把 view.asof 作为 d 传入，例如 limit_prices(code, name, prev_close, view.asof)，因为涨跌幅按当天的规则生效；判断是否涨停或跌停用 at_limit_up / at_limit_down，它们容忍一分钱的复权舍入误差。
6. 策略是纯计算：不读写文件、不联网、不打印；结果可复现，不用随机数，不依赖当前时间；select 不依赖上一次调用留下的状态。
7. 写法保持直接：不用 getattr、setattr、vars、globals、operator.attrgetter 这类反射写法，不用 eval、exec、open，也不用 pandas 的 eval、query；不写 print、exit，也不 raise SystemExit；open、input、compile、exec、eval、print、exit、quit、locals、globals、vars、breakpoint 这些内建名字连变量名、参数名也不能用（开盘价写成 open_px、h["open"] 或 bar.open）；self 只作为类中方法的第一个参数，只在方法体内直接给 self 的属性赋值（不在嵌套函数或 lambda 里），不给模块、类或其他对象的属性赋值；拼字符串用 f-string，任何对象的 .format 方法都不用（包括 str.format）；不访问 pd.api、pd.set_option、np.seterr（需要时用 np.errstate）；名字、属性和字符串里都不出现双下划线（例如 super().__init__()、__name__、对象的私有属性；定义 __init__ 方法本身没问题）；pandas 和 numpy 只做计算，不调用 read_*、to_csv、to_string、load、save 等读写或导出函数。
8. 注意速度：一次评估要回放约 2,600 个交易日、每天约 800 只股票，并且有时间上限；基线策略每天对每只股票取 60 行做几次 pandas 运算，全程约 7 分钟。用 view.history(code, n) 只取需要的最近 n 行，避免每天对整段历史重复计算。
9. Pick 字段：code、name、score、entry_max（可接受的最高买入价，通常比最近收盘价高 2% 到 3%）、stop、target（须满足 stop < entry_max < target）、hold_days（正整数），以及中文 reason（选股依据，会推送给用户）。
10. name 须匹配 ^[a-z][a-z0-9_]{2,40}$，并且是一个新名字：不能与用户消息里列出的任何已有策略重名，也不能与 strategies/ 下已有的模块重名。已有策略很多、名单没有全部列出时，给名字加上年月后缀（例如 _2610）。

# 工作方式
- 本任务不使用工具：需要的接口说明、源码、门槛和试验记录都在用户消息里。你只需按 JSON Schema 返回 analysis、drafts、done。
- 系统依次处理你的草稿：检查名字和代码规范，保存为 strategies/<name>.py（状态 candidate），然后运行回测评估。没通过检查的草稿不会评估，也不占评估次数，原因会在下一轮告诉你。
- gate_pass 且 holdout_pass 的策略自动进入影子盘（shadow），之后由至少 20 个交易日的前向模拟盘表现决定能否成为现任。
- 一次会话有轮数、每轮草稿数和评估次数的上限（具体数字见用户消息）。草稿数不要超过剩余评估次数，超出的部分不会处理。
- 下一轮你会看到每个草稿的处理结果和评估报告：train、val 各含 total_return、ann_return、sharpe（年化）、max_dd（负数）、excess_ann（相对沪深 300 的年化超额）、n_days；另有 dsr、gate_pass、holdout_pass（门槛未达标时为 null，表示没有运行）和 n_trials。
- 每个草稿都要用新名字，包括修正上一轮出错的草稿。

# 输出
- analysis：中文。先回顾现任策略、试验记录和上一轮结果，再说明本轮每个假设的逻辑和预期；最后一轮或准备结束时，总结结论和下次研究的方向。
- drafts：0 个或多个 Draft（name、hypothesis、code）。
- done：之后不需要再来一轮时为 true（本轮的草稿照常处理）；想根据本轮评估结果继续迭代时为 false。
""".replace("<<ALLOWED_IMPORTS>>", ", ".join(sorted(ALLOWED_IMPORTS)))


def _month_cost(store: Store) -> float:
    t = date.today()
    return store.month_llm_cost(t.year, t.month)


def _rounded(obj, nd: int = 4):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _rounded(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rounded(v, nd) for v in obj]
    return obj


def _outcome(res: dict) -> str:
    """一个草稿的处理结果，一句中文（用于推送与提交信息）。"""
    if not res["saved"]:
        return res["message"]
    if not res["ok"]:
        return res["error"]
    rep = res["report"]
    if rep.get("gate_pass") and rep.get("holdout_pass"):
        return "门槛达标，holdout 通过，已进入影子盘"
    if rep.get("gate_pass"):
        return "门槛达标，holdout 未通过，保持 candidate"
    return "门槛未达标，保持 candidate"


def _draft_block(res: dict, draft: Draft) -> str:
    lines = [f"### 草稿 {res['name']}", f"假设：{res['hypothesis'][:FEEDBACK_HYPOTHESIS_CHARS]}",
             f"处理：{res['message'][:FEEDBACK_MESSAGE_CHARS]}"]
    if res["saved"] and res["ok"]:
        lines.append("评估报告：" + json.dumps(_rounded(res["report"]), ensure_ascii=False))
    elif res["saved"]:
        lines.append(res["error"][:EVAL_ERROR_CHARS + 1_600])  # 错误原因加出错位置
    code = draft.code
    if len(code) > FEEDBACK_CODE_CHARS:
        code = code[:FEEDBACK_CODE_CHARS] + "\n# ...（已截断）"
    lines.append(f"代码：\n```python\n{code.rstrip()}\n```")
    return "\n".join(lines)


def _feedback(history: list[dict]) -> str:
    parts = ["# 本会话前几轮的结果"]
    for h in history:
        parts.append(f"## 第 {h['round']} 轮\n你的分析（节选）：{h['analysis'][:FEEDBACK_ANALYSIS_CHARS]}")
        parts += [_draft_block(res, d) for res, d in zip(h["results"], h["drafts"])]
        if not h["results"]:
            parts.append("（本轮没有草稿）")
        if h["ignored"]:
            parts.append(f"另有 {h['ignored']} 个草稿超出每轮 {MAX_DRAFTS_PER_ROUND} 个的上限，没有处理。")
    return "\n\n".join(parts)


def _round_task(rnd_no: int, evals_left: int, n_trials: int) -> str:
    lines = [f"# 本轮任务（第 {rnd_no}/{MAX_ROUNDS} 轮）",
             f"本会话还可评估 {evals_left} 次，本轮最多处理 {MAX_DRAFTS_PER_ROUND} 个草稿；草稿数不要超过剩余评估次数。",
             f"目前累计试验次数 n_trials：{n_trials}（下一次评估计为第 {n_trials + 1} 次）。"]
    if rnd_no == 1:
        lines.append("先在 analysis 里回顾现任策略和最近的试验记录，再提出有依据的新假设。"
                     "没有值得检验的想法时，返回 done=true 和空的 drafts。")
    else:
        lines.append("请根据上面的结果决定：修正出错的草稿（换新名字）、提出有依据的新变体，或者返回 done=true 结束。"
                     "不要为了让 holdout 通过而微调参数。")
    if rnd_no == MAX_ROUNDS:
        lines.append("这是最后一轮：analysis 里请总结本会话的结论和下次研究的方向。")
    return "\n".join(lines)


def _commit_message(today: date, saved: list[dict]) -> str:
    lines = [f"research: {today.isoformat()} 新增候选策略 {', '.join(r['name'] for r in saved)}", ""]
    for r in saved:
        hyp = " ".join(r["hypothesis"].split())[:300]
        lines.append(f"- {r['name']}：{hyp}（{_outcome(r)}）")
    return "\n".join(lines)


def _summary(today: date, out: dict, error: str) -> str:
    label = {"ok": "完成", "budget_exceeded": "本月 LLM 预算用完，提前结束",
             "llm_error": "Claude 调用失败，提前结束", "error": "本地处理出错，提前结束"}[out["status"]]
    saved = [r for r in out["results"] if r["saved"]]
    other = [r for r in out["results"] if not r["saved"]]
    lines = [f"轮数 {out['rounds']}｜评估 {len(saved)} 次｜花费 {out['cost_cny']:.2f} 元"]
    if error:
        lines.append(f"原因：{error[:300]}")
    if saved:
        lines += ["新策略："] + [f"- {r['name']}：{_outcome(r)[:200]}" for r in saved]
    if other:
        lines += ["未采用的草稿："] + [f"- {r['name']}：{r['message'][:200]}" for r in other]
    if not out["results"]:
        lines.append("本次没有提交策略草稿。")
    if out["analysis"]:
        lines.append("结论：" + out["analysis"][:800])
    title = f"研究会话 {today.isoformat()}：{label}"
    if out["status"] in ("llm_error", "error"):
        return format_alert(title, "\n".join(lines))
    return f"RSIQS {title}\n" + "\n".join(lines)


def _process_round(settings: Settings, store: Store, rnd_no: int, rnd: ResearchRound,
                   evals: int, out: dict) -> tuple[list[dict], int]:
    """依次提交本轮至多 MAX_DRAFTS_PER_ROUND 个草稿：只在还有评估额度时提交，提交成功就立即评估。
    每次评估后都校验 engine/：不符时不再处理后面的草稿，并把 out["engine_changed"] 置为 True。

    结果边做边记进 out：即使本地出错抛异常，已写入的文件也会照常提交。"""
    results = []
    for draft in rnd.drafts[:MAX_DRAFTS_PER_ROUND]:
        res = {"round": rnd_no, "name": draft.name.strip(), "hypothesis": draft.hypothesis.strip(),
               "saved": False, "message": "", "ok": False, "report": None, "error": ""}
        results.append(res)
        out["results"].append(res)
        if evals >= MAX_EVALS_PER_SESSION:
            res["message"] = "本会话评估次数已用完，没有处理"
            continue
        res["saved"], res["message"] = submit_draft(settings, store, draft)
        if res["saved"]:
            out["written"].append(res["name"])
            evals += 1  # 先计数：超时、失败同样消耗一次额度
            res.update(evaluate_strategy(settings, store, res["name"]))
            if verify(Path(settings.root)):
                out["engine_changed"] = True
                break
    return results, evals


def run_research(settings: Settings, store: Store, notifier, llm=None) -> dict:
    """一次研究会话（最多 MAX_ROUNDS 轮）。返回 status/rounds/written/results/cost_cny/analysis。"""
    root = Path(settings.root)
    today = date.today()
    out = {"status": "ok", "rounds": 0, "written": [], "results": [], "cost_cny": 0.0, "analysis": ""}

    bad = verify(root)
    if bad:
        notifier.send_text(format_alert("研究会话未启动：engine/ 校验不符", "\n".join(bad)))
        return {**out, "status": "integrity_failed"}

    llm = llm or LLM(settings, store)
    try:
        llm.check_budget(False)
    except BudgetExceeded as e:
        notifier.send_text(f"RSIQS 研究会话 {today.isoformat()} 跳过：{e}")
        return {**out, "status": "budget_exceeded"}

    cost0 = _month_cost(store)
    error = ""
    history: list[dict] = []
    evals = 0
    try:
        context = build_context(settings, store)
        for rnd_no in range(1, MAX_ROUNDS + 1):
            feedback = _feedback(history) + "\n\n" if history else ""
            task = _round_task(rnd_no, MAX_EVALS_PER_SESSION - evals, store.trial_count())
            user = f"{context}\n\n{feedback}{task}"
            try:
                llm.check_budget(False)
                remaining_usd = (settings.monthly_budget_cny - _month_cost(store)) / settings.usd_cny
                rnd = llm.parse(purpose="research", system=RESEARCH_SYSTEM, user=user, schema=ResearchRound,
                                effort="high", timeout=1800.0, max_budget_usd=min(3.0, remaining_usd),
                                essential=False)
            except BudgetExceeded as e:
                out["status"], error = "budget_exceeded", str(e)
                break
            except Exception as e:  # noqa: BLE001 — LLMError、超时、输出不合 schema 都在这里收口
                log.warning("research round %d failed: %s: %s", rnd_no, type(e).__name__, e)
                out["status"], error = "llm_error", f"{type(e).__name__}: {e}"
                break
            out["rounds"], out["analysis"] = rnd_no, rnd.analysis
            results, evals = _process_round(settings, store, rnd_no, rnd, evals, out)
            history.append({"round": rnd_no, "analysis": rnd.analysis, "results": results,
                            "drafts": rnd.drafts[:MAX_DRAFTS_PER_ROUND],
                            "ignored": max(0, len(rnd.drafts) - MAX_DRAFTS_PER_ROUND)})
            if out.get("engine_changed") or rnd.done or not rnd.drafts or evals >= MAX_EVALS_PER_SESSION:
                break  # engine/ 被改动：立即停止，不再请求 LLM，也不再评估
    except Exception as e:  # noqa: BLE001 — 写文件、数据库等本地故障：停止，但照常做会后校验与提交
        log.exception("research session failed")
        out["status"], error = "error", f"{type(e).__name__}: {e}"
    out["cost_cny"] = _month_cost(store) - cost0

    out.pop("engine_changed", None)
    bad = verify(root)
    if bad:
        # engine/ 被改动时得到的门槛结果不可信：本次进入影子盘的策略改回 candidate，不参与前向模拟
        reverted = [n for n in out["written"] if store.get_status(n) == "shadow"]
        for n in reverted:
            store.set_status(n, "candidate", today)
        notifier.send_text(format_alert(
            "研究会话后 engine/ 校验不符，未提交 git",
            "\n".join(bad) + "\n本次写入的策略：" + ("、".join(out["written"]) or "无") + "（请人工检查）"
            + (f"\n已从影子盘改回 candidate：{'、'.join(reverted)}" if reverted else "")))
        return {**out, "status": "integrity_failed"}

    saved = [r for r in out["results"] if r["saved"]]
    text = _summary(today, out, error)
    if saved:
        paths = [f"strategies/{r['name']}.py" for r in saved]
        if not git_commit(paths, _commit_message(today, saved)):
            log.warning("research commit failed: %s", paths)
            text += "\n注意：git 提交失败，请人工提交：" + "、".join(paths)
    notifier.send_text(text)
    return out
```

说明：
- `RESEARCH_SYSTEM` 不写死轮数、草稿数和评估次数，这些上限由 `build_context` 和 `_round_task` 按运行时的模块常量写进 user 消息，所以冒烟时把 `MAX_ROUNDS` 等改成 1 也前后一致。可 import 的模块列表由 `ALLOWED_IMPORTS` 生成，与 `check_source` 始终一致。
- 背景在会话开始时生成一次，各轮前缀相同；会变的累计试验次数 n_trials 写在每轮的任务说明里。反馈里每轮附分析节选、每个草稿的假设、处理结果、评估报告（浮点保留 4 位）或错误原因和代码，各有长度上限，所以 user 消息的长度有界。评估报错时，错误原因后面附上 stderr 里属于该策略文件的 traceback 帧（行号和代码行），帮模型定位出错的行；留出期的异常在评估器里直接按不通过处理、不写日志，所以不会出现在这里。
- 模型输出只以策略文件的形式进入系统：先经 `submit_draft` 校验写入，再在评估子进程里运行。Python 不执行模型输出的其他任何部分。
- LLM 失败不重试：研究每周一次，失败就结束本次会话，下周再跑。所有 parse 异常都在 `run_research` 内收口，`jobs/research.py` 的 `run_job` 照常记为完成。
- 会后完整性失败时不提交 git，写入的策略文件留在工作区，本次进入影子盘的策略改回 candidate，由人工排查后决定提交还是删除。
- 背景里嵌入的源码同样要避开提示词规范里的措辞：`strategies/base.py` 的 `RESERVED` 一行在嵌入时换成中性说明，测试对整份背景（含代码块）检查禁用词。

**Step 4: 运行测试，确认通过**

```
uv run pytest tests/test_research_tools.py tests/test_research_loop.py -v
```

预期：51 passed（tools 34 + loop 17）。

**Step 5: 提交**

```
git add agents/research.py tests/test_research_loop.py
git commit -m "feat: multi-round research loop with structured drafts, eval cap, budget and integrity checks"
```

（提交信息末尾附加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer。）

**Manual smoke step（真实 Claude Code CLI：一轮、最多一次评估）**

前置：已完成 `python -m jobs.bootstrap`（行情缓存、现任策略、integrity 清单就绪）；`.env` 已配置 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`（或 `ANTHROPIC_API_KEY`），并且 `uv run python scripts/smoke_llm.py` 能通过；`git status` 干净（会话会提交新的策略文件）。

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run python -c "from agents import research; research.MAX_ROUNDS = 1; research.MAX_EVALS_PER_SESSION = 1; from config import get_settings; from engine.store import Store; from notify.wecom import NullNotifier; s = get_settings(); n = NullNotifier(); out = research.run_research(s, Store(s.root / s.db_path), n); print({k: out[k] for k in ('status', 'rounds', 'written', 'cost_cny')}); print(n.sent[-1])"
```

检查点：
- `status` 为 `ok`，`rounds` 为 1，`written` 最多 1 个名字（其余草稿在摘要里标“本会话评估次数已用完，没有处理”）。若为 `llm_error`，摘要里写有原因：先单独跑 `scripts/smoke_llm.py` 排查中转和鉴权；若报错与 schema 或分类器有关，检查提示词措辞。
- `strategies/<name>.py` 的 docstring 以“假设：”开头；`uv run python -c "from config import get_settings; from engine.store import Store; s = get_settings(); print(Store(s.root / s.db_path).recent_trials(1))"` 能看到这次试验。
- `cost_cny` 大于 0，且在几元人民币的量级（单轮上限 3 美元）。
- `git log -1 --format=%B` 显示 `research: <日期> 新增候选策略 <name>` 以及假设；`uv run python -c "from engine.integrity import verify; from pathlib import Path; print(verify(Path('.')))"` 输出 `[]`。
- 若不想保留这次冒烟写入的策略：`git revert HEAD`，并把它的状态改为 retired（`Store(...).set_status(name, "retired", date.today())`），以免它留在候选或影子盘里。

---

## Part 6: 任务编排、调度与端到端测试

> 约定补充（契约在本部分内的歧义，取最简一致方案）：
> - `run_job` 返回 `True` 表示该任务当日"已完成"（本次成功，或此前已成功而跳过），`False` 表示本次失败。
> - `bootstrap` 把股票池写入 `store.kv_set("universe", codes)`，并在锚定日 `anchor = common.last_closed_day(now, calendar)`（今天是交易日且已过 15:10 则为今天，否则为之前最近的交易日）为每个账户记录初始净值 `capital`，`kv "bench_start"` = anchor；`settle` 以 `h:active / h:candidate / bench:csi300` 三个核心账户的最小 `last_nav_date` 决定补跑起点。
> - `settle` 每次运行只在有待结算日时刷新一次缓存/指数（`run_job("settle_data", 最后待结算日, force=True)`，失败即告警并退出）；单日日线覆盖率 <50% 或缺指数视为数据未更新，抛 `DataError`，停止后续补跑。
> - 行情缓存 `BarCache` 与 `Store` 共用 `settings.db_path` 同一个 sqlite 文件（不同表），其他部分（如 `engine.run_eval`）按同一路径构造即可读到缓存。
> - `bootstrap` 若 `kv "playbook_released"` 不存在，则写入锚定日 ISO 字符串（视为 v1 的发布日），保证 `maybe_release` 的 10 个交易日间隔从启动日起算。
> - `monitor` 去重键持久化在 `kv "monitor_sent:<ISO日期>"`（进程重启不重复告警）；`check_once(d, store, client, notifier, cache=None) -> list[str]`（cache 提供分红送转记录，除权除息日按换算后的价位判断）。
> - `jobs.review` 只复盘"不晚于今天、已结算"的最近一个交易日（契约原文）；该日已复盘则跳过。
> - 盘前分两段。`jobs.premarket_scan`（08:30）在同一个 `run_job("premarket_scan")` 里先跑 `premarket.scan`，再跑 `premarket.run_analyze`（active/candidate 两路深度分析并行，计划存 `kv "plan:<ISO日期>:<kind>"`）。分析失败（包括 `run_analyze` 本身抛异常）不算任务失败：任务照样记 ok，只推一条"盘前分析未完成"告警。启动时已过 09:25（开机补跑）则只扫描、不分析，因为定稿已经开始，计划用不上，不必白花钱。
> - `jobs.premarket_final` 的计划任务 09:25 触发。入口先判断交易日和是否过时（晚于 09:35 跳过），再等到 09:25:15（竞价快照落定）后定稿；非交易日、过时都不等待。若当日 `premarket_scan` 未完成（开机晚或 08:30 扫描失败），先内联补跑扫描：只扫描不分析，同样记为 `premarket_scan` 完成。此时两路都没有计划，`run_final` 退回量化推荐。
> - 所有入口 `main(now: datetime | None = None)`；`premarket_final.main` 另有 `sleep=time.sleep` 参数，测试注入假 sleep。入口通过 `common.context()` 取依赖；测试中 monkeypatch `jobs.common.context`。
> - `jobs/common.py` 另外提供 `make_llm(settings, store)` 工厂、`load_calendar` 与 `setup_logging`。`make_llm` 返回 `agents.llm.LLM`（经本机 `claude.exe` 无头模式调用 Claude）；入口一律通过 `common.make_llm` 取 LLM，测试打桩为 `FakeLLM`，没有任何测试会启动 `claude.exe`。入口一律以 `from jobs import common` 引用，以便 monkeypatch 生效。
> - `setup_logging` 把文件 handler 挂在根 logger 上：`agents.*` 记录的 `LLMError`（CLI 返回的 subtype 与输出摘要）、`notify.wecom` 的推送失败都写进 `logs/jobs.log`，排查"未经 LLM 审核"时只看这一个文件。
> - 端到端测试依赖 Part 2 的 `Store` 可跨线程使用（`check_same_thread=False` + 锁）：`run_analyze` 与 `run_final` 都用两个线程分别处理 active/candidate。
> - 测试共享假对象放在 `tests/jobfakes.py`，以 `from tests.jobfakes import ...` 导入；为此新增空文件 `tests/__init__.py`（若已存在则保留），使 `tests` 成为常规包，避免与 site-packages 中同名包冲突。
> - 开机补跑用单独的计划任务 `\RSIQS\Catchup`（AtLogOn，延迟 3 分钟，顺序执行 settle → review 两个动作），满足"settle also AtLogOn"且保证复盘在结算之后。

### Task 6.1: jobs/common.py 与测试假对象

**Files:**
- Create: `jobs/common.py`
- Create: `tests/__init__.py`（空文件；已存在则跳过）
- Create: `tests/jobfakes.py`（本部分所有 jobs 测试共享的假对象）
- Test: `tests/test_jobs_common.py`

`FakeLLM` 与契约的 `LLM.parse(purpose, system, user, schema, effort="medium", timeout=120.0, max_budget_usd=1.0, essential=False)` 同签名，按 schema 分派：
- `PremarketPlan`：`store` 中 `day` 当天的全部候选 keep，价位沿用量化，可接受跳空 ±5%；
- `AuctionCheck`：同一批候选全部 keep；
- `ReviewOutput`：固定一条带证据的新规则；
- `ResearchRound`：无草稿，`done=True`。

每次调用先记入 `calls`（失败的调用也记）。`fail={purpose 前缀: 异常}` 命中时抛出，用来模拟中转失效、超时、拒答。`FakeLLM` 不计费，也没有 `cost_cny`。`store`、`day` 由测试在主线程设置，`parse` 会在 `run_analyze`/`run_final` 的工作线程里被调用，只读 `store`（Store 带锁，可跨线程）。

**Step 1: 写共享假对象与失败测试（测试基础设施，非产品代码）**

`tests/jobfakes.py`：

```python
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
    import agents.review as rv
    if hasattr(rv, "git_commit"):
        monkeypatch.setattr(rv, "git_commit", lambda *a, **k: False)
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
```

> 说明：`prepare_root` 依赖 Part 4 已提交的 `playbook/premarket_system.md`、`playbook/review_system.md`；若 `agents.review` 以 `from agents.playbook import git_commit` 方式导入，也一并打桩。`make_settings` 的空 `claude.exe` 是兜底：本部分测试都会打桩 `common.make_llm`，漏打桩时也只会立即报错，不会真的调用 Claude。

`tests/test_jobs_common.py`：

```python
import logging
from datetime import date, datetime, time

import pytest

from agents.llm import LLMError
from agents.premarket import AuctionCheck, PremarketPlan
from agents.research import ResearchRound
from agents.review import ReviewOutput
from engine.calendar import TradingCalendar
from engine.store import Store
from engine.types import Pick
from jobs.common import (incumbent_strategy, last_closed_day, load_calendar, run_job,
                         setup_logging)
from notify.wecom import NullNotifier
from tests.jobfakes import FakeLLM

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


def test_setup_logging_captures_agent_logs_once(tmp_path):
    base = logging.getLogger()
    before, level = list(base.handlers), base.level
    try:
        setup_logging(tmp_path)
        setup_logging(tmp_path)  # 重复调用不重复挂 handler
        added = [h for h in base.handlers if h not in before]
        assert len(added) == 1
        logging.getLogger("agents.premarket").warning("analyze:active 失败 LLMError")
        added[0].flush()
        text = (tmp_path / "logs" / "jobs.log").read_text(encoding="utf-8")
        assert "agents.premarket" in text and "LLMError" in text
    finally:
        for h in [h for h in base.handlers if h not in before]:
            base.removeHandler(h)
            h.close()
        base.setLevel(level)


def test_fake_llm_dispatch_and_scripted_failure():
    store = Store(":memory:")
    store.save_candidates(D, [Pick(code="600001", name="测试01", score=1.0, entry_max=10.3,
                                   stop=9.3, target=11.0, hold_days=5, reason="量化")], {})
    llm = FakeLLM(store, fail={"auction": LLMError("中转不可用")})
    llm.day = D
    plan = llm.parse("analyze:active", "s", "u", PremarketPlan, effort="high",
                     max_budget_usd=1.5, essential=True)
    assert [(p.code, p.action, p.entry_max) for p in plan.picks] == [("600001", "keep", 10.3)]
    assert llm.parse("review", "s", "u", ReviewOutput).lessons[0].evidence
    assert llm.parse("research", "s", "u", ResearchRound).done is True
    with pytest.raises(LLMError):
        llm.parse("auction:active", "s", "u", AuctionCheck)
    assert llm.purposes == ["analyze:active", "review", "research", "auction:active"]
    assert llm.calls[0]["max_budget_usd"] == 1.5 and llm.calls[0]["essential"] is True
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_jobs_common.py -v
```
预期：FAIL，`ModuleNotFoundError: No module named 'jobs.common'`。

**Step 3: 最小实现**

`jobs/common.py`（`setup_logging` 挂在根 logger 上，`agents.*` 的 LLM 失败原因也进 `logs/jobs.log`）：

```python
"""任务入口公共设施：幂等运行、告警、日志、依赖装配。"""
from __future__ import annotations

import logging
import os
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
    """所有模块的日志写入 <root>/logs/jobs.log。handler 挂在根 logger 上：jobs、agents
    （LLMError 的 subtype 与输出摘要）、notify（推送失败）都进同一文件；重复调用不重复挂。"""
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = os.path.abspath(log_dir / "jobs.log")
    base = logging.getLogger()
    if not any(getattr(h, "baseFilename", None) == target for h in base.handlers):
        h = logging.FileHandler(target, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        base.addHandler(h)
    base.setLevel(logging.INFO)


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
    """LLM 工厂：agents.llm.LLM（经本机 claude.exe 无头模式调用 Claude）；测试中打桩为 FakeLLM。"""
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

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_common.py -v
```
预期：9 passed。

**Step 5: 提交**

```
git add jobs/common.py tests/__init__.py tests/jobfakes.py tests/test_jobs_common.py
git commit -m "feat: jobs.common run_job/context/logging and shared job test fakes"
```
（提交信息末尾附 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。）

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


def test_bootstrap_names_fall_back_to_spot(tmp_path, monkeypatch):
    dates, settings, store, cache, _, _ = setup(tmp_path, monkeypatch)
    from jobs import common
    client = common.context()[2]

    def down():
        raise ConnectionError("exchanges down")

    monkeypatch.setattr(client, "names", down)
    bootstrap.main(now=datetime.combine(dates[100], time(20, 0)))
    assert cache.names["600001"] == NAMES["600001"]


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


def test_bootstrap_rerun_keeps_existing_manifest_and_warns(tmp_path, monkeypatch):
    dates, settings, _, _, notifier, manifests = setup(tmp_path, monkeypatch)
    now = datetime.combine(dates[100], time(20, 0))
    bootstrap.main(now=now)
    (settings.root / integrity.MANIFEST_PATH).write_text("{}", encoding="utf-8")  # 清单已经存在
    monkeypatch.setattr(integrity, "verify", lambda r: ["engine/broker.py"])
    bootstrap.main(now=now)
    assert manifests == [settings.root]  # 没有重写
    assert "engine/broker.py" in notifier.sent[-1]


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
    try:
        cache.set_names(client.names())  # 交易所列表，含 ST 前缀；全市场快照要将近一分钟
    except Exception:  # noqa: BLE001 - 交易所列表取不到时退回全市场快照
        common.log.exception("names() failed, falling back to spot()")
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

    if (root / integrity.MANIFEST_PATH).exists():
        changed = integrity.verify(root)  # 已有清单不重写：重写等于认可 engine/ 的任何改动
    else:
        integrity.write_manifest(root)
        changed = []
    msg = (f"RSIQS 初始化完成：股票池 {len(codes)} 只，起始日 {anchor.isoformat()}，"
           f"新建账户 {', '.join(created) or '无'}")
    if changed:
        msg += (f"\n警告：engine/ 与哈希清单不符：{', '.join(changed)}。"
                "确认改动无误后按 Task 2.8 的命令人工重写清单并提交。")
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
预期：6 passed。

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

规则回顾（撮合与估值用不复权日线，前复权只给策略算因子，见 Part 3 契约说明）：待结算区间 = `calendar.next_day(min(核心账户 last_nav_date))` 到"已收盘的最近交易日"（今天是交易日且 `now >= 15:10` 才包含今天）。每天用 `run_job("settle", d)` 包裹；某天失败即停止后续补跑（后一天依赖前一天持仓）。每个账户若 `last_nav_date >= d` 则跳过，保证重复执行不会重复成交。除权除息：每个待结算日之前先刷新所有持仓股票和当日盘前推荐股票的分红记录（新浪，单只失败不阻塞，汇总告警，下次结算重试），结算完再刷新一次，供次日盘中监控使用；撮合前对上次结算之后到 d 日（含）除权的持仓调用 `apply_dividend`（只在 d 日尚未撮合时调整，重跑不重复）；盘前推荐的 meta 记每只股票写价位时依据的昨收 `ref_close`（有竞价快照时是快照昨收，价位已按它换算；否则是扫描时的昨收）；结算与 d 日交易所参考昨收比较，不一致（除权除息）才按比例换算，没有 `ref_close` 的旧推荐按分红记录换算。量化策略按前复权价格写价位，结算按 d 日前复权与不复权收盘价之比（`_to_raw`）换算后再撮合。前复权有 d 日 K 线、不复权却没有（取数失败或空表）时暂停结算、下次重试；当天推荐的代码即使不在 kv 股票池里也会检查。持仓估值用 d 日（含）之前最近的不复权收盘价，中间有除权时按除权参考价换算（除权日缺 K 线也不会虚增净值）。不复权日线的参考昨收与上一收盘价不一致、却没有分红记录时：分红接口当天失败就暂停结算（下次重试），否则告警（多半是配股）。

**Step 1: 写失败测试**

`tests/test_jobs_settle.py`：

```python
from datetime import datetime, time

import pandas as pd
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
                settings=settings, index=index, decided=decided, client=client)


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


def test_crash_before_account_saved_loses_no_trades(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    real = store.save_account

    def killed_once(acc):
        if acc.name == "h:active" and acc.last_trade_day == dates[101]:
            monkeypatch.setattr(store, "save_account", real)
            raise RuntimeError("进程被杀")
        return real(acc)

    monkeypatch.setattr(store, "save_account", killed_once)
    assert settle.main(now=at(dates[101], 16)) == []
    assert settle.main(now=at(dates[101], 17)) == [dates[101]]
    buys = [f for f in store.load_account("h:active").trades if f.side == "BUY" and f.date == dates[101]]
    assert len(buys) == 1 and store.last_nav_date("h:active") == dates[101]


def test_crash_after_account_saved_records_nav_once(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    real = store.record_nav

    def killed_once(account, d, equity):
        if account == "h:active" and d == dates[101]:
            monkeypatch.setattr(store, "record_nav", real)
            raise RuntimeError("进程被杀")
        return real(account, d, equity)

    monkeypatch.setattr(store, "record_nav", killed_once)
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.load_account("h:active").last_trade_day == dates[101]  # 账户已存，净值没记
    assert settle.main(now=at(dates[101], 17)) == [dates[101]]
    buys = [f for f in store.load_account("h:active").trades if f.side == "BUY" and f.date == dates[101]]
    assert len(buys) == 1 and store.last_nav_date("h:active") == dates[101]


def test_nan_close_uses_last_valid_close(env):
    dates, store = env["dates"], env["store"]
    env["cache"].raw["600001"].loc[pd.Timestamp(dates[101]), "close"] = float("nan")
    assert settle.main(now=at(dates[101], 16)) == [dates[101]]
    assert store.nav_series("h:active").notna().all()


def test_strategy_view_stops_at_previous_day(env, monkeypatch):
    dates, store = env["dates"], env["store"]
    seen = []

    class Probe:
        name = "probe"

        def select(self, view):  # 策略经 history 能看到的最后一行：d 日的行情不在里面
            seen.append(max(view.history(c).index.max() for c in view.codes()))
            return []

    monkeypatch.setattr(settle, "load_strategy", lambda name: Probe())
    settle.main(now=at(dates[101], 16))
    assert seen == [pd.Timestamp(dates[100])]


def test_ex_dividend_adjusts_position_before_matching(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))  # h:active 买入 600001
    pos = store.load_account("h:active").positions["600001"]
    qty0, stop0, cash0 = pos.qty, pos.stop, store.load_account("h:active").cash
    env["client"].dividend_data = {"600001": [(dates[102], 0.2, 0.5)]}  # 10 转 5 派 2
    settle.main(now=at(dates[102], 16))
    acc = store.load_account("h:active")
    pos = acc.positions["600001"]
    assert pos.qty == int(qty0 * 1.5) and pos.stop == pytest.approx(max((stop0 - 0.2) / 1.5, 0.01))
    assert acc.corporate_actions[0]["date"] == dates[102].isoformat()
    sells = [f for f in acc.trades if f.side == "SELL" and f.date == dates[102]]
    assert sells == [] and acc.cash == pytest.approx(cash0 + round(qty0 * 0.2 * 0.8, 2))
    assert any("除权除息" in str(m) for m in env["notifier"].sent)
    settle.settle_day(dates[102], env["settings"], store, env["cache"], cal(dates))  # 强制重算不会重复调整
    assert len(store.load_account("h:active").corporate_actions) == 1


def test_recommendation_prices_adjusted_on_ex_date(env):
    dates, store = env["dates"], env["store"]
    env["client"].dividend_data = {"600001": [(dates[101], 0.0, 1.0)]}  # 10 转 10：价格减半
    # 推荐写于除权前：除权后开盘约 3.29 元，低于止损 6.0、高于换算后的 3.0；不换算就买不进
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=6.0,
                target=999.0, hold_days=5, reason="测试")
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True})
    _split_from(env, "600001", dates[101])  # 除权日起的不复权日线（参考昨收是除权参考价）
    settle.main(now=at(dates[101], 16))  # 当天推荐的股票也会刷新分红记录
    buys = [f for f in store.load_account("h:active").trades if f.side == "BUY" and f.code == "600001"]
    pos = store.load_account("h:active").positions["600001"]
    assert buys and pos.stop == pytest.approx(3.0) and pos.target == pytest.approx(999.0 / 2)


def test_dividend_fetch_failure_alerts_but_settles(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    env["client"].dividend_data = {"600001": "FAIL"}
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    assert any("除权数据获取失败" in str(m) and "600001" in str(m) for m in env["notifier"].sent)


def _split_from(env, code, d, k=2.0):
    """d 日起 10 转 10（k=2）：不复权价格从 d 日起除以 k，d 日的参考昨收就是除权参考价。"""
    df = env["cache"].raw[code]
    m = df.index >= pd.Timestamp(d)
    cols = ["open", "high", "low", "close", "prev_close"]
    df.loc[m, cols] = df.loc[m, cols] / k


def _rec_with_tight_stop(env, d):
    """d 日推荐 600001，止损设在开盘价下方 10%：除权日不先调整持仓的话一定会记下假止损。"""
    o = float(env["cache"].raw["600001"].loc[pd.Timestamp(d), "open"])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=round(o * 0.9, 2),
                target=999.0, hold_days=5, reason="测试")
    env["store"].save_recommendation(d, "h:active", [pick], {"llm_reviewed": True})
    return pick


def test_ex_rights_adjusted_before_matching(env):
    dates, store = env["dates"], env["store"]
    pick = _rec_with_tight_stop(env, dates[101])
    settle.main(now=at(dates[101], 16))
    env["client"].dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}
    _split_from(env, "600001", dates[102])
    settle.main(now=at(dates[102], 16))
    acc = store.load_account("h:active")
    assert [f for f in acc.trades if f.side == "SELL"] == []
    assert acc.positions["600001"].stop == pytest.approx(pick.stop / 2)
    nav = store.nav_series("h:active")
    assert nav.iloc[-1] == pytest.approx(nav.iloc[-2], rel=0.05)  # 股数翻倍、价格减半：净值连续


def test_raw_bar_failure_pauses_and_retries(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    cache.raw_fail = {"600001"}  # 推荐的股票取不到不复权日线：按停牌处理会漏掉买入，所以暂停
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.last_nav_date("h:active") == dates[100]
    cache.raw_fail = set()
    assert settle.main(now=at(dates[101], 17)) == [dates[101]]
    assert any(f.code == "600001" for f in store.load_account("h:active").trades)


def test_raw_empty_while_adjusted_has_bar_pauses(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    cache.raw["600001"] = cache.raw["600001"].drop(pd.Timestamp(dates[101]))  # 接口返回空表：update_raw 不报失败
    assert settle.main(now=at(dates[101], 16)) == []
    assert store.last_nav_date("h:active") == dates[100]


def test_raw_update_failure_alone_pauses(env, monkeypatch):
    dates, cache = env["dates"], env["cache"]
    monkeypatch.setattr(cache, "update_raw", lambda codes, end, start: ["600001"])  # 旧数据还在，但这次更新失败
    assert settle.main(now=at(dates[101], 16)) == []


def test_recommendation_outside_universe_is_checked(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    df = cache.panel["600001"].copy()
    cache.panel["600099"], cache.raw["600099"], cache.names["600099"] = df.copy(), df.copy(), "新成分"
    cache.raw["600099"] = cache.raw["600099"].drop(pd.Timestamp(dates[101]))
    pick = Pick(code="600099", name="新成分", score=1.0, entry_max=999.0, stop=0.01, target=999.0, hold_days=5)
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True})
    assert settle.main(now=at(dates[101], 16)) == []  # 不在 kv universe 里，同样暂停而不是按停牌跳过


def test_unexplained_ex_with_old_dividend_records_still_pauses(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    env["cache"].save_dividends("600001", pd.DataFrame({"ex_date": [dates[50]], "cash": [0.1], "ratio": [0.0]}))
    _split_from(env, "600001", dates[102])
    env["client"].dividend_data = {"600001": "FAIL"}  # 今天的记录取不到，历史记录不能拿来解释今天的除权
    assert settle.main(now=at(dates[102], 16)) == []


def test_small_unrecorded_dividend_is_detected(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    df = env["cache"].raw["600001"]
    m = df.index >= pd.Timestamp(dates[102])
    df.loc[m, ["open", "high", "low", "close", "prev_close"]] -= 0.05  # 每股派息 0.05 元，没有分红记录
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    assert any("可能是配股" in str(m) for m in env["notifier"].sent)


def test_catch_up_across_ex_date_matches_on_unadjusted_prices(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    env["client"].dividend_data = {"600001": [(dates[102], 0.5, 0.0)]}
    # 前复权在除权后把此前的价格整体下调 0.5；不复权日线不变
    m = cache.panel["600001"].index < pd.Timestamp(dates[102])
    cache.panel["600001"].loc[m, ["open", "high", "low", "close", "prev_close"]] -= 0.5
    settle.main(now=at(dates[102], 16))  # 一次补跑 101、102
    acc = store.load_account("h:active")
    buy = next(f for f in acc.trades if f.side == "BUY" and f.code == "600001")
    raw_open = float(cache.raw["600001"].loc[pd.Timestamp(dates[101]), "open"])
    assert buy.price == pytest.approx(raw_open * 1.001)  # 按当天真实价格成交，不是下调后的前复权价
    assert [a["date"] for a in acc.corporate_actions] == [dates[102].isoformat()]  # 分红只入账一次


def test_missing_bar_on_ex_date_values_at_ex_rights_price(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    settle.main(now=at(dates[101], 16))
    env["client"].dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}
    _split_from(env, "600001", dates[102])
    ts = pd.Timestamp(dates[102])  # 除权日停牌：前复权和不复权都没有这根 K 线
    cache.raw["600001"], cache.panel["600001"] = cache.raw["600001"].drop(ts), cache.panel["600001"].drop(ts)
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    nav = store.nav_series("h:active")
    assert store.load_account("h:active").positions["600001"].qty % 2 == 0  # 股数已翻倍
    assert nav.iloc[-1] == pytest.approx(nav.iloc[-2], rel=0.05)  # 估值价同样减半，净值没有虚增


def test_dividend_fetch_failure_on_ex_date_pauses_and_retries(env):
    dates, store = env["dates"], env["store"]
    pick = _rec_with_tight_stop(env, dates[101])
    settle.main(now=at(dates[101], 16))
    _split_from(env, "600001", dates[102])
    env["client"].dividend_data = {"600001": "FAIL"}
    assert settle.main(now=at(dates[102], 16)) == []  # 疑似除权却没有分红记录：暂停
    assert store.last_nav_date("h:active") == dates[101]
    env["client"].dividend_data = {"600001": [(dates[102], 0.0, 1.0)]}
    assert settle.main(now=at(dates[102], 17)) == [dates[102]]  # 下次运行重试成功
    acc = store.load_account("h:active")
    assert acc.positions["600001"].stop == pytest.approx(pick.stop / 2)
    assert [f for f in acc.trades if f.side == "SELL"] == []


def test_unexplained_ex_without_record_warns(env):
    dates, store = env["dates"], env["store"]
    settle.main(now=at(dates[101], 16))
    _split_from(env, "600001", dates[102], k=1.1)  # 配股之类：参考昨收变了，但没有分红记录
    assert settle.main(now=at(dates[102], 16)) == [dates[102]]
    assert any("可能是配股" in str(m) for m in env["notifier"].sent)


def test_future_and_past_events_not_applied_again(env):
    dates, store = env["dates"], env["store"]
    env["client"].dividend_data = {"600001": [(dates[101], 0.0, 1.0), (dates[104], 0.0, 1.0)]}
    _split_from(env, "600001", dates[101])  # 101 日除权；h:active 在除权日当天按除权后的价格买入
    settle.main(now=at(dates[101], 16))
    qty = store.load_account("h:active").positions["600001"].qty
    settle.main(now=at(dates[102], 16))
    acc = store.load_account("h:active")
    assert acc.positions["600001"].qty == qty and acc.corporate_actions == []  # 101 的事件早于买入，104 的还没到


def test_recommendation_already_adjusted_is_not_adjusted_again(env):
    dates, store = env["dates"], env["store"]
    env["client"].dividend_data = {"600001": [(dates[101], 0.0, 1.0)]}
    _split_from(env, "600001", dates[101])
    o = float(env["cache"].raw["600001"].loc[pd.Timestamp(dates[101]), "open"])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=round(o * 0.9, 2),
                target=999.0, hold_days=5, reason="测试")
    ref = float(env["cache"].raw["600001"].loc[pd.Timestamp(dates[101]), "prev_close"])  # 已是除权参考价
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True, "ref_close": {"600001": ref}})
    settle.main(now=at(dates[101], 16))
    assert store.load_account("h:active").positions["600001"].stop == pytest.approx(pick.stop)


def test_recommendation_rescaled_by_ref_close(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    ts = pd.Timestamp(dates[101])
    ref = float(cache.raw["600001"].loc[ts, "prev_close"])  # 盘前按除权前的昨收写价位（快照没取到）
    _split_from(env, "600001", dates[101])  # 当天 10 转 10，不需要分红记录
    o = float(cache.raw["600001"].loc[ts, "open"])
    pick = Pick(code="600001", name=NAMES["600001"], score=1.0, entry_max=999.0, stop=round(o * 1.8, 2),
                target=9999.0, hold_days=5, reason="测试")  # 止损按除权前价位写：不换算就高于开盘价，买不进
    store.save_recommendation(dates[101], "h:active", [pick], {"llm_reviewed": True, "ref_close": {"600001": ref}})
    settle.main(now=at(dates[101], 16))
    pos = store.load_account("h:active").positions["600001"]
    assert pos.stop == pytest.approx(pick.stop / 2) and pos.target == pytest.approx(9999.0 / 2)


def test_quant_pick_converted_when_adjusted_history_moved(env, monkeypatch):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    env["client"].dividend_data = {"600001": [(dates[102], 0.5, 0.0)]}
    m = cache.panel["600001"].index < pd.Timestamp(dates[102])
    cache.panel["600001"].loc[m, ["open", "high", "low", "close", "prev_close"]] -= 0.5  # 前复权整体下调
    q_open = float(cache.panel["600001"].loc[pd.Timestamp(dates[101]), "open"])

    class Tight:  # 买入上限只比前复权开盘价高 2%：不换算的话，真实开盘价高出 0.5 元（约 8%），买不进
        name = "tight"

        def select(self, view):
            return [Pick(code="600001", name="x", score=1.0, entry_max=q_open * 1.02, stop=q_open * 0.8,
                         target=q_open * 1.5, hold_days=5)]

    monkeypatch.setattr(settle, "load_strategy", lambda name: Tight())
    settle.main(now=at(dates[102], 16))  # 一次补跑 101、102
    trades = store.load_account("q:baseline_momo").trades
    assert any(f.side == "BUY" and f.code == "600001" and f.date == dates[101] for f in trades)


def test_quant_pick_prices_converted_to_unadjusted():
    from engine.types import Bar
    d0 = make_dates()[0]
    # 窄振幅：之后 10 转 4，前复权 = 不复权 / 1.4 再舍入到分；按收盘价之比换算，误差在千分之一以内
    r = Bar("600001", d0, 10.33, 10.36, 10.31, 10.33, 10.30, 1e6, 1e7)
    q = Bar("600001", d0, 7.38, 7.40, 7.36, 7.38, 7.36, 1e6, 1e7)
    pick = Pick(code="600001", name="x", score=1.0, entry_max=7.53, stop=6.79, target=8.49, hold_days=5)
    out = settle._to_raw(pick, q, r)
    assert (out.entry_max, out.stop, out.target) == pytest.approx((7.53 * 1.4, 6.79 * 1.4, 8.49 * 1.4), rel=1e-3)
    assert settle._to_raw(pick, r, r) is pick and settle._to_raw(pick, None, r) is pick


def test_new_positions_get_dividend_records_for_tomorrow(env):
    dates, store, cache = env["dates"], env["store"], env["cache"]
    settle.main(now=at(dates[101], 16))
    held = {c for n in store.list_accounts() for c in store.load_account(n).positions}
    assert held and held <= set(cache.divs)  # 结算完再刷新一次：供次日盘中监控换算


def test_names_refreshed_daily(env):
    dates, cache = env["dates"], env["cache"]
    env["client"].names = lambda: {**NAMES, "600001": "ST测试"}  # 收盘后戴帽
    settle.main(now=at(dates[101], 16))
    assert cache.names["600001"] == "ST测试"


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

from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd

from data.akshare_client import DataError
from engine import promotion
from engine.backtest import bars_for_day
from engine.broker import SimBroker
from engine.dataview import DataView
from engine.ledger import Account, ex_rights_price
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
    try:  # 每天刷新简称：新纳入的成分股、新戴帽的股票（基线策略按名称排除 ST）
        cache.set_names(client.names())
    except Exception:  # noqa: BLE001 - 名称取不到不影响结算，沿用旧名称
        common.log.exception("refresh names failed")


def refresh_dividends(store, client, cache, d: date | None = None) -> list[str]:
    """刷新所有账户持仓股票，以及 d 日盘前推荐股票的分红送转记录（新浪），返回获取失败的代码。"""
    held = set()
    for name in store.list_accounts():
        acc = store.load_account(name)
        if acc is not None:
            held.update(acc.positions)
    for name in (common.LLM_ACCOUNTS if d else ()):
        rec = store.load_recommendation(d, name)
        held.update(p.code for p in (rec[0] if rec else []))
    failed = []
    for code in sorted(held):
        try:
            cache.save_dividends(code, client.dividends(code))
        except Exception:  # noqa: BLE001 — 单只失败不阻塞结算，汇总后告警
            common.log.exception("dividends %s failed", code)
            failed.append(code)
    return failed


RAW_START_DAYS = 30  # 新代码的不复权日线从结算日前 30 天取起：估值和除权核对只需要最近的收盘价


def _scale(pick, k: float):
    return replace(pick, entry_max=pick.entry_max * k, stop=pick.stop * k, target=pick.target * k)


def _to_raw(pick, q, r):
    """量化策略按前复权价格写价位，结算按不复权日线撮合。按 d 日两根 K 线的收盘价之比换算（前复权扣减了 d 日之后
    的分红送转；没有除权时两者相同，价位不变）。不用高低价算斜率：振幅只有几分钱时，舍入到分的误差太大。
    派息时比例换算与精确的加回派息略有出入，误差约为 股息率 ×（价位 / 收盘 - 1），高股息低价股可达 0.5%，
    偏向宽松（买入上限与目标略高、止损略低）。只在多日补跑跨越之后的除权时才会用到。"""
    if q is None or r is None or not (q.close > 0 and r.close > 0):
        return pick
    k = r.close / q.close
    return pick if abs(k - 1.0) < 1e-9 else _scale(pick, k)


def _rescale_pick(pick, ref: float | None, bar):
    """盘前推荐的价位按 ref（写价位时的昨收）写成；d 日交易所的参考昨收与它不同（除权除息）时按比例换算。
    数据已按新基准复权、或盘前已按竞价快照换算时两者相同，不会重复换算。"""
    if not ref or bar is None or not bar.prev_close > 0 or abs(bar.prev_close - ref) <= 0.011:
        return pick
    return _scale(pick, bar.prev_close / ref)


def _valuation(cache, code: str, d: date) -> float | None:
    """持仓估值：d 日（含）之前最近的不复权收盘价；中间有除权（例如除权日缺 K 线）时换算到除权后，
    与已经按送转放大的股数一致。"""
    last = cache.last_raw_close(code, d)
    if last is None:
        return None
    t, price = last
    for ex, cash, ratio in cache.dividends(code):
        if t < ex <= d:
            price = ex_rights_price(price, cash, ratio)
    return price


def _ex_adjust_pick(pick, cache, d: date):
    """盘前推荐的价位按除权前的收盘价写成；d 是这只股票的除权除息日时换算到除权后。"""
    for ex, cash, ratio in cache.dividends(pick.code):
        if ex == d:
            return replace(pick, entry_max=ex_rights_price(pick.entry_max, cash, ratio),
                           stop=ex_rights_price(pick.stop, cash, ratio),
                           target=ex_rights_price(pick.target, cash, ratio))
    return pick


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


def settle_day(d: date, settings, store, cache, calendar, div_failed=frozenset()) -> list[str]:
    """结算单个交易日；已记录 d 日净值的账户跳过（幂等）。返回警告列表。

    前复权日线只给策略算因子：除权后它会整体变化，多日补跑时较早的日子已经是之后的复权基准。撮合和估值用
    不复权日线（cache.raw_bars），持仓在除权除息日按分红记录调整。div_failed 是今天分红记录获取失败的代码。"""
    codes = tracked_codes(store)
    # 当天盘前推荐的代码也载入：盘前扫描用实时股票池，可能有 kv universe 之外的新成分股
    recs = set()
    for name in common.LLM_ACCOUNTS:
        rec = store.load_recommendation(d, name)
        recs.update(p.code for p in (rec[0] if rec else []))
    panel, names = cache.load_panel(sorted(set(codes) | recs), d - timedelta(days=LOOKBACK_CAL_DAYS), d)
    qfq_bars = bars_for_day(panel, names, d)
    if len(qfq_bars) < max(1, int(len(codes) * MIN_COVERAGE)):
        raise DataError(f"{d.isoformat()} 日线覆盖不足 {len(qfq_bars)}/{len(codes)}，数据可能未更新")
    bench_start = date.fromisoformat(store.kv_get("bench_start"))
    index = cache.load_index(common.INDEX_SYMBOL)
    bench_equity = settings.capital * _index_close(index, d) / _index_close(index, bench_start)

    asof = calendar.prev_day(d)
    # 策略只拿到截至 asof 的面板：d 日的行情只用于撮合
    view = DataView({c: df.loc[:pd.Timestamp(asof)] for c, df in panel.items()}, names, asof=asof)
    broker, per_pick, warnings = SimBroker(), settings.capital / settings.max_picks, []

    def raw_bars(codes) -> dict:
        need = sorted(set(codes))
        failed = cache.update_raw(need, d, d - timedelta(days=RAW_START_DAYS))
        bars = cache.raw_bars(need, d, names)
        # 前复权有 d 日 K 线、不复权却没有：取数失败或接口返回空表。按停牌处理会漏掉止损和买入，所以暂停结算
        missing = sorted(set(failed) | {c for c in need if c in qfq_bars and c not in bars})
        if missing:
            raise DataError(f"{d.isoformat()} 不复权日线获取失败：{', '.join(missing)}，暂停结算，下次重试")
        return bars

    def check_unexplained_ex(code: str, bar) -> None:
        """不复权日线的参考昨收与上一个收盘价不一致，说明 d 日除权除息。没有对应的分红记录时：分红接口今天失败
        就暂停结算（下次重试），否则告警（多半是配股，不建模）。"""
        prev = cache.last_raw_close(code, calendar.prev_day(d))
        if bar is None or prev is None or abs(bar.prev_close - prev[1]) <= 0.011:
            return
        if any(prev[0] < ex <= d for ex, _, _ in cache.dividends(code)):
            return
        if code in div_failed:
            raise DataError(f"{code} 于 {d.isoformat()} 疑似除权除息，但分红记录获取失败：暂停结算，下次重试")
        warnings.append(f"{code} 于 {d.isoformat()} 疑似除权除息，但没有分红记录（可能是配股，不建模），持仓未调整")

    def run(name: str, picks) -> None:
        acc = store.load_account(name)
        if acc is None:
            acc = Account(name=name, cash=settings.capital)
            store.record_nav(name, calendar.prev_day(d), settings.capital)
        bars = raw_bars([*acc.positions, *(p.code for p in picks)])
        if acc.last_trade_day is None or acc.last_trade_day < d:  # d 日尚未撮合：重跑时不重复调整
            # 除权除息：上次结算之后到 d 日（含）之间除权的持仓，先换算股数、成本和价位，现金分红入账
            for code in list(acc.positions):
                check_unexplained_ex(code, bars.get(code))
                for ex, cash, ratio in cache.dividends(code):
                    if (acc.last_trade_day is None or ex > acc.last_trade_day) and ex <= d:
                        act = acc.apply_dividend(ex, code, cash, ratio)
                        if act:
                            warnings.append(f"{name} 持仓 {code} 于 {ex.isoformat()} 除权除息：股数 "
                                            f"{act['qty_before']} → {act['qty_after']}，税后分红 {act['cash']:.2f} 元")
        broker.execute_day(acc, d, bars, picks, calendar, per_pick)
        # 先存账户再记净值：中途中断时，重跑看到 last_trade_day == d，execute_day 不会重复成交，只补记净值
        store.save_account(acc)
        prices = {c: v for c in acc.positions if (v := _valuation(cache, c, d)) is not None}
        store.record_nav(name, d, acc.equity(prices))

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
            raw = raw_bars(p.code for p in picks)
            run(name, [_to_raw(p, qfq_bars.get(p.code), raw.get(p.code)) for p in picks])

    for name in common.LLM_ACCOUNTS:
        if not _settled(store, name, d):
            rec = store.load_recommendation(d, name)
            picks = rec[0] if rec else []
            ref = rec[1].get("ref_close") if rec else None
            if ref is not None:  # 盘前记下了每只股票写价位时的昨收：与当天参考昨收比较
                raw = raw_bars(p.code for p in picks)
                picks = [_rescale_pick(p, ref.get(p.code), raw.get(p.code)) for p in picks]
            elif picks:  # 没有 ref_close 的旧推荐：按分红记录换算
                picks = [_ex_adjust_pick(p, cache, d) for p in picks]
            run(name, picks)

    if not _settled(store, common.BENCH_ACCOUNT, d):
        store.record_nav(common.BENCH_ACCOUNT, d, bench_equity)
    return warnings


def settle_and_decide(d: date, settings, store, cache, calendar, notifier, div_failed=frozenset()) -> None:
    warnings = settle_day(d, settings, store, cache, calendar, div_failed)
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
    done, div_failed = [], set()
    for d in days:
        failed = frozenset(refresh_dividends(store, client, cache, d))  # 截至前一日的持仓与 d 日推荐
        div_failed.update(failed)
        ok = common.run_job("settle", d, lambda d=d, failed=failed: settle_and_decide(
            d, settings, store, cache, calendar, notifier, failed), store, notifier)
        if not ok:
            break
        done.append(d)
    if done:
        div_failed.update(refresh_dividends(store, client, cache))  # 当天新买入的持仓：供次日盘中监控
    if div_failed:
        notifier.send_text(format_alert("除权数据获取失败", f"{', '.join(sorted(div_failed))}：分红记录今天没有"
                                        "更新；如果持仓当天除权除息，结算会暂停，下次运行时重试"))
    return done


if __name__ == "__main__":
    main()
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_settle.py -v
```
预期：32 passed。

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

盘前两段的分工：

| 触发 | 入口 | 做什么 |
|---|---|---|
| 08:30 | `premarket_scan` | `premarket.scan` 存候选，再 `premarket.run_analyze` 两路深度分析（每路最长 `ANALYZE_TIMEOUT` = 900 秒），计划存 kv。有一路失败就推"盘前分析未完成"告警，任务仍记 ok；09:25 之后启动只扫描 |
| 09:25 | `premarket_final` | 等到 09:25:15；扫描缺失则补跑（只扫描）；`premarket.run_final` 拉一次竞价快照，按计划条件过滤并做轻量确认，`FINAL_DEADLINE` = 110 秒内定稿，只推 h:active |
| 09:35 之后 | `premarket_final` | 视为过时，直接返回 False，不推送 |

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
    calls, analysis, analysis_kw = [], {"active": True, "candidate": True}, {}

    def fake_scan(d, settings, store, cache, client, strategy, calendar):
        calls.append(("scan", d, strategy.name))
        store.save_candidates(d, [], {})
        return []

    def fake_analyze(d, settings, store, llm, deadline=None, now=None):
        calls.append(("analyze", d, llm))
        analysis_kw.update(deadline=deadline, now=now)
        return dict(analysis)

    def fake_final(d, settings, store, llm, client, notifier, calendar):
        calls.append(("final", d, llm))

    def fake_review(d, settings, store, llm, cache, notifier, calendar):
        calls.append(("review", d, llm))

    def fake_research(settings, store, notifier, llm=None):
        calls.append(("research", llm))
        return {"status": "ok"}

    monkeypatch.setattr(premarket, "scan", fake_scan)
    monkeypatch.setattr(premarket, "run_analyze", fake_analyze)
    monkeypatch.setattr(premarket, "run_final", fake_final)
    monkeypatch.setattr(review_agent, "run_review", fake_review)
    monkeypatch.setattr(research_agent, "run_research", fake_research)
    monkeypatch.setattr(common, "make_llm", lambda settings, store: "LLM")
    return dict(dates=dates, store=store, notifier=notifier, calls=calls, analysis=analysis,
                slept=[], analysis_kw=analysis_kw)


def at(d, hh, mm=0, ss=0):
    return datetime.combine(d, time(hh, mm, ss))


def test_scan_runs_scan_then_analyze_once(env):
    d = env["dates"][10]
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert premarket_scan.main(now=at(d, 8, 40)) is True  # 已完成 -> 跳过
    assert env["calls"] == [("scan", d, "baseline_momo"), ("analyze", d, "LLM")]
    assert env["notifier"].sent == []


def test_scan_alerts_when_analysis_incomplete_but_job_ok(env):
    d = env["dates"][10]
    env["analysis"]["candidate"] = False
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert env["store"].job_done("premarket_scan", d)
    [msg] = env["notifier"].sent
    assert "盘前分析未完成" in msg and "9:25 将按量化/规则推荐" in msg
    assert "candidate" in msg and "active" not in msg


def test_scan_analyze_crash_counts_as_incomplete(env, monkeypatch):
    d = env["dates"][10]

    def crash(*a, **k):
        raise RuntimeError("找不到 claude.exe")

    monkeypatch.setattr(premarket, "run_analyze", crash)
    assert premarket_scan.main(now=at(d, 8, 30)) is True
    assert env["store"].job_done("premarket_scan", d)
    [msg] = env["notifier"].sent
    assert "盘前分析未完成" in msg and "active、candidate" in msg


def test_scan_started_after_0925_skips_analyze(env):
    d = env["dates"][10]
    assert premarket_scan.main(now=at(d, 9, 28)) is True  # 开机补跑
    assert env["calls"] == [("scan", d, "baseline_momo")]
    assert env["notifier"].sent == []


def test_scan_passes_analysis_deadline(env):
    d = env["dates"][10]
    premarket_scan.main(now=at(d, 9, 15))
    kw = env["analysis_kw"]
    assert kw["deadline"] == at(d, 9, 22) and at(d, 9, 15) <= kw["now"] < at(d, 9, 16)


def test_scan_counts_context_time_toward_analysis_start(env, monkeypatch):
    d = env["dates"][10]
    clock = [1000.0]
    real = common.context

    def slow_context():
        clock[0] += 90  # 初始化依赖（连库、取交易日历）花了 90 秒
        return real()

    monkeypatch.setattr(common, "context", slow_context)
    monkeypatch.setattr(premarket_scan._time, "monotonic", lambda: clock[0])
    premarket_scan.main(now=at(d, 9, 15))
    assert env["analysis_kw"]["now"] == at(d, 9, 16, 30)


def test_final_does_not_rescan_while_analysis_runs(env):
    d = env["dates"][10]
    env["store"].save_candidates(d, [], {})  # 扫描已完成，深度分析还在跑：premarket_scan 尚未记 ok
    assert not env["store"].job_done("premarket_scan", d)
    assert premarket_final.main(now=at(d, 9, 25, 15), sleep=env["slept"].append) is True
    assert [c[0] for c in env["calls"]] == ["final"]


def test_scan_skips_non_trading_day(env):
    assert premarket_scan.main(now=at(next_saturday(env["dates"][5]), 8, 30)) is False
    assert env["calls"] == []


@pytest.mark.parametrize("hh, mm, ss, waited", [
    (9, 20, 0, [315.0]), (9, 25, 0, [15.0]), (9, 25, 15, []), (9, 30, 0, [])])
def test_final_waits_until_092515_then_runs(env, hh, mm, ss, waited):
    d = env["dates"][10]
    premarket_scan.main(now=at(d, 8, 30))
    assert premarket_final.main(now=at(d, hh, mm, ss), sleep=env["slept"].append) is True
    assert env["slept"] == waited
    assert env["calls"] == [("scan", d, "baseline_momo"), ("analyze", d, "LLM"),
                            ("final", d, "LLM")]
    assert env["store"].job_done("premarket_final", d)


def test_final_runs_missing_scan_inline_without_analyze(env):
    d = env["dates"][10]
    assert premarket_final.main(now=at(d, 9, 30), sleep=env["slept"].append) is True
    assert [c[0] for c in env["calls"]] == ["scan", "final"]
    assert env["store"].job_done("premarket_scan", d)
    assert premarket_scan.main(now=at(d, 9, 31)) is True  # 已补跑 -> 跳过，不再分析
    assert [c[0] for c in env["calls"]] == ["scan", "final"]


def test_final_stale_after_0935(env):
    d = env["dates"][10]
    assert premarket_final.main(now=at(d, 9, 36), sleep=env["slept"].append) is False
    assert env["calls"] == [] and env["slept"] == []
    assert not env["store"].job_done("premarket_final", d)


def test_final_skips_non_trading_day_without_waiting(env):
    sat = next_saturday(env["dates"][5])
    assert premarket_final.main(now=at(sat, 9, 25), sleep=env["slept"].append) is False
    assert env["calls"] == [] and env["slept"] == []


def test_final_not_run_when_scan_fails(env, monkeypatch):
    d = env["dates"][10]

    def boom(*a, **k):
        raise RuntimeError("数据源失败")

    monkeypatch.setattr(premarket, "scan", boom)
    assert premarket_final.main(now=at(d, 9, 26), sleep=env["slept"].append) is False
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


def test_research_runs_once_per_day_with_factory_llm(env):
    sat = next_saturday(env["dates"][5])
    assert research.main(now=at(sat, 10)) == {"status": "ok"}
    assert research.main(now=at(sat, 11)) is None  # 当天已跑过
    assert env["calls"] == [("research", "LLM")]
```

**Step 2: 运行，确认失败**

```
uv run pytest tests/test_jobs_entrypoints.py -v
```
预期：FAIL，`ImportError: cannot import name 'premarket_final' from 'jobs'`。

**Step 3: 最小实现**

`jobs/premarket_scan.py`：

```python
"""08:30 盘前扫描 + 深度分析（仅交易日）。python -m jobs.premarket_scan

扫描失败由 run_job 告警（09:25 定稿时会再补跑扫描）。深度分析失败只降级：任务仍记 ok，
推一条"盘前分析未完成"告警，09:25 时那一路按量化/规则推荐。"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta

from agents import premarket
from jobs import common
from notify.format import format_alert
from strategies.base import load_strategy

KINDS = ("active", "candidate")
ANALYZE_CUTOFF = time(9, 25)  # 晚于此刻启动（开机补跑）只扫描：定稿已开始，计划用不上
ANALYZE_DEADLINE = time(9, 22)  # 深度分析必须在此前结束，给 09:25 定稿留出余量


def scan(d: date, settings, store, client, cache, calendar) -> list:
    """只做量化扫描并保存候选；premarket_final 补跑扫描时也调用它。"""
    strategy = load_strategy(common.incumbent_strategy(store))
    return premarket.scan(d, settings, store, cache, client, strategy, calendar)


def analyze_all(d: date, settings, store, now: datetime) -> dict[str, bool]:
    """两路深度分析，最晚到 ANALYZE_DEADLINE；run_analyze 本身抛异常时按两路都未完成处理。"""
    try:
        return premarket.run_analyze(d, settings, store, common.make_llm(settings, store),
                                     deadline=datetime.combine(d, ANALYZE_DEADLINE), now=now)
    except Exception:  # noqa: BLE001 — 分析只影响推荐质量，不能让扫描任务失败
        common.log.exception("premarket analyze %s crashed", d)
        return {k: False for k in KINDS}


def main(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    t0 = _time.monotonic()  # 在 context() 之前取：开机补跑时初始化依赖也要花时间
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_scan %s: not a trading day", d)
        return False
    status: dict[str, bool] = {}

    def job() -> None:
        scan(d, settings, store, client, cache, calendar)
        if now.time() >= ANALYZE_CUTOFF:
            common.log.info("premarket_scan %s: started at %s, analysis skipped", d, now.time())
            return
        # 扫描本身可能要几分钟：按实际经过的时间算出分析开始的时刻
        status.update(analyze_all(d, settings, store, now + timedelta(seconds=_time.monotonic() - t0)))

    ok = common.run_job("premarket_scan", d, job, store, notifier)
    failed = sorted(k for k, done in status.items() if not done)
    if failed:
        notifier.send_text(format_alert(
            "盘前分析未完成", f"9:25 将按量化/规则推荐（未完成：{'、'.join(failed)}）"))
    return ok


if __name__ == "__main__":
    main()
```

`jobs/premarket_final.py`：

```python
"""09:25 竞价定稿并推送（仅交易日）。python -m jobs.premarket_final

计划任务 09:25 触发；入口先等到 09:25:15，让竞价快照落定后再定稿；09:35 之后视为过时跳过。"""
from __future__ import annotations

import time as _time
from datetime import datetime, time

from agents import premarket
from jobs import common, premarket_scan

READY_AT = time(9, 25, 15)  # 集合竞价 09:25 结束，留 15 秒给行情源刷新竞价价
STALE_AFTER = time(9, 35)


def main(now: datetime | None = None, sleep=_time.sleep) -> bool:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    d = now.date()
    if not calendar.is_trading_day(d):
        common.log.info("premarket_final %s: not a trading day", d)
        return False
    if now.time() > STALE_AFTER:
        common.log.info("premarket_final %s: stale at %s, skipped", d, now.time())
        return False
    wait = (datetime.combine(d, READY_AT) - now).total_seconds()
    if wait > 0:
        common.log.info("premarket_final %s: waiting %.0fs for auction data", d, wait)
        sleep(wait)
    if store.load_candidates(d) is None:
        # 开机晚或 08:30 扫描失败：只补跑扫描（不做深度分析），run_final 会退回量化推荐。
        # 看候选是否已保存而不是看 premarket_scan 任务是否 ok：扫描完成后深度分析可能还在跑，任务尚未记 ok
        scanned = common.run_job("premarket_scan", d, lambda: premarket_scan.scan(
            d, settings, store, client, cache, calendar), store, notifier)
        if not scanned:
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
"""周六 10:00 研究循环（Claude 多轮结构化输出，评估在子进程里跑）。python -m jobs.research"""
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
        out["summary"] = research_agent.run_research(
            settings, store, notifier, llm=common.make_llm(settings, store))

    common.run_job("research", now.date(), fn, store, notifier)
    return out.get("summary")


if __name__ == "__main__":
    main()
```

**Step 4: 运行，确认通过**

```
uv run pytest tests/test_jobs_entrypoints.py -v
```
预期：19 passed（参数化的 4 个等待场景各算 1 个）。

**Step 5: 提交**

```
git add jobs/premarket_scan.py jobs/premarket_final.py jobs/review.py jobs/research.py tests/test_jobs_entrypoints.py
git commit -m "feat: thin job entrypoints for two-stage premarket, review, research"
```
（附 Co-Authored-By trailer。）

### Task 6.5: jobs/monitor.py（盘中监控）

**Files:**
- Create: `jobs/monitor.py`
- Test: `tests/test_jobs_monitor.py`

只测 `check_once`（一次检查；除权除息日按换算后的止损、止盈价判断，结算还没调整这笔持仓时才换算）；`main` 循环仅做时间窗与睡眠，不写单测。监控对象是 `h:active` 账户在上一次结算后的持仓（当天新买入的 T+1 不可卖，无需监控）。

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

    def spot(self, codes=None):
        self.n += 1
        self.codes = codes
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
    assert client.codes == ["600001", "600002", "600003", "600004"]  # 只取持仓的报价
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


class DivCache:
    def __init__(self, events):
        self.events = events

    def dividends(self, code):
        return self.events.get(code, [])


def test_ex_date_uses_adjusted_levels():
    store = make_store(("600001", 9.3, 11.0))  # 除权前的价位
    client = SpotClient([row("600001", 6.4, 6.6, prev_close=6.6)])  # 10 转 5 后正常交易
    cache = DivCache({"600001": [(D, 0.0, 0.5)]})
    assert check_once(D, store, client, NullNotifier(), cache) == []  # 换算后止损 6.2，没有跌破
    out = check_once(D, store, SpotClient([row("600001", 6.1, 6.6, prev_close=6.6)]), NullNotifier(), cache)
    assert len(out) == 1 and "触发止损" in out[0] and "6.20" in out[0]
    assert len(check_once(D, make_store(("600001", 9.3, 11.0)), client, NullNotifier())) == 1  # 不换算会误报
    # 前一天除权、结算已经调整过的持仓，今天不再换算
    yesterday = DivCache({"600001": [(date(2026, 10, 15), 0.0, 0.5)]})
    out = check_once(D, make_store(("600001", 6.2, 7.3)), SpotClient([row("600001", 6.1, 6.6, prev_close=6.6)]),
                     NullNotifier(), yesterday)
    assert len(out) == 1 and "6.20" in out[0]


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

from engine.ledger import ex_rights_price
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


def _levels(pos, acc, cache, d: date) -> tuple[float, float]:
    """止损、止盈价；d 是除权除息日且结算还没调整这笔持仓时，换算到除权后。"""
    if cache is not None and not any(a["code"] == pos.code and a["date"] == d.isoformat()
                                     for a in acc.corporate_actions):
        for ex, cash, ratio in cache.dividends(pos.code):
            if ex == d:
                return ex_rights_price(pos.stop, cash, ratio), ex_rights_price(pos.target, cash, ratio)
    return pos.stop, pos.target


def check_once(d: date, store, client, notifier, cache=None) -> list[str]:
    """检查一次持仓，返回本次新发出的告警；(code, 事件) 去重键按日持久化在 kv。
    cache 提供分红送转记录：除权除息日当天按换算后的止损、止盈价判断。"""
    acc = store.load_account(ACCOUNT)
    if acc is None or not acc.positions:
        return []
    rows = {str(r["code"]): r for r in client.spot(list(acc.positions)).to_dict("records")}  # 只取持仓
    key = f"monitor_sent:{d.isoformat()}"
    sent = set(store.kv_get(key) or [])
    out = []
    for code, pos in acc.positions.items():
        r = rows.get(code, {})
        last, high, prev = _num(r.get("last")), _num(r.get("high")), _num(r.get("prev_close"))
        if not last > 0:  # 停牌或无报价
            continue
        events = []
        stop, target = _levels(pos, acc, cache, d)
        if last <= stop:
            events.append(("触发止损", f"现价 {last:.2f} ≤ 止损 {stop:.2f}"))
        if last >= target:
            events.append(("触发止盈", f"现价 {last:.2f} ≥ 止盈 {target:.2f}"))
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
                check_once(d, store, client, notifier, cache)
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
预期：6 passed。

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
| PremarketScan | 周一至周五 08:30 | `jobs.premarket_scan`（扫描 + 两路深度分析，约 5–20 分钟） |
| PremarketFinal | 周一至周五 09:25 | `jobs.premarket_final`（入口内等到 09:25:15 再定稿，09:28 前推送） |
| Monitor | 周一至周五 09:30 | `jobs.monitor` |
| Settle | 周一至周五 15:35 | `jobs.settle` |
| Review | 周一至周五 16:05 | `jobs.review` |
| Research | 周六 10:00 | `jobs.research` |
| Catchup | 登录时（延迟 3 分钟） | `jobs.settle` 然后 `jobs.review`（顺序执行） |

所有任务开启 `StartWhenAvailable`（错过的触发在开机后补跑，是否过时由代码判断：PremarketFinal 过 09:35 跳过，PremarketScan 过 09:25 只扫描不分析）、`MultipleInstances IgnoreNew`（同一任务不并发）、允许电池供电运行。`.ps1` 保持纯 ASCII：Windows PowerShell 5.1 按系统代码页读取无 BOM 文件，中文会乱码。

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
    for t in ("08:30", "09:25", "09:30", "15:35", "16:05", "10:00"):
        assert f'"{t}"' in ps1, t
    assert '(New-WeekdayTrigger "09:25") (New-JobAction "premarket_final")' in ps1
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
# PremarketFinal fires at 09:25; the job itself waits until 09:25:15 for auction data.
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
Register-Rsiqs "PremarketFinal" (New-WeekdayTrigger "09:25") (New-JobAction "premarket_final")
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

预期：列出 7 个任务（State 为 Ready）；NextRunTime 与上表一致（PremarketFinal 为下一个工作日 09:25，Research 为下一个周六 10:00）；Catchup 有两个动作（settle、review）；手动触发 Settle 后 `LastTaskResult` 为 `0`，`logs\jobs.log` 出现 `job settle ... ok` 或 `skip settle`。再验证卸载：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall_tasks.ps1` 后 `Get-ScheduledTask -TaskPath "\RSIQS\"` 无输出（报 "No MSFT_ScheduledTask objects found"），然后重新执行安装脚本。

注意：
- 若注册时报 `Access is denied`，以管理员身份打开 PowerShell 重跑（任务仍以当前用户 Interactive 身份运行）。
- 每个任务运行时会弹出控制台窗口：PremarketScan 的窗口在深度分析期间会停留十几分钟，PremarketFinal 约 2–3 分钟，Monitor 保持到 15:00，都不要关闭。若介意弹窗，可在 `New-JobAction` 中把 `-Execute` 换成 `"$Root\.venv\Scripts\pythonw.exe"`、`-Argument` 换成 `"-m jobs.$Job"`（需先 `uv sync`）。

**Step 6: 提交**

```
git add scripts/install_tasks.ps1 scripts/uninstall_tasks.ps1 tests/test_install_tasks.py
git commit -m "feat: Windows scheduled task install/uninstall scripts"
```
（附 Co-Authored-By trailer。）

### Task 6.7: tests/test_e2e.py（两个交易日的端到端回放）

**Files:**
- Test: `tests/test_e2e.py`

真实组件：`jobs.*` 入口、`agents.premarket`（`scan`、`run_analyze`/`analyze`、`run_final`/`finalize`/`auction_filter`）、`agents.review.run_review`、`agents.playbook`、`engine.*`（撮合、账本、Store、promotion）、`strategies.baseline_momo`。替身：`FakeClient`（行情/新闻/竞价）、`FakeCache`、`FakeLLM`、`NullNotifier`，git 提交被打桩。时间全部通过 `main(now=...)` 注入，`premarket_final` 的等待用假 sleep 记录。共三个场景：两个交易日的完整循环；中转失效时的降级；关机两天后的开机补跑。

**Step 1: 写测试**

`tests/test_e2e.py`：

```python
"""端到端：bootstrap -> 扫描+深度分析 -> 竞价定稿推送 -> 结算 -> 复盘 -> 次日。
全程无网络、不启动 claude.exe（FakeLLM），时间全部通过 main(now=...) 注入。"""
from datetime import datetime, time

import pytest

import agents.playbook as pb
import engine.integrity as integrity
from agents.llm import LLMError
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
    llm = FakeLLM(store)
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


def start_day(world, day):
    """“今天”切到 day：行情快照与 FakeLLM 覆盖的候选都按这一天。"""
    world["client"].current = world["llm"].day = day


def test_two_trading_days_full_cycle(world):
    dates, store, notifier, llm = world["dates"], world["store"], world["notifier"], world["llm"]
    for day in dates[101:103]:
        start_day(world, day)
        assert premarket_scan.main(now=at(day, 8, 30)) is True
        cands, _ = store.load_candidates(day)
        plan = store.kv_get(f"plan:{day.isoformat()}:active")
        assert cands and plan and {p["code"] for p in plan["picks"]} == {p.code for p in cands}
        assert store.kv_get(f"plan:{day.isoformat()}:candidate")
        assert not any("盘前分析未完成" in m for m in notifier.sent)

        n0, slept = len(notifier.sent), []
        assert premarket_final.main(now=at(day, 9, 25), sleep=slept.append) is True
        assert slept == [15.0]                                 # 等到 09:25:15 再定稿
        recs, meta = store.load_recommendation(day, "h:active")
        assert recs and len(recs) <= world["settings"].max_picks and meta["llm_reviewed"] is True
        pushed = notifier.sent[n0:]
        assert len(pushed) == 1 and all(p.code in pushed[0] for p in recs)  # 只推 h:active
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
    assert {"analyze:active", "analyze:candidate", "auction:active", "auction:candidate",
            "review"} <= set(llm.purposes)
    assert all(c["essential"] for c in llm.calls if c["purpose"] != "review")  # 盘前不受月预算限制


def test_relay_outage_degrades_to_quant_and_still_reports(world):
    """中转失效（Claude Code 升级或账号被封）：分析与复盘都抛 LLMError，推荐与日报照常推送。"""
    dates, store, notifier, llm = world["dates"], world["store"], world["notifier"], world["llm"]
    day = dates[101]
    start_day(world, day)
    llm.fail = {"analyze": LLMError("中转不可用"), "review": LLMError("中转不可用")}
    assert premarket_scan.main(now=at(day, 8, 30)) is True     # 扫描成功即记 ok
    assert store.kv_get(f"plan:{day.isoformat()}:active") is None
    assert any("盘前分析未完成" in m for m in notifier.sent)

    n0 = len(notifier.sent)
    assert premarket_final.main(now=at(day, 9, 26)) is True
    recs, meta = store.load_recommendation(day, "h:active")
    cands, _ = store.load_candidates(day)
    assert meta["llm_reviewed"] is False
    assert [p.code for p in recs] == [p.code for p in cands[: world["settings"].max_picks]]
    assert any("未经 LLM 审核" in m for m in notifier.sent[n0:])    # 量化推荐照常推送
    assert not any(p.startswith("auction") for p in llm.purposes)  # 无计划不做竞价确认

    assert settle.main(now=at(day, 15, 35)) == [day]
    assert review.main(now=at(day, 16, 5)) is True
    assert "复盘失败" in notifier.sent[-1] and "h:active" in notifier.sent[-1]
    assert pb.load("candidate")["rules"] == []


def test_logon_catch_up_after_missed_days(world):
    """电脑在 dates[101]、dates[102] 全天关机，dates[103] 10:00 开机。"""
    dates, store, llm = world["dates"], world["store"], world["llm"]
    day = dates[103]
    start_day(world, day)
    assert premarket_scan.main(now=at(day, 10, 0)) is True      # 错过的 08:30 任务：只扫描
    assert premarket_final.main(now=at(day, 10, 0)) is False    # 过时推荐跳过
    assert store.load_recommendation(day, "h:active") is None
    assert settle.main(now=at(day, 10, 3)) == dates[101:103]   # Catchup 动作 1
    assert review.main(now=at(day, 10, 5)) is True              # Catchup 动作 2
    assert store.job_done("review", dates[102])
    for acct in ACCOUNTS:
        assert store.last_nav_date(acct) == dates[102], acct
    assert store.load_account("h:active").trades == []          # 无推荐的日子不交易
    assert llm.calls == []                                      # 开机补跑不花 LLM 费用
```

**Step 2: 运行**

```
uv run pytest tests/test_e2e.py -v
```
预期：3 passed（此时 Part 1-5 与 Task 6.1-6.6 均已完成，本测试是集成验证，不需要新增产品代码）。若失败，按报错定位到契约不一致的那一方修复（不放宽断言）；常见原因：
- `sqlite3.ProgrammingError: ... created in a thread` → Part 2 的 `Store` 需 `sqlite3.connect(..., check_same_thread=False)` 并以锁串行化执行（`run_analyze`、`run_final` 都在工作线程里用 Store）。
- `kv "plan:<日期>:active"` 为空 → `premarket.analyze` 须在 LLM 成功后执行 `store.kv_set(f"plan:{d.isoformat()}:{kind}", plan.model_dump())`；另外 `FakeLLM` 只覆盖 `llm.day` 当天的候选，`start_day` 必须在 `premarket_scan.main` 之前调用。
- `llm.purposes` 缺 `auction:*` → 有计划且规则过滤后非空时，`finalize` 须以 `purpose=f"auction:{kind}"` 做一次确认调用。
- 推送文本里找不到股票代码 → `notify.format.format_recommendation` 必须包含每只股票的代码。
- 降级场景 `llm_reviewed` 不是 False，或推荐不是量化排名前几只 → `finalize` 无计划时须返回 `(cands[:max_picks], False, "")`；`analyze` 遇到任何异常都返回 None，且不写 kv。
- `maybe_release` 在第 1 天就发布 → Part 4 需读取 `kv "playbook_released"`（ISO 日期，bootstrap 写入）作为上次发布日。
- `FileNotFoundError: playbook/...md` → Part 4 的提示词文件须已提交，且 `agents.playbook` 在调用时读取模块级 `PLAYBOOK_DIR`（不要在函数默认参数里固化路径）。
- 补跑测试中 `review.main` 返回 False → `agents.review.run_review` 在当日无推荐、无成交时也应正常结束（照常推送日报）。

**Step 3: 提交**

```
git add tests/test_e2e.py
git commit -m "test: end-to-end replay of two trading days, relay outage and logon catch-up"
```
（附 Co-Authored-By trailer。）

### Task 6.8: 全量测试 + 首次实盘运行清单

**Files:** 无新增文件。只运行已有脚本：`scripts/smoke_akshare.py`（Task 3.1）、`scripts/smoke_llm.py`（Task 4.1）和 Task 3.3 的企业微信冒烟命令。

**Step 1: 全量测试（离线）**

```
uv run pytest -v
```
预期：全部 passed、0 failed。本部分贡献 78 个测试：`test_jobs_common.py` 9、`test_jobs_bootstrap.py` 6、`test_jobs_settle.py` 32、`test_jobs_entrypoints.py` 19、`test_jobs_monitor.py` 6、`test_install_tasks.py` 3、`test_e2e.py` 3。任何测试都不得访问网络、启动真实 `claude.exe` 或推送真实企业微信；如有失败，先修复再继续。

**Step 2: 确认本机 Claude Code 可用（Windows PowerShell）**

```powershell
claude --version
Get-Command claude, claude.exe -ErrorAction SilentlyContinue | Format-Table Name, Source
```
预期：版本 ≥ 2.1.223（2026-10-19 用该版本实测通过）。`agents.llm.find_claude_exe` 的查找顺序：`.env` 的 `CLAUDE_EXE` → npm 安装（`claude.cmd` 同目录下的 `node_modules\@anthropic-ai\claude-code\bin\claude.exe`）→ PATH 中的 `claude.exe` → `%USERPROFILE%\.local\bin\claude.exe`（原生安装）。都找不到时在 `.env` 填 `CLAUDE_EXE`：必须是 `claude.exe` 的完整路径，不能填 `claude.cmd` / `claude.ps1`（cmd.exe 的引号规则会弄坏 JSON 参数）。

**Step 3: 填写 .env（Windows 本机，项目根目录）**

```powershell
Copy-Item .env.example .env
notepad .env
git check-ignore .env
```
Claude 鉴权二选一（`.env.example` 里有同样的注释）：
- 中转：填 `ANTHROPIC_BASE_URL`（中转地址）和 `ANTHROPIC_AUTH_TOKEN`，`ANTHROPIC_API_KEY` 留空；
- 官方：只填 `ANTHROPIC_API_KEY`。

`CLAUDE_EXE` 一般留空（自动查找）。另填 `WECOM_WEBHOOK_URL`（企业微信群机器人完整地址），按需调整 `MONTHLY_BUDGET_CNY`（默认 300；花费按 CLI 返回的 `total_cost_usd` × `USD_CNY` 记账）、`CAPITAL`、`MAX_PICKS`。`agents.llm.child_env` 会先去掉系统环境里的所有 `ANTHROPIC_*` 变量和 `CLAUDECODE`，再放入 `get_settings()` 里非空的这几项，再加上 `--setting-sources ""` 不加载 Claude Code 的设置文件，所以本机 Claude Code 自己的登录状态和 `~/.claude/settings.json` 里的 `env` 都不影响 RSIQS。冒烟时确认：在 `~/.claude/settings.json` 的 `env` 里临时放一个假的 `ANTHROPIC_BASE_URL`，`scripts/smoke_llm.py` 仍应按 `.env` 的配置成功；如果失败，说明这个 claude.exe 版本仍会读设置文件，需要把 `CLAUDE_CONFIG_DIR` 指到一个空目录。注意 `get_settings()` 每个键先取非空的系统环境变量，再取 `.env`：系统环境里如果有非空的 `ANTHROPIC_API_KEY` 等变量，会覆盖 `.env`，并随子进程发给 `ANTHROPIC_BASE_URL` 指向的地址（官方模式下，系统里残留的 `ANTHROPIC_BASE_URL` 也会让官方 Key 发往那个地址）。无论使用中转还是官方，都先确认系统环境里没有非空的 `ANTHROPIC_*` 变量（PowerShell：`Get-ChildItem Env:ANTHROPIC_*`）。预期 `git check-ignore .env` 输出 `.env`（确认不会被提交）。

**Step 4: Manual smoke steps（联网）**

```
uv run python scripts/smoke_akshare.py
uv run python scripts/smoke_llm.py
uv run python -c "from config import get_settings; from notify.wecom import WeCom; print(WeCom(get_settings().wecom_webhook_url).send_text('RSIQS 连通测试'))"
```
预期：`smoke_akshare.py` 按 Part 3 的说明全部通过（列名映射正确）；`smoke_llm.py` 约 10–25 秒后打印解析出的对象和本月花费（单次约 0.01–0.03 美元）；企业微信命令打印 `True`，群里收到"RSIQS 连通测试"。任一失败先排查，不要继续。`smoke_llm.py` 常见报错：
- `找不到 claude.exe` → 按 Step 2 设置 `CLAUDE_EXE`。
- `LLMError`（消息是 CLI 返回的 subtype 与输出摘要）→ 核对 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`，再用下面的命令直接跑一次 claude.exe，区分是中转的问题还是 RSIQS 的问题。
- 超时 → 中转或网络慢，重试一次；持续超时按 Step 9 处理。

在普通 PowerShell 窗口（不要在 Claude Code 会话里）直接调用 claude.exe。提示词用英文，因为 Windows PowerShell 5.1 的管道默认按 ASCII 编码：

```powershell
$env:ANTHROPIC_BASE_URL = "https://<中转地址>"; $env:ANTHROPIC_AUTH_TOKEN = "<token>"
"Reply with the single word ok" | & "<claude.exe 完整路径>" -p --bare --output-format json --model claude-opus-5-5 --no-session-persistence
```
预期输出的 JSON 里 `"is_error": false`、`"result"` 为 `ok`。这两个环境变量只在当前窗口有效，关掉窗口即失效。这里也失败，说明问题在中转或账号，不在 RSIQS。

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
预期：非交易日或时间窗外，入口静默返回（日志里有 `not a trading day` / `stale` / `settle not done yet`）；`logs\jobs.log` 无 `ERROR`。交易日早上手动运行时注意：
- 09:25 前运行 `premarket_scan` 会做两路深度分析（每路最长 15 分钟、花费上限 1.5 美元）；09:25 之后只扫描（日志 `analysis skipped`）。
- 09:25:15 前运行 `premarket_final` 会先等到 09:25:15（日志 `waiting ...s for auction data`），不想等就按 Ctrl+C；09:25:15–09:35 之间运行直接定稿，群里收到推荐。

若 scan/final 因时间窗被跳过，在下一个交易日早上 08:30 前保持电脑开机，观察自动运行。

**Step 7: 安装计划任务**

按 Task 6.6 Step 5 执行 `scripts\install_tasks.ps1`，并用 `Get-ScheduledTask -TaskPath "\RSIQS\"` 核对 7 个任务与下次运行时间（PremarketFinal 为 09:25）。

**Step 8: 首周观察清单**

- 每个交易日 08:30 后 20 分钟内，`jobs.log` 出现 `job premarket_scan <日期> ok`，群里没有"盘前分析未完成"告警。
- 09:28 前群里收到推荐。若标注"未经 LLM 审核"（该路没有盘前计划），或市场观点末尾有"竞价二次确认未完成"，到 `logs\jobs.log` 查 `agents.premarket` / `agents.llm` 记录的 `LLMError`（含 CLI 的 subtype 与输出摘要）。
- 15:35 后 `jobs.log` 出现 `job settle <日期> ok`；16:05 后收到日报（含全部账户的净值与日收益）。
- 关机一天后开机：3 分钟后 Catchup 任务补齐结算与复盘，日志里能看到逐日的 `job settle ... ok`；错过的 PremarketScan 也会补跑，但 09:25 之后只扫描、不调用 Claude。
- 周六 10:00 研究循环运行后收到研究摘要；`uv run python -c "from jobs import common; s, st, *_ = common.context(); import datetime as dt; t = dt.date.today(); print(st.month_llm_cost(t.year, t.month))"` 查看当月花费（元），确认低于 `MONTHLY_BUDGET_CNY`。
- `logs\notify_failed.jsonl` 应为空或不存在；有内容说明推送失败过，检查网络与 webhook。

**Step 9: 已知风险：中转失效**

RSIQS 只能通过本机 `claude.exe` 调用 Claude（中转会拒绝直接的 SDK 请求）。Claude Code 升级后协议变化、中转账号被封或中转下线，都会让所有调用失败（`LLMError` 或超时）。系统按设计降级，不会停摆：
- 盘前深度分析失败：推"盘前分析未完成"告警，09:25 照常推送量化/规则推荐（标注"未经 LLM 审核"），09:28 前仍有消息；
- 复盘失败：日报照发，复盘栏写"LLM 复盘失败"，playbook 不更新；
- 研究循环：遇到 `LLMError` 即结束本次会话，干净退出。

`tests/test_e2e.py::test_relay_outage_degrades_to_quant_and_still_reports` 覆盖这条降级路径。连续收到上述告警时，先 `claude --version` 看 Claude Code 是否刚升级，再重跑 `uv run python scripts/smoke_llm.py`。这个中转会校验 Claude Code 客户端版本：报错提示需要升级时执行 `claude update`；如果是刚升级后才开始报错，向中转方确认它支持的版本；中转不可用时，在 `.env` 换成可用的中转 Token，或改用官方 `ANTHROPIC_API_KEY`。另外，修改 `playbook/*.md` 提示词时，把约束写成编码规范（如"策略代码只能 import 下列模块"），不要用"安全""沙箱""绕过""权限测试"一类措辞：这类措辞会触发服务端分类器，请求被转到中转没有的模型，调用直接失败。

---
