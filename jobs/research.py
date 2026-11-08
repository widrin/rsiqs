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
        summary = research_agent.run_research(
            settings, store, notifier, llm=common.make_llm(settings, store))
        out["summary"] = summary
        if summary["status"] != "ok":
            # 会话没跑完（完整性拦截/LLM 失败/本地出错）时 agents.research 已推过告警，但 run_job 会记 ok，
            # doctor 只能靠"近期无 LLM 记录"事后发现空转。这里记 error，failed_jobs 当天就能看到。
            raise common.JobFailed(f"研究会话状态 {summary['status']}（原因已单独告警）")

    common.run_job("research", now.date(), fn, store, notifier)
    return out.get("summary")


if __name__ == "__main__":
    main()
