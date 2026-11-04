"""LLM 调用封装：直连 OpenAI 兼容 API（/chat/completions），结构化输出、token 记账。

默认服务商是火山方舟（个人版 Trae 订阅无法经 TRAE CLI 2.0 用内置模型，它仅对企业版旗舰
套餐开放，故不走任何 CLI 中间层）；换 DeepSeek/Kimi/GLM 等兼容服务商只需改 .env 的
LLM_BASE_URL/ARK_MODEL/ARK_API_KEY 三项。LLM_BASE_URL 必填、无默认回退：不填启动即报错。
每次调用：system + user 两条消息，response_format=json_schema（strict）约束输出结构，
thinking 关闭（方舟默认 enabled 深度思考，定时任务要低延迟、用量可预测）。
模型不使用任何工具，所需上下文全部由 Python 放进提示词。
方舟价格随模型/渠道变动，llm_usage 只记 token，成本记 0。
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable

import requests
from pydantic import BaseModel, ValidationError

from config import Settings
from engine.store import Store

MAX_TOKENS = 16384  # 方舟默认 4096，研究报告的长度不够
THINKING = {"type": "disabled"}  # 方舟默认 enabled（深度思考）；关闭换取低延迟与可预测用量
NO_KEY = "未配置 ARK_API_KEY：.env 里填 LLM 服务商的 API Key（见 .env.example）"


class LLMError(Exception):
    """未配置 Key、超时、网络/HTTP 错误、回复里没有 JSON、结构校验失败。"""


def _strictify(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for v in node.values():
            _strictify(v)
    elif isinstance(node, list):
        for v in node:
            _strictify(v)


def strict_schema(model: type[BaseModel]) -> dict:
    """model_json_schema()，并对每个 object（含 $defs）设 additionalProperties=False、required=全部字段。"""
    schema = copy.deepcopy(model.model_json_schema())
    _strictify(schema)
    return schema


def _int(value: Any) -> int:
    return int(value or 0)


def _tail(raw: Any, limit: int = 300) -> str:
    return str(raw or "").strip()[-limit:]


def _extract_json(text: str) -> Any:
    """从模型回复里取出 JSON：剥掉 markdown 围栏，截取首 { 到末 }，解析失败返回 None。

    json_schema 模式下回复本应是纯 JSON，这里只作为兜底。
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except ValueError:
        return None


class LLM:
    def __init__(self, settings: Settings, store: Store, post: Callable[..., Any] | None = None):
        # post 是 requests.post 的注入点（测试用假响应替代真实 HTTP）
        self.settings = settings
        self.store = store
        self.post = post or requests.post

    def parse(self, purpose: str, system: str, user: str, schema: type[BaseModel],
              timeout: float = 120.0) -> BaseModel:
        """一次方舟调用，返回 schema 实例。

        未配置 Key、超时、网络/HTTP 错误、回复不是 JSON、结构校验失败都抛 LLMError。
        """
        if not self.settings.ark_api_key:
            raise LLMError(NO_KEY)  # 请求没发出去，不记账
        payload = {
            "model": self.settings.ark_model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema.__name__,
                                                "schema": strict_schema(schema)}},
            "thinking": THINKING,
            "max_tokens": MAX_TOKENS,
        }
        try:
            url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
            resp = self.post(url,
                             headers={"Authorization": f"Bearer {self.settings.ark_api_key}"},
                             json=payload, timeout=timeout)
        except requests.Timeout as e:
            # 请求已发出、服务端可能已处理并计费：记一笔 0 用量超时
            self.store.add_llm_usage(f"{purpose}:timeout", 0, 0, 0.0)
            raise LLMError(f"{purpose}: 方舟请求超时（>{timeout:.0f}s）") from e
        except requests.RequestException as e:
            raise LLMError(f"{purpose}: 方舟网络错误：{e!r}") from e
        try:
            data = json.loads(resp.text)
        except ValueError as e:
            raise LLMError(f"{purpose}: 响应不是 JSON（HTTP {resp.status_code}）："
                           f"{_tail(resp.text)}") from e
        if resp.status_code != 200 or not isinstance(data, dict):
            msg = data.get("error", {}).get("message") if isinstance(data, dict) else resp.text
            raise LLMError(f"{purpose}: 方舟 HTTP {resp.status_code}：{_tail(msg)}")
        usage = data.get("usage") or {}
        self.store.add_llm_usage(purpose, _int(usage.get("prompt_tokens")),
                                 _int(usage.get("completion_tokens")), 0.0)
        try:
            content = str(data["choices"][0]["message"].get("content") or "")
        except (LookupError, AttributeError):
            raise LLMError(f"{purpose}: 响应缺少 choices："
                           f"{_tail(json.dumps(data, ensure_ascii=False))}")
        structured = _extract_json(content)
        if structured is None:
            raise LLMError(f"{purpose}: 回复里没有可解析的 JSON：{_tail(content)}")
        try:
            return schema.model_validate(structured)
        except ValidationError as e:
            raise LLMError(f"{purpose}: 输出不符合 {schema.__name__}：{e}") from e
