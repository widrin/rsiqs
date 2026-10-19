"""研究循环：Claude 每轮返回分析和策略草稿（结构化输出，没有任何工具）。

Claude 只能返回代码文本。命名规则、import 白名单（strategies.sandbox.check_source）、编译、
写文件、子进程评估、评估次数上限、留出集只回传通过/不通过、会话前后的 engine/ 哈希校验和
月度预算，全部由本模块的 Python 代码执行。
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import tokenize
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from agents.llm import LLM, BudgetExceeded
from agents.playbook import git_commit
from config import Settings
from engine.evaluator import GATE, HOLDOUT_GATE, SPLITS
from engine.integrity import verify
from engine.store import Store
from notify.format import format_alert
from strategies.base import NAME_RE, RESERVED
from strategies.sandbox import ALLOWED_IMPORTS, check_source

log = logging.getLogger(__name__)

MAX_ROUNDS = 3
MAX_DRAFTS_PER_ROUND = 3
MAX_EVALS_PER_SESSION = 5
EVAL_TIMEOUT = 1800  # 基线策略全量评估实测约 7 分钟
CONTEXT_MAX_CHARS = 60_000       # build_context 总长度上限（字符）
FILE_MAX_CHARS = 15_000          # 背景里单个源码文件的上限
TRIALS_IN_CONTEXT = 20
BASELINE = "baseline_momo"
INTERFACE_FILES = (
    ("strategies/base.py", "策略接口"),
    ("engine/types.py", "Pick 等数据类型"),
    ("engine/dataview.py", "DataView：策略唯一的数据入口"),
    ("engine/rules.py", "A 股交易规则工具，可以 import"),
)
STATUS_CN = {"incumbent": "现任", "shadow": "影子盘", "candidate": "候选", "retired": "已退役"}


class Draft(BaseModel):
    name: str
    hypothesis: str
    code: str


class ResearchRound(BaseModel):
    analysis: str
    drafts: list[Draft]
    done: bool


def _strip_comments(src: str) -> str:
    """去掉 # 注释（保留 docstring 与字符串里的 #）；无法分词时原样返回。"""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return src
    lines = src.split("\n")
    drop: set[int] = set()
    for t in toks:
        if t.type == tokenize.COMMENT:
            row, col = t.start[0] - 1, t.start[1]
            head = lines[row][:col].rstrip()
            lines[row] = head
            if not head:
                drop.add(row)
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


def _source_block(root: Path, rel: str, title: str) -> str:
    p = root / rel
    text = _strip_comments(p.read_text(encoding="utf-8")) if p.is_file() else "（文件不存在）"
    if len(text) > FILE_MAX_CHARS:
        text = text[:FILE_MAX_CHARS] + "\n# ...（已截断）"
    return f"### {rel}（{title}）\n```python\n{text.rstrip()}\n```"


def _hypothesis(store: Store, name: str) -> str:
    return str(store.kv_get(f"research:hypothesis:{name}", "") or "")


def _trial_line(store: Store, t: dict) -> str:
    def f(x):
        return "?" if x is None else f"{x:.2f}"

    ho = {True: "通过", False: "未通过", None: "未运行"}[t["holdout_pass"]]
    hyp = _hypothesis(store, t["strategy"])[:120]
    return (f"- {t['ts'][:10]} {t['strategy']}：train 夏普 {f(t['train_sharpe'])}，val 夏普 "
            f"{f(t['val_sharpe'])}，门槛{'达标' if t['gate_pass'] else '未达标'}，holdout {ho}"
            + (f"；假设：{hyp}" if hyp else ""))


def _status_lines(store: Store) -> list[str]:
    out = []
    for st, cn in STATUS_CN.items():
        names = store.strategies_with_status(st)
        shown = "、".join(names[:50]) + (f" 等 {len(names)} 个" if len(names) > 50 else "")
        out.append(f"- {cn}（{st}）：{shown or '无'}")
    return out


def build_context(settings: Settings, store: Store) -> str:
    """每轮共用的背景：会话限制、门槛、可用模块、策略状态、最近试验、接口与策略源码。

    短而关键的部分在前，源码在后；超过 CONTEXT_MAX_CHARS 时从尾部截断。"""
    root = Path(settings.root)
    splits = {k: [a.isoformat(), b.isoformat() if b else "最新"] for k, (a, b) in SPLITS.items()}
    trials = store.recent_trials(TRIALS_IN_CONTEXT)
    head = [
        f"# 本次研究会话（{date.today().isoformat()}）",
        "",
        "## 会话限制",
        f"- 最多 {MAX_ROUNDS} 轮；每轮最多处理 {MAX_DRAFTS_PER_ROUND} 个草稿；整个会话最多评估 "
        f"{MAX_EVALS_PER_SESSION} 次；单次评估限时 {EVAL_TIMEOUT} 秒。",
        "",
        "## 数据切分与门槛",
        f"- 切分 SPLITS：{json.dumps(splits, ensure_ascii=False)}",
        f"- 回测门槛 GATE（全部为 >= 比较；val_max_dd 为负数）：{json.dumps(GATE)}",
        f"- holdout 门槛 HOLDOUT_GATE：{json.dumps(HOLDOUT_GATE)}。holdout 只返回 holdout_pass（通过/不通过）。",
        f"- 历史试验总数 n_trials：{store.trial_count()}（下一次评估计为第 {store.trial_count() + 1} 次）",
        "",
        "## 策略代码可以 import 的模块（含其子模块）",
        "- " + ", ".join(sorted(ALLOWED_IMPORTS)),
        "",
        "## 现有策略（新草稿不能再用这些名字）",
        *_status_lines(store),
        "",
        f"## 最近 {TRIALS_IN_CONTEXT} 次试验（新到旧）",
        *([_trial_line(store, t) for t in trials] or ["- 暂无"]),
        "",
        "## 接口与示例源码（已去掉 # 注释）",
    ]
    blocks = [_source_block(root, rel, title) for rel, title in INTERFACE_FILES]
    incumbents = [n for n in store.strategies_with_status("incumbent") if NAME_RE.fullmatch(n)]
    for name in incumbents:
        if name != BASELINE:
            blocks.append(_source_block(root, f"strategies/{name}.py", "当前现任策略"))
    baseline_title = "基线策略，当前现任" if BASELINE in incumbents else "基线策略，写法示例"
    blocks.append(_source_block(root, f"strategies/{BASELINE}.py", baseline_title))
    text = "\n".join(head) + "\n\n" + "\n\n".join(blocks)
    if len(text) > CONTEXT_MAX_CHARS:
        text = text[:CONTEXT_MAX_CHARS - 20] + "\n...（背景已截断）"
    return text


def submit_draft(settings: Settings, store: Store, draft: Draft) -> tuple[bool, str]:
    """校验并保存一个草稿。成功返回 (True, 说明)；失败返回 (False, 原因)，原因会原样反馈给模型。

    规则：名字匹配 NAME_RE 且不是保留名；不是现任；是新名字（任何已有状态或已有文件都不覆盖，
    保证每个试验记录对应唯一一份代码）；compile() 通过；check_source() 无违规。"""
    name, code = draft.name.strip(), draft.code
    if not NAME_RE.fullmatch(name) or name in RESERVED:
        return False, (f"策略名 {name!r} 不合规：须匹配 ^[a-z][a-z0-9_]{{2,40}}$，"
                       "且不能是 strategies/ 下的保留模块名")
    if name in store.strategies_with_status("incumbent"):
        return False, f"{name} 是当前现任策略，不能覆盖；请换一个新名字"
    path = Path(settings.root) / "strategies" / f"{name}.py"
    status = store.get_status(name)
    if status is not None or path.exists():
        return False, f"策略名 {name} 已被使用（{STATUS_CN.get(status, '已有同名文件')}）；每个草稿都要用新名字"
    try:
        compile(code, f"strategies/{name}.py", "exec", dont_inherit=True)
    except SyntaxError as e:
        return False, f"语法错误（第 {e.lineno} 行）：{e.msg}"
    except (ValueError, RecursionError, MemoryError) as e:
        return False, f"源码无法编译：{type(e).__name__}: {e}"
    problems = check_source(code)
    if problems:
        return False, "代码规范检查未通过：" + "；".join(problems)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8", newline="\n")
    store.set_status(name, "candidate", date.today())
    store.kv_set(f"research:hypothesis:{name}", draft.hypothesis.strip())
    return True, f"已写入 strategies/{name}.py（状态 candidate）"


def _last_json_line(stdout: str) -> dict | None:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def evaluate_strategy(settings: Settings, store: Store, name: str, runner=None) -> dict:
    """在子进程中运行 engine.run_eval；本次写入数据库的试验记录 gate_pass 且 holdout_pass 时设为 shadow。

    策略代码和评估器在同一个子进程里，stdout 可能被伪造，所以报告只用于展示，是否通过以子进程写入的
    试验记录为准。返回 {"ok": bool, "report": dict | None, "error": str}；任何失败都不抛异常。"""
    run = runner or subprocess.run
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # Windows 管道默认是 ANSI 代码页
    before = store.trial_count(name)
    try:
        proc = run([sys.executable, "-m", "engine.run_eval", name], cwd=str(settings.root),
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=env, timeout=EVAL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "report": None, "error": f"评估超时（{EVAL_TIMEOUT} 秒），请简化计算"}
    except OSError as e:
        return {"ok": False, "report": None, "error": f"无法启动评估进程：{e}"}
    report = _last_json_line(proc.stdout)
    if proc.returncode != 0 or report is None or "error" in report or "gate_pass" not in report:
        if report and report.get("error"):
            detail = str(report["error"])
        else:
            detail = (proc.stderr or proc.stdout or "无输出").strip()[-1500:]
        return {"ok": False, "report": None, "error": f"评估失败（退出码 {proc.returncode}）：{detail}"}
    trial = store.last_trial(name) if store.trial_count(name) > before else None
    if trial is None:
        return {"ok": False, "report": None, "error": "评估没有写入试验记录，报告不予采信"}
    report = {**report, "gate_pass": trial["gate_pass"], "holdout_pass": trial["holdout_pass"]}
    if trial["gate_pass"] and trial["holdout_pass"]:
        store.set_status(name, "shadow", date.today())
    return {"ok": True, "report": report, "error": ""}


FEEDBACK_CODE_CHARS = 6_000      # 反馈里每个草稿代码的上限
FEEDBACK_ANALYSIS_CHARS = 2_000  # 反馈里每轮分析的上限


RESEARCH_SYSTEM = """你是 RSIQS 的量化研究员。RSIQS 是一个 A 股盘前推荐与模拟交易系统：每个交易日开盘前，现任量化策略给出候选股票，LLM 层复核后推送不超过 5 只，模拟盘按开盘价成交。你的工作是提出并检验新的选股策略，让系统长期变得更好。

# 目标
在下列约束下，提高扣除交易成本后、相对沪深 300 的风险调整超额收益：
- 每天最多新开仓 ≤5 只：回测与模拟盘只取 select 返回列表的前 5 个，每只约占初始资金的 1/5。
- 开盘价成交：t 日的 select 只能看到截至上一交易日收盘的数据（view.asof），选中的股票在 t 日按开盘价加 10bp 滑点买入；开盘价高于 entry_max、开盘价不高于 stop、开盘即涨停、停牌或买不足 100 股时不成交。
- T+1：买入当天不能卖出。每个交易日分三步撮合：(1) 开盘卖出：到达到期日（买入后第 hold_days 个交易日）按开盘价卖出；否则开盘价 ≤ stop 按开盘价止损；否则开盘价 ≥ target 按开盘价止盈。(2) 开盘买入：只有第 1 步卖出回笼的资金可用于当天买入。(3) 盘中卖出：其余持仓盘中最低价触及 stop 按 stop 卖出（与 target 同日触及时按止损处理），否则最高价触及 target 按 target 卖出，这部分资金当天不能再买入。全天封死跌停时卖不出；停牌或价格数据异常的股票当天不交易。卖出同样扣 10bp 滑点，成交价不超出当日涨跌停价。
- 涨跌停和成本按成交日当时的规则：创业板 2020-08-24 前涨跌幅 10%；佣金万 2.5（最低 5 元）；过户费 2022-04-29 前十万分之二、之后十万分之一；卖出印花税 2023-08-28 前千分之一、之后万 5。换手越高，成本吃掉的收益越多。
- 股票池约 800 只（沪深 300 与中证 500 的当前成分股，前复权日线，2015 年 6 月起）。存在幸存者偏差，回测结果偏乐观。

# 数据切分
- train：2016 年至 2024 年，用来形成和检验想法。
- val：2025 年 1 月至 2026 年 6 月，样本外检验，门槛主要看这一段。
- holdout：2026 年 7 月至今。只有 train/val 门槛全部达标才会运行，而且只返回 holdout_pass（通过/不通过），不给任何数值。不要根据 holdout 结果反复改版去碰运气，那只会消耗试验次数。
精确日期和各项门槛数值见用户消息。

# 过拟合与多重检验
- 每次评估都记为一次试验；n_trials 是所有策略、所有会话累计的总数。门槛里的 DSR（Deflated Sharpe Ratio）按 n_trials 折减：试验越多，同样的夏普越可能只是运气，DSR 越低，越难达标。
- 所以要少而精：1 到 2 个论证充分、彼此差异明显的变体，胜过一堆参数微调。不做参数网格搜索，不为贴合 train/val 去微调阈值；参数取常见的整数（如 5、10、20、60 日），规则越简单越好。
- 只在 val 好看、train 很差，或者只靠少数几笔交易撑起来的结果，大概率是噪声，属于过拟合的信号。

# 好的假设
- 优先有经济学或市场微观结构依据的想法，例如：中期动量与短期反转、流动性与成交额变化、波动率与特质波动、涨跌停附近的价格行为、散户主导下的过度反应与修正、T+1 带来的隔夜风险、开盘成交的价格冲击等。
- 讲清楚：这个效应为什么应该存在，为什么还没被套利掉，在什么市场环境下会失效，与现任策略有什么不同。
- 先读用户消息里的现任策略和最近的试验记录，不重复已经失败的方向；前几轮有结果时，据此调整。
- 没有值得检验的想法时，宁可返回 done=true 和空的 drafts，也不要凑数。

# 策略代码规范
每个草稿的 code 是一个完整的 Python 模块，会原样保存为 strategies/<name>.py：
1. 模块 docstring 以“假设：”开头，写明假设、经济学理由、与现任策略的差异、预期何时失效；Draft.hypothesis 用一两句话概括同一个假设。
2. 定义一个类，实现 strategies.base.Strategy 接口（按接口写好 name 和 select 即可，不需要 import strategies.base）：类属性 name 等于草稿的 name；方法 select(self, view: DataView) -> list[Pick] 返回按优先级从高到低排序的 Pick 列表（可以多于 5 个：回测取前 5 个，盘前流程最多取前 15 个复核）。
3. 模块末尾定义模块级实例 STRATEGY = 你的类()。
4. 只 import 下列模块（及其子模块）：<<ALLOWED_IMPORTS>>。不要写 from __future__ import annotations。
5. 只通过 view 取数：view.codes()、view.name(code)、view.history(code, n)、view.bar_on(code, d)。history 只含 asof 及以前的行，列为 open、high、low、close、volume（手）、amount（元）、prev_close。用 engine.rules 判断 ST 和涨跌停：计算涨跌停价时把 view.asof 作为 d 传入，例如 limit_prices(code, name, prev_close, view.asof)，因为涨跌幅按当天的规则生效；判断是否涨停或跌停用 at_limit_up / at_limit_down，它们容忍一分钱的复权舍入误差。
6. 策略是纯计算：不读写文件、不联网、不打印；结果可复现，不用随机数，不依赖当前时间；select 不依赖上一次调用留下的状态。
7. 写法保持直接：不用 getattr、setattr、vars、globals、operator.attrgetter 这类反射写法，不用 eval、exec、open，也不用 pandas 的 eval、query；不写 print、exit，也不 raise SystemExit；self 只作为类中方法的第一个参数，只在方法体内直接给 self 的属性赋值（不在嵌套函数或 lambda 里），不给模块、类或其他对象的属性赋值；拼字符串用 f-string，不用 str.format；不访问 pd.api、pd.set_option、np.seterr（需要时用 np.errstate）；名字、属性和字符串里都不出现双下划线（例如 super().__init__()、__name__、对象的私有属性；定义 __init__ 方法本身没问题）；pandas 和 numpy 只做计算，不调用 read_*、to_csv、load、save 等读写函数。
8. 注意速度：一次评估要回放约 2,600 个交易日、每天约 800 只股票，并且有时间上限；基线策略每天对每只股票取 60 行做几次 pandas 运算，全程约 7 分钟。用 view.history(code, n) 只取需要的最近 n 行，避免每天对整段历史重复计算。
9. Pick 字段：code、name、score、entry_max（可接受的最高买入价，通常比最近收盘价高 2% 到 3%）、stop、target（须满足 stop < entry_max < target）、hold_days（正整数），以及中文 reason（选股依据，会推送给用户）。
10. name 须匹配 ^[a-z][a-z0-9_]{2,40}$，并且是一个新名字：不能与用户消息里列出的任何已有策略重名，也不能与 strategies/ 下已有的模块重名。

# 工作方式
- 本任务不使用工具：需要的接口说明、源码、门槛和试验记录都在用户消息里。你只需按 JSON Schema 返回 analysis、drafts、done。
- 系统依次处理你的草稿：检查名字和代码规范，保存为 strategies/<name>.py（状态 candidate），然后运行回测评估。没通过检查的草稿不会评估，也不占评估次数，原因会在下一轮告诉你。
- gate_pass 且 holdout_pass 的策略自动进入影子盘（shadow），之后由至少 20 个交易日的前向模拟盘表现决定能否成为现任。
- 一次会话有轮数、每轮草稿数和评估次数的上限（具体数字见用户消息）。草稿数不要超过剩余评估次数，超出的部分不会处理。
- 下一轮你会看到每个草稿的处理结果和评估报告：train、val 各含 total_return、ann_return、sharpe（年化）、max_dd（负数）、excess_ann（相对沪深 300 的年化超额）、n_days；另有 dsr、gate_pass、holdout_pass（门槛未达标时为 null，表示没有运行）和 n_trials。
- 每个草稿都要用新名字，包括修正上一轮出错的草稿。

# 输出
- analysis：中文。先回顾现任策略、试验记录和上一轮结果，再说明本轮每个假设的逻辑和预期；最后一轮或准备结束时，总结结论和下次研究的方向。
- drafts：0 个或多个 Draft（name、hypothesis、code）。
- done：之后不需要再来一轮时为 true（本轮的草稿照常处理）；想根据本轮评估结果继续迭代时为 false。
""".replace("<<ALLOWED_IMPORTS>>", ", ".join(sorted(ALLOWED_IMPORTS)))


def _month_cost(store: Store) -> float:
    t = date.today()
    return store.month_llm_cost(t.year, t.month)


def _rounded(obj, nd: int = 4):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _rounded(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rounded(v, nd) for v in obj]
    return obj


def _outcome(res: dict) -> str:
    """一个草稿的处理结果，一句中文（用于推送与提交信息）。"""
    if not res["saved"]:
        return res["message"]
    if not res["ok"]:
        return res["error"]
    rep = res["report"]
    if rep.get("gate_pass") and rep.get("holdout_pass"):
        return "门槛达标，holdout 通过，已进入影子盘"
    if rep.get("gate_pass"):
        return "门槛达标，holdout 未通过，保持 candidate"
    return "门槛未达标，保持 candidate"


def _draft_block(res: dict, draft: Draft) -> str:
    lines = [f"### 草稿 {res['name']}", f"假设：{res['hypothesis']}", f"处理：{res['message']}"]
    if res["saved"] and res["ok"]:
        lines.append("评估报告：" + json.dumps(_rounded(res["report"]), ensure_ascii=False))
    elif res["saved"]:
        lines.append(res["error"])
    code = draft.code
    if len(code) > FEEDBACK_CODE_CHARS:
        code = code[:FEEDBACK_CODE_CHARS] + "\n# ...（已截断）"
    lines.append(f"代码：\n```python\n{code.rstrip()}\n```")
    return "\n".join(lines)


def _feedback(history: list[dict]) -> str:
    parts = ["# 本会话前几轮的结果"]
    for h in history:
        parts.append(f"## 第 {h['round']} 轮\n你的分析（节选）：{h['analysis'][:FEEDBACK_ANALYSIS_CHARS]}")
        parts += [_draft_block(res, d) for res, d in zip(h["results"], h["drafts"])]
        if not h["results"]:
            parts.append("（本轮没有草稿）")
        if h["ignored"]:
            parts.append(f"另有 {h['ignored']} 个草稿超出每轮 {MAX_DRAFTS_PER_ROUND} 个的上限，没有处理。")
    return "\n\n".join(parts)


def _round_task(rnd_no: int, evals_left: int) -> str:
    lines = [f"# 本轮任务（第 {rnd_no}/{MAX_ROUNDS} 轮）",
             f"本会话还可评估 {evals_left} 次，本轮最多处理 {MAX_DRAFTS_PER_ROUND} 个草稿；草稿数不要超过剩余评估次数。"]
    if rnd_no == 1:
        lines.append("先在 analysis 里回顾现任策略和最近的试验记录，再提出有依据的新假设。"
                     "没有值得检验的想法时，返回 done=true 和空的 drafts。")
    else:
        lines.append("请根据上面的结果决定：修正出错的草稿（换新名字）、提出有依据的新变体，或者返回 done=true 结束。"
                     "不要为了让 holdout 通过而微调参数。")
    if rnd_no == MAX_ROUNDS:
        lines.append("这是最后一轮：analysis 里请总结本会话的结论和下次研究的方向。")
    return "\n".join(lines)


def _commit_message(today: date, saved: list[dict]) -> str:
    lines = [f"research: {today.isoformat()} 新增候选策略 {', '.join(r['name'] for r in saved)}", ""]
    for r in saved:
        hyp = " ".join(r["hypothesis"].split())[:300]
        lines.append(f"- {r['name']}：{hyp}（{_outcome(r)}）")
    return "\n".join(lines)


def _summary(today: date, out: dict, error: str) -> str:
    label = {"ok": "完成", "budget_exceeded": "本月 LLM 预算用完，提前结束",
             "llm_error": "Claude 调用失败，提前结束", "error": "本地处理出错，提前结束"}[out["status"]]
    saved = [r for r in out["results"] if r["saved"]]
    other = [r for r in out["results"] if not r["saved"]]
    lines = [f"轮数 {out['rounds']}｜评估 {len(saved)} 次｜花费 {out['cost_cny']:.2f} 元"]
    if error:
        lines.append(f"原因：{error[:300]}")
    if saved:
        lines += ["新策略："] + [f"- {r['name']}：{_outcome(r)[:200]}" for r in saved]
    if other:
        lines += ["未采用的草稿："] + [f"- {r['name']}：{r['message'][:200]}" for r in other]
    if not out["results"]:
        lines.append("本次没有提交策略草稿。")
    if out["analysis"]:
        lines.append("结论：" + out["analysis"][:800])
    title = f"研究会话 {today.isoformat()}：{label}"
    if out["status"] in ("llm_error", "error"):
        return format_alert(title, "\n".join(lines))
    return f"RSIQS {title}\n" + "\n".join(lines)


def _process_round(settings: Settings, store: Store, rnd_no: int, rnd: ResearchRound,
                   evals: int, out: dict) -> tuple[list[dict], int]:
    """依次提交本轮至多 MAX_DRAFTS_PER_ROUND 个草稿：只在还有评估额度时提交，提交成功就立即评估。

    结果边做边记进 out：即使本地出错抛异常，已写入的文件也会照常提交。"""
    results = []
    for draft in rnd.drafts[:MAX_DRAFTS_PER_ROUND]:
        res = {"round": rnd_no, "name": draft.name.strip(), "hypothesis": draft.hypothesis.strip(),
               "saved": False, "message": "", "ok": False, "report": None, "error": ""}
        results.append(res)
        out["results"].append(res)
        if evals >= MAX_EVALS_PER_SESSION:
            res["message"] = "本会话评估次数已用完，没有处理"
            continue
        res["saved"], res["message"] = submit_draft(settings, store, draft)
        if res["saved"]:
            out["written"].append(res["name"])
            evals += 1  # 先计数：超时、失败同样消耗一次额度
            res.update(evaluate_strategy(settings, store, res["name"]))
    return results, evals


def run_research(settings: Settings, store: Store, notifier, llm=None) -> dict:
    """一次研究会话（最多 MAX_ROUNDS 轮）。返回 status/rounds/written/results/cost_cny/analysis。"""
    root = Path(settings.root)
    today = date.today()
    out = {"status": "ok", "rounds": 0, "written": [], "results": [], "cost_cny": 0.0, "analysis": ""}

    bad = verify(root)
    if bad:
        notifier.send_text(format_alert("研究会话未启动：engine/ 校验不符", "\n".join(bad)))
        return {**out, "status": "integrity_failed"}

    llm = llm or LLM(settings, store)
    try:
        llm.check_budget(False)
    except BudgetExceeded as e:
        notifier.send_text(f"RSIQS 研究会话 {today.isoformat()} 跳过：{e}")
        return {**out, "status": "budget_exceeded"}

    cost0 = _month_cost(store)
    error = ""
    history: list[dict] = []
    evals = 0
    try:
        context = build_context(settings, store)
        for rnd_no in range(1, MAX_ROUNDS + 1):
            feedback = _feedback(history) + "\n\n" if history else ""
            user = f"{context}\n\n{feedback}{_round_task(rnd_no, MAX_EVALS_PER_SESSION - evals)}"
            try:
                llm.check_budget(False)
                remaining_usd = (settings.monthly_budget_cny - _month_cost(store)) / settings.usd_cny
                rnd = llm.parse(purpose="research", system=RESEARCH_SYSTEM, user=user, schema=ResearchRound,
                                effort="high", timeout=1800.0, max_budget_usd=min(3.0, remaining_usd),
                                essential=False)
            except BudgetExceeded as e:
                out["status"], error = "budget_exceeded", str(e)
                break
            except Exception as e:  # noqa: BLE001 — LLMError、超时、输出不合 schema 都在这里收口
                log.warning("research round %d failed: %s: %s", rnd_no, type(e).__name__, e)
                out["status"], error = "llm_error", f"{type(e).__name__}: {e}"
                break
            out["rounds"], out["analysis"] = rnd_no, rnd.analysis
            results, evals = _process_round(settings, store, rnd_no, rnd, evals, out)
            history.append({"round": rnd_no, "analysis": rnd.analysis, "results": results,
                            "drafts": rnd.drafts[:MAX_DRAFTS_PER_ROUND],
                            "ignored": max(0, len(rnd.drafts) - MAX_DRAFTS_PER_ROUND)})
            if rnd.done or not rnd.drafts or evals >= MAX_EVALS_PER_SESSION:
                break
    except Exception as e:  # noqa: BLE001 — 写文件、数据库等本地故障：停止，但照常做会后校验与提交
        log.exception("research session failed")
        out["status"], error = "error", f"{type(e).__name__}: {e}"
    out["cost_cny"] = _month_cost(store) - cost0

    bad = verify(root)
    if bad:
        notifier.send_text(format_alert(
            "研究会话后 engine/ 校验不符，未提交 git",
            "\n".join(bad) + "\n本次写入的策略：" + ("、".join(out["written"]) or "无") + "（请人工检查）"))
        return {**out, "status": "integrity_failed"}

    saved = [r for r in out["results"] if r["saved"]]
    if saved:
        git_commit([f"strategies/{r['name']}.py" for r in saved], _commit_message(today, saved))
    notifier.send_text(_summary(today, out, error))
    return out
