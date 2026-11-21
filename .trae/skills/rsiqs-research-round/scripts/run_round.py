"""发起一轮 RSIQS 研究会话（绕过 jobs.research 的当日幂等）。

用法：
    uv run python .trae/skills/rsiqs-research-round/scripts/run_round.py <focus 文本文件>

focus 文件内容会作为本轮研究方向注入 prompt。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 脚本位于 <root>/.trae/skills/<skill>/scripts/，需把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agents import research as research_agent  # noqa: E402
from jobs import common  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_round.py <focus_file>")
        return 2
    focus_path = Path(sys.argv[1])
    if not focus_path.exists():
        print(f"focus file not found: {focus_path}")
        return 2
    focus = focus_path.read_text(encoding="utf-8").strip()

    settings, store, client, cache, calendar, notifier = common.context()
    summary = research_agent.run_research(
        settings,
        store,
        notifier,
        llm=common.make_llm(settings, store),
        focus=focus,
    )

    print("research status:", summary.get("status"), "rounds:", summary.get("rounds"))
    print("written:", summary.get("written"))
    for r in summary.get("results", []):
        rep = r.get("report") or {}
        tr, va = rep.get("train", {}), rep.get("val", {})
        print(
            "RESULT",
            r.get("name"),
            "train_sharpe=", tr.get("sharpe"),
            "train_dd=", tr.get("max_dd"),
            "val_sharpe=", va.get("sharpe"),
            "val_excess=", va.get("excess_ann"),
            "val_dd=", va.get("max_dd"),
            "dsr=", rep.get("dsr"),
            "gate=", rep.get("gate_pass"),
            r.get("message"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
