"""Runtime settings loaded from .env / environment."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

# 每个策略的默认持有天数（与各策略文件当前硬编码值保持一致）。
# key = STRATEGY.name（策略类的 name 属性，不是文件名）
_STRATEGY_HOLD_DAYS_DEFAULTS: dict[str, int] = {
    "baseline_momo": 5,
    "low_iv_momo_2610": 5,
    "momo_gap_rev_2611": 5,
    "momo_ivgate_2611": 8,
    "momo_tight_2610": 8,
    "momo_turn_dry_2611": 5,
}
_HOLD_DAYS_FALLBACK = 5  # 未知策略的兜底值


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
    hold_days_map: dict[str, int] = field(default_factory=dict)

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

    def hold_days_for(self, strategy_name: str) -> int:
        """返回策略 ``strategy_name`` 的持有天数。

        查找顺序：hold_days_map（由 .env HOLD_DAYS_* 覆写）→ 内置默认值
        _STRATEGY_HOLD_DAYS_DEFAULTS → 兜底 _HOLD_DAYS_FALLBACK=5。
        """
        return self.hold_days_map.get(
            strategy_name,
            _STRATEGY_HOLD_DAYS_DEFAULTS.get(strategy_name, _HOLD_DAYS_FALLBACK),
        )


def _num(env, name: str, default: str, cast):
    raw = env(name, default)
    try:
        return cast(raw)
    except ValueError:
        kind = "整数" if cast is int else "数字"
        raise ValueError(f"配置 {name} 必须是{kind}，当前为 {raw!r}") from None


def _read_strategy_hold_days(env) -> dict[str, int]:
    """遍历所有策略名，从 .env / 环境变量读取 HOLD_DAYS_<STRATEGY_UPPER>，
    未设置或非法时跳过（用默认值）。环境变量键名：策略名全大写，下划线保留。"""
    result: dict[str, int] = {}
    for strat, default in _STRATEGY_HOLD_DAYS_DEFAULTS.items():
        key = f"HOLD_DAYS_{strat.upper()}"
        try:
            raw = env(key, "")
            if raw.strip():  # 空字符串视为未设置
                val = int(raw)
                if val >= 1:
                    result[strat] = val
                else:
                    # 非法值（0 或负数）警告并跳过，让调用方拿默认
                    import logging
                    logging.getLogger(__name__).warning(
                        "配置 %s=%d 非法（必须 >= 1），跳过，使用默认值 %d", key, val, default)
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                "配置 %s=%r 不是整数，跳过，使用默认值 %d", key, raw, default)
    return result


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
        hold_days_map=_read_strategy_hold_days(env),
    )
