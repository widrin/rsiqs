import config

ENV_NAMES = ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_EXE",
             "WECOM_WEBHOOK_URL", "MONTHLY_BUDGET_CNY", "USD_CNY", "DB_PATH", "LLM_MODEL", "CAPITAL",
             "MAX_PICKS"]


def _isolate(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
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
