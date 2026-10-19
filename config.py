"""Runtime settings loaded from .env / environment."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


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
        anthropic_base_url=env("ANTHROPIC_BASE_URL", ""),
        anthropic_auth_token=env("ANTHROPIC_AUTH_TOKEN", ""),
        claude_exe=env("CLAUDE_EXE", ""),
    )
