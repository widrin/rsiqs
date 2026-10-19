"""一次性初始化：python -m jobs.bootstrap（可重复运行，已存在的账户/playbook 不覆盖）。"""
from __future__ import annotations

from datetime import date, datetime

import agents.playbook as playbook
from engine import integrity
from engine.ledger import Account
from jobs import common

HISTORY_START = date(2015, 6, 1)


def account_names(store) -> list[str]:
    return [f"q:{common.incumbent_strategy(store)}", *common.LLM_ACCOUNTS, common.BENCH_ACCOUNT]


def main(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    settings, store, client, cache, calendar, notifier = common.context()
    root = settings.root
    for sub in ("data", "logs", "playbook", ".integrity"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    codes = sorted(client.universe())
    store.kv_set("universe", codes)
    try:
        cache.set_names(client.names())  # 交易所列表，含 ST 前缀；全市场快照要将近一分钟
    except Exception:  # noqa: BLE001 - 交易所列表取不到时退回全市场快照
        common.log.exception("names() failed, falling back to spot()")
        spot = client.spot()
        cache.set_names({str(c): str(n) for c, n in zip(spot["code"], spot["name"])})
    cache.update(codes, end=now.date(), start_default=HISTORY_START)
    cache.save_index(common.INDEX_SYMBOL, client.index_daily(common.INDEX_SYMBOL))

    if not store.strategies_with_status("incumbent"):
        store.set_status(common.DEFAULT_INCUMBENT, "incumbent", now.date())

    anchor = common.last_closed_day(now, calendar)
    for key in ("bench_start", "playbook_released"):
        if store.kv_get(key) is None:
            store.kv_set(key, anchor.isoformat())
    created = []
    for name in account_names(store):
        if store.load_account(name) is None:
            store.save_account(Account(name=name, cash=settings.capital))
            store.record_nav(name, anchor, settings.capital)
            created.append(name)

    for kind in ("active", "candidate"):
        if not (playbook.PLAYBOOK_DIR / f"{kind}.json").exists():
            playbook.save(kind, {"version": 1, "created": now.date().isoformat(), "rules": []})

    if (root / integrity.MANIFEST_PATH).exists():
        changed = integrity.verify(root)  # 已有清单不重写：重写等于认可 engine/ 的任何改动
    else:
        integrity.write_manifest(root)
        changed = []
    msg = (f"RSIQS 初始化完成：股票池 {len(codes)} 只，起始日 {anchor.isoformat()}，"
           f"新建账户 {', '.join(created) or '无'}")
    if changed:
        msg += (f"\n警告：engine/ 与哈希清单不符：{', '.join(changed)}。"
                "确认改动无误后按 Task 2.8 的命令人工重写清单并提交。")
    notifier.send_text(msg)
    print(msg)
    return {"universe": len(codes), "anchor": anchor.isoformat(), "created": created}


if __name__ == "__main__":
    main()
