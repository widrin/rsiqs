"""策略唯一的数据入口：只暴露 asof 及以前的行，杜绝未来函数。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engine.types import Bar


class LookaheadError(Exception):
    pass


class DataView:
    # 私有状态用双下划线（名字改编为 _DataView__xxx）且无 __dict__。改编后的名字仍可访问，挡住它的是
    # 策略源码检查（Task 3.6 禁止任何含 "__" 的属性、名字和标识符式字符串），以及 run_backtest
    # 只把截断到本段末尾的面板交给 DataView（训练、验证段看不到之后的数据）。
    __slots__ = ("__panel", "__names", "__asof", "__ts", "__codes")

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str], asof: date):
        self.__panel = panel
        self.__names = names
        self.__asof = asof
        self.__ts = pd.Timestamp(asof)
        # 只列出 asof 时已经有数据的股票：之后才上市的代码本身就是未来信息
        self.__codes = sorted(c for c, df in panel.items() if len(df) and df.index[0] <= self.__ts)

    @property
    def asof(self) -> date:
        return self.__asof

    def codes(self) -> list[str]:
        return list(self.__codes)

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
