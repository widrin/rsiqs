from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "scripts" / "service.ps1"


def test_service_script_exists_and_ascii():
    # PS 5.1 对无 BOM 的 UTF-8 中文注释按 ANSI 解析会乱码，脚本保持纯 ASCII
    text = PS1.read_text(encoding="utf-8")
    assert text.isascii()
    assert text.startswith("# RSIQS service control")


def test_service_script_covers_all_actions():
    text = PS1.read_text(encoding="utf-8")
    for action in ("install", "start", "stop", "remove", "status"):
        assert f'"{action}"' in text, action
    for fn in ("Invoke-Install", "Invoke-Start", "Invoke-Stop", "Invoke-Remove", "Show-Status"):
        assert f"function {fn}" in text, fn


def test_service_script_delegates_task_registration():
    text = PS1.read_text(encoding="utf-8")
    assert "install_tasks.ps1" in text and "uninstall_tasks.ps1" in text
    assert "Enable-ScheduledTask" in text and "Disable-ScheduledTask" in text
    assert "Stop-ScheduledTask" in text  # 暂停先停正在跑的实例
    assert "Assert-Admin" in text  # 注册/启停/删除 SYSTEM 任务需要管理员


def test_service_script_checks_required_env():
    text = PS1.read_text(encoding="utf-8")
    assert "LLM_BASE_URL" in text and "ARK_API_KEY" in text
    assert "WECOM_WEBHOOK_URL" in text  # 空则告警但不阻塞部署
    assert "jobs.bootstrap" in text and "-Bootstrap" in text


def test_service_script_uses_repo_venv_python():
    text = PS1.read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in text
    assert 'TaskPath -eq \'\\\'' in text  # 根路径过滤，与 install/uninstall 一致
    assert "RSIQS_*" in text
