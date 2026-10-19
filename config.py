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
