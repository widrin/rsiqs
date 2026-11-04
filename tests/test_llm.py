import json
from types import SimpleNamespace

import pytest
import requests
from pydantic import BaseModel, Field

from agents import llm as llm_mod
from agents.llm import LLM, LLMError, strict_schema
from config import Settings
from engine.store import Store


class Inner(BaseModel):
    code: str = Field(description="股票代码")
    score: float


class Outer(BaseModel):
    view: str
    items: list[Inner]
    note: str = ""


GOOD = {"view": "震荡", "items": [{"code": "600000", "score": 0.7}], "note": ""}
SYSTEM = "你是测试助手。\n只输出 JSON。"
USER = "请分析 600000 浦发银行的竞价情况"


def ark_body(content=None, *, usage=None):
    """方舟 /chat/completions 200 响应体（字段与 API 文档一致）。"""
    if content is None:
        content = json.dumps(GOOD, ensure_ascii=False)
    return {"id": "c-1", "choices": [{"message": {"role": "assistant", "content": content},
                                      "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 4700, "completion_tokens": 350,
                               "prompt_tokens_details": {"cached_tokens": 0}}}


class FakePost:
    """requests.post 替身：记录参数，返回预设响应（.status_code/.text，llm 用 json.loads 解析）。"""

    def __init__(self, *, status=200, body=None, text=None, exc=None):
        if body is None and text is None and exc is None:
            body = ark_body()
        self.status = status
        self.text = text if text is not None else json.dumps(body, ensure_ascii=False)
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(status_code=self.status, text=self.text)


def make_settings(root, **kw) -> Settings:
    base = dict(ark_api_key="ark-key", wecom_webhook_url="", root=root,
                llm_base_url="https://ark.cn-beijing.volces.com/api/v3")
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def usage_rows(store):
    return store.conn.execute("SELECT purpose, input_tokens, output_tokens, cost_cny FROM llm_usage").fetchall()


def this_month_cost(store):
    import datetime as dt
    t = dt.date.today()
    return store.month_llm_cost(t.year, t.month)


def chat_url(settings: Settings) -> str:
    return settings.llm_base_url.rstrip("/") + "/chat/completions"


def test_strict_schema_nested():
    s = strict_schema(Outer)
    assert s["additionalProperties"] is False
    assert sorted(s["required"]) == ["items", "note", "view"]
    inner = s["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert sorted(inner["required"]) == ["code", "score"]


def test_parse_posts_ark_request_and_returns_model(store, settings):
    post = FakePost()
    out = LLM(settings, store, post=post).parse("unit", SYSTEM, USER, Outer, timeout=30.0)
    assert isinstance(out, Outer) and out.view == "震荡" and out.items[0].code == "600000"
    assert len(post.calls) == 1
    url, kw = post.calls[0]
    assert url == chat_url(settings)
    assert kw["headers"] == {"Authorization": "Bearer ark-key"}
    assert kw["timeout"] == 30.0
    payload = kw["json"]
    assert payload["model"] == settings.ark_model
    assert payload["messages"] == [{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": USER}]
    assert payload["thinking"] == llm_mod.THINKING and payload["max_tokens"] == llm_mod.MAX_TOKENS
    rf = payload["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["name"] == "Outer"
    assert rf["json_schema"]["schema"] == strict_schema(Outer)
    assert "股票代码" in rf["json_schema"]["schema"]["$defs"]["Inner"]["properties"]["code"]["description"]


def test_custom_base_url_switches_provider(store, tmp_path):
    s = make_settings(tmp_path, llm_base_url="https://api.deepseek.com/v1/")
    post = FakePost()
    LLM(s, store, post=post).parse("unit", SYSTEM, USER, Outer)
    assert post.calls[0][0] == "https://api.deepseek.com/v1/chat/completions"  # 结尾斜杠已去掉


def test_usage_tokens_recorded_cost_is_zero(store, settings):
    body = ark_body(usage={"prompt_tokens": 4700, "completion_tokens": 350})
    LLM(settings, store, post=FakePost(body=body)).parse("unit", SYSTEM, USER, Outer)
    [(purpose, tokens_in, tokens_out, cost)] = usage_rows(store)
    assert (purpose, tokens_in, tokens_out, cost) == ("unit", 4700, 350, 0.0)
    assert this_month_cost(store) == 0.0  # 方舟价格随模型变动，不在此折算，只记 token


def test_extract_json_tolerates_markdown_fence():
    text = "说明如下：\n```json\n" + json.dumps(GOOD) + "\n```"
    assert llm_mod._extract_json(text) == GOOD


@pytest.mark.parametrize("status, text", [
    (401, '{"error":{"message":"invalid api key"}}'),
    (429, '{"error":{"message":"rate limit"}}'),
    (500, 'internal error'),
])
def test_http_error_raises_llm_error_without_usage(store, settings, status, text):
    with pytest.raises(LLMError, match=f"HTTP {status}"):
        LLM(settings, store, post=FakePost(status=status, text=text)).parse("unit", SYSTEM, USER, Outer)
    assert usage_rows(store) == []  # 非 200 没有产出，不记账


@pytest.mark.parametrize("content", [
    "纯文本回答，没有 JSON",
    "{'view': 单引号坏 JSON}",
])
def test_200_without_json_raises_llm_error_and_records_usage(store, settings, content):
    with pytest.raises(LLMError, match="没有可解析的 JSON"):
        LLM(settings, store, post=FakePost(body=ark_body(content=content))).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1  # 200 已产出 token，先记账再解析


def test_structured_output_failing_validation_raises_llm_error(store, settings):
    post = FakePost(body=ark_body(content=json.dumps({"view": 1})))
    with pytest.raises(LLMError, match="Outer"):
        LLM(settings, store, post=post).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1


def test_missing_choices_raises_llm_error_after_usage(store, settings):
    post = FakePost(body={"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 0}})
    with pytest.raises(LLMError, match="choices"):
        LLM(settings, store, post=post).parse("unit", SYSTEM, USER, Outer)
    assert len(usage_rows(store)) == 1


def test_non_json_body_raises_llm_error_without_usage(store, settings):
    with pytest.raises(LLMError, match="响应不是 JSON"):
        LLM(settings, store, post=FakePost(text="<html>网关错误</html>")).parse("unit", SYSTEM, USER, Outer)
    assert usage_rows(store) == []


def test_timeout_records_zero_usage(store, settings):
    post = FakePost(exc=requests.ReadTimeout("read timed out"))
    with pytest.raises(LLMError, match="超时"):
        LLM(settings, store, post=post).parse("unit", SYSTEM, USER, Outer, timeout=5.0)
    [(purpose, _, _, cost)] = usage_rows(store)
    assert purpose == "unit:timeout" and cost == 0.0  # 请求已发出，服务端可能已计费


def test_connection_error_raises_llm_error_without_usage(store, settings):
    with pytest.raises(LLMError, match="网络错误"):
        LLM(settings, store, post=FakePost(exc=requests.ConnectionError("refused"))).parse(
            "unit", SYSTEM, USER, Outer)
    assert usage_rows(store) == []


def test_missing_key_raises_llm_error_before_request(store, tmp_path):
    post = FakePost()
    llm = LLM(make_settings(tmp_path, ark_api_key=""), store, post=post)
    with pytest.raises(LLMError, match="ARK_API_KEY"):
        llm.parse("unit", SYSTEM, USER, Outer)
    assert post.calls == [] and usage_rows(store) == []
