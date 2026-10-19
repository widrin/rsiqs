# strategies/sandbox.py
"""策略源码静态检查：限制导入、危险内建、双下划线访问、动态取属性、修改已导入模块和文件读写。"""
import ast

ALLOWED_IMPORTS = {"math", "statistics", "numpy", "pandas", "dataclasses", "typing",
                   "engine.types", "engine.dataview", "engine.rules"}
# strategies.base 不在白名单：它模块级导入了 pathlib.Path；策略按鸭子类型实现 Strategy 接口，不需要 import 它
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "breakpoint", "input",
                # 评估子进程只认 stdout 最后一行和数据库里的试验记录：策略不能打印，也不能提前结束进程
                "print", "exit", "quit", "SystemExit", "KeyboardInterrupt", "BaseException",
                "attrgetter", "methodcaller", "get_type_hints", "ForwardRef"}
# 按字符串求值或取属性，或经白名单模块拿到解释器内部模块（例如 dataclasses.sys、typing.operator）
DYNAMIC_ATTRS = {"eval", "query", "sys", "os", "inspect", "operator", "builtins", "importlib",
                 "subprocess", "attrgetter", "methodcaller", "get_type_hints", "ForwardRef",
                 "_eval_type", "_evaluate", "evaluate_forward_ref",
                 # str.format 的字段名按字符串取属性（"{0.x}".format(obj)）
                 "format", "format_map", "vformat", "Formatter",
                 # pandas 扩展接口会用 setattr 覆盖 Series/DataFrame 上的任意方法；set_option 改全局状态
                 "api", "extensions", "register_series_accessor", "register_dataframe_accessor",
                 "register_index_accessor", "register_extension_dtype", "set_option", "option_context",
                 "seterr", "set_printoptions"}
IO_ATTRS = {"to_csv", "to_pickle", "to_parquet", "to_excel", "to_json", "to_hdf", "to_sql",
            "to_feather", "to_html", "to_clipboard", "to_string", "to_latex", "to_xml", "to_stata",
            "to_markdown", "load", "loadtxt", "save", "savez", "savetxt", "fromfile", "tofile",
            "genfromtxt", "fromregex", "memmap", "DataSource", "open", "write_text", "write_bytes",
            "read_text", "read_bytes", "unlink", "mkdir", "touch", "rmdir", "iterdir", "glob", "rglob"}


def _allowed(mod: str) -> bool:
    return any(mod == a or mod.startswith(a + ".") for a in ALLOWED_IMPORTS)


def _has_strategy(tree: ast.Module) -> bool:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "STRATEGY" for t in targets):
            return True
    return False


def _docstrings(tree: ast.Module) -> set[int]:
    """模块、类、函数的 docstring 节点（不会被求值，允许提到 __init__ 之类的名字）。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


def _method_selves(tree: ast.Module, parent: dict) -> set[int]:
    """类里直接定义的方法的第一个参数（真正的 self）所在的函数节点。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(parent.get(node), ast.ClassDef):
            args = node.args.posonlyargs + node.args.args
            if args and args[0].arg == "self":
                out.add(id(node))
    return out


def _enclosing_function(node, parent: dict):
    while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        node = parent.get(node)
    return node


def check_source(src: str) -> list[str]:
    """返回违规列表；空列表表示通过。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"语法错误: {e.msg} (第 {e.lineno} 行)"]
    out: list[str] = []
    docs = _docstrings(tree)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    selves = _method_selves(tree, parent)
    for node in ast.walk(tree):
        # self 只能是方法的第一个参数：不能重新绑定（self = pd.Series 之后再给它赋值，就绕过了下面的检查）
        bound = ((isinstance(node, ast.Name) and node.id == "self" and isinstance(node.ctx, (ast.Store, ast.Del)))
                 or (isinstance(node, ast.alias) and node.asname == "self")
                 or (isinstance(node, (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                       ast.MatchAs, ast.MatchStar)) and getattr(node, "name", None) == "self")
                 or (isinstance(node, (ast.Global, ast.Nonlocal)) and "self" in node.names))
        if bound:
            out.append(f"第 {getattr(node, 'lineno', '?')} 行: 禁止给 self 重新赋值")
        elif isinstance(node, ast.arg) and node.arg == "self" and id(parent.get(parent.get(node))) not in selves:
            out.append(f"第 {node.lineno} 行: self 只能作为类中方法的第一个参数")
    # import engine.types 会把包名 engine 绑定进来，经它能拿到 engine.evaluator 等任何已加载的模块
    pkg_roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names if a.asname is None and "." in a.name}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        # 属性赋值、删除只允许 self.xxx：经任何别名修改模块、类或 pandas 方法（S = pd.Series; S.mean = ...）
        # 都会改变同一进程里评估器的行为。下标赋值（df["x"] = ...）不受影响
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if not (isinstance(node.value, ast.Name) and node.value.id == "self"
                    and id(_enclosing_function(node, parent)) in selves):
                out.append(f"第 {line} 行: 只能在类的方法体内直接给 self 的属性赋值（不能在嵌套函数、lambda 或"
                           f"方法以外），禁止 {_dotted(node) or node.attr}")
        if isinstance(node, ast.Import):
            out += [f"第 {line} 行: 禁止导入 {a.name}" for a in node.names if not _allowed(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.append(f"第 {line} 行: 禁止相对导入")
            elif not _allowed(node.module or ""):
                out.append(f"第 {line} 行: 禁止导入 {node.module}")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                out.append(f"第 {line} 行: 禁止使用 {node.id}")
            elif "__" in node.id:  # 含改编后的私有名字，例如 _DataView__panel
                out.append(f"第 {line} 行: 禁止使用含双下划线的名字 {node.id}")
            elif node.id in pkg_roots and not isinstance(parent.get(node), ast.Attribute):
                out.append(f"第 {line} 行: 包名 {node.id} 只能以完整的白名单模块路径使用")
        elif isinstance(node, ast.Attribute):
            path = _dotted(node)
            if path and path.split(".")[0] in pkg_roots and not _allowed(path) and not any(
                    m.startswith(path + ".") for m in ALLOWED_IMPORTS):
                out.append(f"第 {line} 行: 只能访问白名单模块，禁止 {path}")
            if "__" in node.attr:
                out.append(f"第 {line} 行: 禁止访问含双下划线的属性 {node.attr}")
            elif node.attr.startswith("read_") or node.attr in IO_ATTRS:
                out.append(f"第 {line} 行: 禁止文件读写 {node.attr}")
            elif node.attr in DYNAMIC_ATTRS:
                hint = {"format": "，拼字符串用 f-string", "format_map": "，拼字符串用 f-string",
                        "api": "，判断类型用 isinstance", "seterr": "，忽略数值警告用 np.errstate",
                        "set_option": "，不要修改全局选项"}.get(node.attr, "")
                out.append(f"第 {line} 行: 禁止动态求值、修改全局状态或访问解释器内部模块 {node.attr}{hint}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "__" in node.value and id(node) not in docs:  # 字符串注解等可以被 typing 求值
                out.append(f"第 {line} 行: 禁止含双下划线的字符串 {node.value[:40]!r}")
    if not _has_strategy(tree):
        out.append("缺少模块级 STRATEGY 赋值")
    return out
