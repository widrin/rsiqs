# tests/test_sandbox.py
from pathlib import Path

import pytest

from strategies.sandbox import IO_ATTRS, check_source

OK = """
import math
import numpy as np
import pandas as pd
from engine.types import Pick
from engine.rules import is_st


class S:
    name = "good_one"

    def __init__(self):
        self.k = 20

    def select(self, view):
        return []


STRATEGY = S()
"""


def bad(code: str) -> list[str]:
    return check_source(code + "\nSTRATEGY = None\n")


def test_allowed_imports_ok():
    assert check_source(OK) == []
    assert bad("import numpy.linalg") == []


def test_baseline_strategy_passes():
    src = (Path(__file__).resolve().parents[1] / "strategies" / "baseline_momo.py").read_text(encoding="utf-8")
    assert check_source(src) == []


def test_import_os_rejected():
    for code in ["import os", "from os import path", "import engine.store", "from strategies.base import Path",
                 "from engine import ledger", "import subprocess as sp", "from . import base"]:
        v = bad(code)
        assert v and "导入" in v[0], code


def test_open_and_builtins_rejected():
    for code in ["open('x.txt')", "exec('1')", "eval('1')", "compile('1','f','exec')",
                 "__import__('os')", "globals()", "getattr(1, 'real')", "vars()"]:
        assert bad(code), code


def test_dunder_access_rejected():
    for code in ["x = ().__class__.__bases__", "y = __builtins__", "z = (1).__dict__",
                 "p = view._DataView__panel", "view._DataView__ts = 0", "a__b = 1\nc = a__b",
                 "k = '_DataView' + '__panel'", "k = '_DataView__panel'"]:
        assert any("__" in m for m in bad(code)), code


def test_dunder_in_docstring_and_method_name_allowed():
    src = OK.replace('    def select(self, view):', '    def select(self, view):\n        """不调用 __init__ 以外的特殊方法。"""')
    assert check_source(src) == []
    assert check_source(OK.replace('    name = "good_one"', '    name = "good_one"\n    k = "a__b"'))  # 普通字符串不行


def test_string_evaluation_and_engine_mutation_rejected():
    for code in ['import typing\ndef f(x: "(lambda p=v._DataView__panel: p)"): pass',
                 "import typing\nh = typing.get_type_hints(f)", "import typing\nr = typing.ForwardRef('x')",
                 "import engine.types\nengine.evaluator.GATE.update(dsr=-1)",
                 "import engine.types\nx = engine", "import strategies.base\ns = strategies.sandbox",
                 "import engine.rules as r\nr.COMMISSION_RATE = 0", "from engine.dataview import DataView\nDataView.history = None",
                 "raise KeyboardInterrupt('x')", "class E(BaseException): pass",
                 "from typing import get_type_hints\nh = get_type_hints", "from typing import ForwardRef\nr = ForwardRef",
                 "import typing\nt = typing._eval_type", "import engine.rules as r\nr.COMMISSION_RATE -= r.COMMISSION_RATE"]:
        assert bad(code), code
    assert bad("import engine.types\np = engine.types.Pick") == []  # 完整的白名单路径可以


@pytest.mark.parametrize("code", [
    "import statistics\nself = statistics.NormalDist\nself.inv_cdf = None",
    "import pandas as pd\nfor self in [pd]:\n    self.concat = None",
    "class S:\n    def f(self):\n        self = 1",
    "class S:\n    def f(self):\n        import pandas as self",       # 只有 import ... as self 这一处绑定
    "class S:\n    def f(self):\n        try:\n            pass\n        except Exception as self:\n            pass",
    "class S:\n    def f(self, x):\n        match x:\n            case self:\n                pass",
    "def patch(self):\n    return self",                                   # self 当普通函数的参数
    "import pandas as self\nself.concat = None",                          # 模块级的 self.xxx
    "class S:\n    self.x = 1",                                             # 不在方法里的 self.xxx（纵深防御）
])
def test_self_cannot_be_rebound_or_faked(code):
    assert bad(code), code


def test_messages_suggest_alternatives():
    assert any("f-string" in m for m in bad("s = '{:.1%}'.format(0.1)"))
    assert any("嵌套函数" in m for m in check_source(OK.replace(
        "    def select(self, view):\n        return []",
        "    def select(self, view):\n        def helper():\n            self.last = 1\n        return []")))


def test_format_and_pandas_extensions_rejected():
    for code in ["v = None\ns = ('{0._DataView' + '_' + '_panel}').format(v)", "s = '{x}'.format_map({})",
                 "import pandas as pd\n@pd.api.extensions.register_series_accessor('astype')\nclass A: pass",
                 "import pandas as pd\npd.set_option('display.max_rows', None)", "import numpy as np\nnp.seterr(all='ignore')"]:
        assert bad(code), code


def test_attribute_stores_only_on_self():
    for code in ["import pandas as pd\nS = pd.Series\nS.mean = None", "import math\nM = math\nM.erf = abs",
                 "import engine.rules as r\n(r,)[0].COMMISSION_RATE = 0", "import pandas as pd\ndel pd.Series.mean",
                 "import pandas as pd\nfor pd.x in []: pass", "o = object\no.y += 1"]:
        assert any("self" in m for m in bad(code)), code
    ok = OK.replace("        self.k = 20", "        self.k = 20\n        self.k += 1\n        del self.k\n        self.k = 3")
    assert check_source(ok) == []
    assert bad("import pandas as pd\ndf = pd.DataFrame()\ndf['x'] = 1\ndf.loc[0, 'x'] = 2") == []  # 下标赋值可以


@pytest.mark.parametrize("attr", sorted(IO_ATTRS))
def test_every_file_attribute_rejected(attr):
    assert bad(f"p = None\np.{attr}()"), attr


def test_open_price_attribute_allowed_but_open_call_rejected():
    assert bad("b = None\nx = b.open\nfor r in []:\n    y = r.open > r.close") == []
    assert any("open" in m for m in bad("p = None\nf = p.open('x')"))


def test_file_access_through_pathlike_and_pandas_rejected():
    for code in ["import pandas as pd\npd.DataFrame().to_string('leak.txt')", "import numpy as np\nnp.fromregex('a', 'b', [])",
                 "p = None\np.write_text('x')", "p = None\np.open()", "p = None\np.read_text()"]:
        assert bad(code), code


def test_dynamic_access_and_process_control_rejected():
    for code in ["import typing\nf = typing.operator.attrgetter('x')", "import dataclasses\nm = dataclasses.sys",
                 "import pandas as pd\npd.eval('1 + 1')", "df = None\ndf.query('a > 1')",
                 "import dataclasses\ni = dataclasses.inspect", "print('x')", "raise SystemExit(0)",
                 "exit(0)", "quit()"]:
        assert bad(code), code


def test_file_io_attributes_rejected():
    for code in ["import pandas as pd\npd.read_csv('a')", "import numpy as np\nnp.load('a')",
                 "df = None\ndf.to_csv('a')"]:
        assert bad(code), code


def test_missing_strategy_rejected():
    v = check_source("import math\nx = 1\n")
    assert v == ["缺少模块级 STRATEGY 赋值"]


def test_syntax_error_rejected():
    v = check_source("def (:\n")
    assert len(v) == 1 and v[0].startswith("语法错误")
