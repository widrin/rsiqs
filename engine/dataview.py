"""策略唯一的数据入口：只暴露 asof 及以前的行，杜绝未来函数。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engine.types import Bar


class LookaheadError(Exception):
    pass


class DataView:
    # 面板只保存在方法的闭包里，实例上没有任何能取到它的属性：改编后的私有名字、str.format 的字段
    # （"{0._DataView__panel}"）都拿不到 asof 之后的数据。闭包本身只能经 __closure__ 这类双下划线属性
    # 取到，Task 3.6 的源码检查禁止任何含 "__" 的名字、属性和字符串。没有 __dict__，实例只读。
    __slots__ = ("asof", "codes", "name", "history", "bar_on")

    def __init__(self, panel: dict[str, pd.DataFrame], names: dict[str, str], asof: date):
        ts = pd.Timestamp(asof)
        # 只列出 asof 时已经有数据的股票：之后才上市的代码本身就是未来信息
        listed = sorted(c for c, df in panel.items() if len(df) and df.index[0] <= ts)

        def codes() -> list[str]:
            return list(listed)

        def name(code: str) -> str:
            return names.get(code, "")

        def history(code: str, n: int | None = None) -> pd.DataFrame:
            df = panel[code]
            end = int(df.index.searchsorted(ts, side="right"))  # 索引升序
            start = 0 if n is None else max(end - n, 0)
            return df.iloc[start:end].copy()

        def bar_on(code: str, d: date) -> Bar:
            if d > asof:
                raise LookaheadError(f"{code} {d} > asof {asof}")
            row = panel[code].loc[pd.Timestamp(d)]  # 无数据 -> KeyError
            return Bar(code=code, date=d, open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                       close=float(row["close"]), prev_close=float(row["prev_close"]), volume=float(row["volume"]),
                       amount=float(row["amount"]), name=names.get(code, ""))

        for attr, value in (("asof", asof), ("codes", codes), ("name", name), ("history", history),
                            ("bar_on", bar_on)):
            object.__setattr__(self, attr, value)

    def __setattr__(self, key, value):
        raise AttributeError("DataView is read-only")

    def __delattr__(self, key):
        raise AttributeError("DataView is read-only")
