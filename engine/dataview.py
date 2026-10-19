"""策略唯一的数据入口：只暴露 asof 及以前的行，杜绝未来函数。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engine.types import Bar


class LookaheadError(Exception):
    pass


class DataView:
    # 私有状态用双下划线（名字改编为 _DataView__xxx）且无 __dict__：
    # 沙箱禁止含 "__" 的属性访问，策略代码因此拿不到完整面板。
    __slots__ = ("__panel", "__names", "__asof", "__ts")

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str], asof: date):
        self.__panel = panel
        self.__names = names
        self.__asof = asof
        self.__ts = pd.Timestamp(asof)

    @property
    def asof(self) -> date:
        return self.__asof

    def codes(self) -> list[str]:
        return sorted(self.__panel)

    def name(self, code: str) -> str:
        return self.__names.get(code, "")

    def history(self, code: str, n: int | None = None) -> pd.DataFrame:
        df = self.__panel[code]
        end = int(df.index.searchsorted(self.__ts, side="right"))  # 索引升序
        start = 0 if n is None else max(end - n, 0)
        return df.iloc[start:end].copy()

    def bar_on(self, code: str, d: date) -> Bar:
        if d > self.__asof:
            raise LookaheadError(f"{code} {d} > asof {self.__asof}")
        row = self.__panel[code].loc[pd.Timestamp(d)]  # 无数据 -> KeyError
        return Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                   close=float(row["close"]), prev_close=float(row["prev_close"]), volume=float(row["volume"]),
                   amount=float(row["amount"]), name=self.name(code))
