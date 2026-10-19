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
    assert "-AtLogOn" in ps1 and "Saturday" in ps1 and "StartWhenAvailable:$Available" in ps1


def test_settle_and_review_retry_and_catch_up_only_via_catchup():
    ps1 = read("install_tasks.ps1")
    assert '(New-RetryTrigger "15:35") (New-JobAction "settle")\n' in ps1
    assert '(New-RetryTrigger "16:05") (New-JobAction "review")\n' in ps1
    assert "-RepetitionInterval (New-TimeSpan -Minutes 20)" in ps1
    for job in ("premarket_scan", "premarket_final", "monitor"):  # 只有早上的任务开机后补跑
        assert f'(New-JobAction "{job}") $true' in ps1, job
    assert '(New-JobAction "research")\n' in ps1


def test_scripts_are_ascii_and_use_rsiqs_folder():
    for name in ("install_tasks.ps1", "uninstall_tasks.ps1"):
        text = read(name)
        assert text.isascii(), name
        assert "\\RSIQS\\" in text, name
