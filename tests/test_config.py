import os

import pytest

import config

ENV_NAMES = ["ARK_API_KEY", "ARK_MODEL", "LLM_BASE_URL", "WECOM_WEBHOOK_URL", "DB_PATH",
             "CAPITAL", "MAX_PICKS"]
REAL_ENV_FILE = config.ENV_FILE


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch, tmp_path):
    """get_settings() without an argument must never read this machine's real .env."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent.env")


def _isolate(monkeypatch):
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)


ARK_URL = "https://ark.cn-beijing.volces.com/api/v3"


def test_defaults(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", ARK_URL)
    s = config.get_settings()
    assert s.ark_api_key == "" and s.wecom_webhook_url == ""
    assert s.db_path == "data/rsiqs.sqlite"
    assert s.ark_model == "doubao-seed-2-1-lite-260915"
    assert s.llm_base_url == ARK_URL
    assert s.capital == 500_000.0 and s.max_picks == 5
    assert (s.root / "config.py").exists()


def test_env_overrides(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("ARK_API_KEY", "ark-test")
    monkeypatch.setenv("ARK_MODEL", "ep-2026-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("CAPITAL", "1000000")
    monkeypatch.setenv("MAX_PICKS", "3")
    s = config.get_settings()
    assert s.ark_api_key == "ark-test"
    assert s.ark_model == "ep-2026-test"
    assert s.llm_base_url == "https://api.deepseek.com/v1"
    assert s.wecom_webhook_url == "https://example.invalid/hook"
    assert s.db_path == ":memory:"
    assert s.capital == 1_000_000.0 and s.max_picks == 3


def test_empty_env_falls_back_to_defaults(monkeypatch):
    _isolate(monkeypatch)
    for n in ENV_NAMES:
        monkeypatch.setenv(n, "")
    monkeypatch.setenv("CAPITAL", "  ")
    monkeypatch.setenv("LLM_BASE_URL", ARK_URL)  # base_url 必填，空值直接报错（见 test_missing_base_url_raises）
    s = config.get_settings()
    assert s.ark_api_key == ""
    assert s.db_path == "data/rsiqs.sqlite"
    assert s.ark_model == "doubao-seed-2-1-lite-260915"
    assert s.llm_base_url == ARK_URL
    assert s.capital == 500_000.0 and s.max_picks == 5


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_base_url_raises(monkeypatch, raw):
    """LLM_BASE_URL 未设置或空白必须报错：不允许静默回退到任何默认服务商。"""
    _isolate(monkeypatch)
    if raw is not None:
        monkeypatch.setenv("LLM_BASE_URL", raw)
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        config.get_settings()


@pytest.mark.parametrize("name,value", [
    ("MAX_PICKS", "0"), ("MAX_PICKS", "2.5"), ("CAPITAL", "0"), ("CAPITAL", "-1"),
    ("CAPITAL", "nan"), ("CAPITAL", "abc"),
])
def test_invalid_numbers_raise_clear_error(monkeypatch, name, value):
    _isolate(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        config.get_settings()


def test_settings_validated_directly():
    with pytest.raises(ValueError, match="MAX_PICKS"):
        config.Settings(ark_api_key="", wecom_webhook_url="", max_picks=0)
    with pytest.raises(ValueError, match="CAPITAL"):
        config.Settings(ark_api_key="", wecom_webhook_url="", capital=0)


def test_repr_hides_secrets():
    s = config.Settings(ark_api_key="sk-secret-1",
                        wecom_webhook_url="https://example.invalid/hook?key=secret-2",
                        llm_base_url=ARK_URL)
    text = repr(s)
    for secret in ("sk-secret-1", "secret-2"):
        assert secret not in text
    assert "doubao-seed" in text


def test_env_example_lists_every_key_without_secrets():
    lines = (config.ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = dict(ln.split("=", 1) for ln in lines if ln.strip() and not ln.startswith("#"))
    assert set(ENV_NAMES) <= set(values)
    assert values["ARK_API_KEY"] == ""
    assert values["WECOM_WEBHOOK_URL"].endswith("key=YOUR-KEY-HERE")


def _env_file(tmp_path, *lines):
    f = tmp_path / ".env"
    f.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    return f


def test_env_file_value_used_when_system_var_is_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    monkeypatch.setenv("CAPITAL", "")
    s = config.get_settings(_env_file(tmp_path, f"LLM_BASE_URL={ARK_URL}",
                                      "CAPITAL=1000000", "MAX_PICKS=3"))
    assert s.capital == 1_000_000.0 and s.max_picks == 3


def test_non_empty_system_var_wins_over_env_file(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    monkeypatch.setenv("CAPITAL", "200000")
    env_file = _env_file(tmp_path, f"LLM_BASE_URL={ARK_URL}", "CAPITAL=1000000")
    assert config.get_settings(env_file).capital == 200_000.0


def test_both_empty_fall_back_to_default(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    monkeypatch.setenv("CAPITAL", "")
    s = config.get_settings(_env_file(tmp_path, "CAPITAL=", "ARK_MODEL=  ",
                                      f"LLM_BASE_URL={ARK_URL}", "MAX_PICKS"))
    assert s.capital == 500_000.0 and s.ark_model == "doubao-seed-2-1-lite-260915" and s.max_picks == 5


def test_get_settings_does_not_write_environment(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    s = config.get_settings(_env_file(tmp_path, "ARK_MODEL=ep-2026-test",
                                      f"LLM_BASE_URL={ARK_URL}"))
    assert s.ark_model == "ep-2026-test"
    assert "ARK_MODEL" not in os.environ


def test_default_env_file_is_root_dotenv(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", ARK_URL)
    assert REAL_ENV_FILE == config.ROOT / ".env"
    seen = []
    monkeypatch.setattr(config, "dotenv_values", lambda path: seen.append(path) or {})
    config.get_settings()
    assert seen == [config.ENV_FILE]
