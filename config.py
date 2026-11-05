"""Runtime settings loaded from .env / environment."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    ark_api_key: str = field(repr=False)  # LLM 服务商的 API Key（.env）
    wecom_webhook_url: str = field(repr=False)
    db_path: str = "data/rsiqs.sqlite"
    ark_model: str = "doubao-seed-2-1-lite-260915"  # 模型 ID 或推理接入点 ep-xxx
    llm_base_url: str = ""  # OpenAI 兼容 base_url，必填（__post_init__ 校验非空，不回退默认服务商）
    capital: float = 500_000.0
    max_picks: int = 5
    root: Path = field(default=ROOT)

    def __post_init__(self):
        # 比较式遇到 NaN 为 False，所以 NaN 同样会被拒绝
        checks = [("MAX_PICKS", self.max_picks, self.max_picks >= 1, ">= 1"),
                  ("CAPITAL", self.capital, self.capital > 0, "> 0")]
        for env_name, value, ok, rule in checks:
            if not ok:
                raise ValueError(f"配置 {env_name} 必须 {rule}，当前为 {value!r}")
        if not self.llm_base_url.strip():
            raise ValueError("配置 LLM_BASE_URL 不能为空：填服务商 base_url"
                             "（火山方舟 https://ark.cn-beijing.volces.com/api/v3，其他家见 .env.example）")


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
        ark_api_key=env("ARK_API_KEY", ""),
        wecom_webhook_url=env("WECOM_WEBHOOK_URL", ""),
        db_path=env("DB_PATH", "data/rsiqs.sqlite"),
        ark_model=env("ARK_MODEL", "doubao-seed-2-1-lite-260915"),
        llm_base_url=env("LLM_BASE_URL", ""),
        capital=_num(env, "CAPITAL", "500000", float),
        max_picks=_num(env, "MAX_PICKS", "5", int),
        root=ROOT,
    )
