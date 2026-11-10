import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_every_task_points_to_existing_job_module():
    jobs = set(re.findall(r'New-JobAction "([\w,]+)"', read("install_tasks.ps1")))
    assert jobs == {"premarket_scan", "premarket_final", "monitor", "midday", "settle", "review",
                    "doctor", "research", "settle,review"}
    for job in jobs:
        for name in job.split(","):
            assert (ROOT / "jobs" / f"{name}.py").exists(), name


def test_schedule_times_and_triggers():
    ps1 = read("install_tasks.ps1")
    for t in ("08:30", "09:25", "09:30", "15:35", "16:05", "07:30", "10:00"):
        assert f'"{t}"' in ps1, t
    assert '(New-WeekdayTrigger "09:25") (New-JobAction "premarket_final")' in ps1
    assert "-AtStartup" in ps1 and "Saturday" in ps1 and "StartWhenAvailable:$Available" in ps1


def test_monitor_settle_review_retry_and_start_when_available():
    ps1 = read("install_tasks.ps1")
    assert '(New-RetryTrigger "09:30" 2 330)  (New-JobAction "monitor")' in ps1
    assert '(New-RetryTrigger "15:35" 20 175) (New-JobAction "settle")' in ps1
    assert '(New-RetryTrigger "16:05" 20 155) (New-JobAction "review")' in ps1
    assert "-RepetitionInterval (New-TimeSpan -Minutes $EveryMin)" in ps1
    assert "$r.StopAtDurationEnd = $false" in ps1  # 重复窗口结束时不终止还在跑的实例
    for job in ("premarket_scan", "premarket_final"):  # 从睡眠中唤醒
        assert f'(New-JobAction "{job}") $true $true' in ps1, job
    assert '(New-JobAction "research")' in ps1  # 周六错过就跳过，不在工作日早上补跑


def test_scripts_are_ascii_and_target_root_path_system():
    # PS 5.1 对无 BOM 的 UTF-8 中文注释按 ANSI 解析会乱码，脚本注释保持纯 ASCII
    for name in ("install_tasks.ps1", "uninstall_tasks.ps1"):
        text = read(name)
        assert text.isascii(), name
    install = read("install_tasks.ps1")
    assert '-UserId "SYSTEM" -LogonType ServiceAccount' in install
    assert 'run_job.ps1' in install  # 统一经 run_job.ps1 调用仓库 venv 的 python
    for name in ("install_tasks.ps1", "uninstall_tasks.ps1"):
        assert "TaskName -Like 'RSIQS_*'" in read(name), name  # 根路径 + 前缀过滤
