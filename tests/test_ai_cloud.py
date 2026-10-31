"""Cloud AI services (ai/cloud.py): the presets, both request shapes, the
error messages, and the guarantee that a VIN never leaves the machine.

No test reaches the network: requests and the anthropic SDK are stubbed. The
VIN used is synthetic.
"""
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai import cloud  # noqa: E402
from ai.ollama import AIError, OllamaClient  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402

VIN = "WVWZZZE1ZTEST0001"          # synthetic, VIN-shaped


def _cfg(**ai):
    return Config({"ai": ai, "ollama": {"host": "http://localhost:11434",
                                        "model": "qwen2.5:7b"}})


# -- choosing a service ------------------------------------------------------------
def test_default_is_the_local_ollama():
    llm = cloud.llm_from_config(_cfg())
    assert isinstance(llm, OllamaClient) and llm.sends_offsite is False
    assert llm.where == "local Ollama"


def test_claude_defaults_to_opus_5_5_with_medium_effort():
    llm = cloud.llm_from_config(_cfg(provider="anthropic", api_key="k"))
    assert isinstance(llm, cloud.AnthropicClient)
    assert (llm.model, llm.effort, llm.sends_offsite) == \
        ("claude-opus-5-5", "medium", True)
    odd = cloud.llm_from_config(_cfg(provider="anthropic", effort="nonsense"))
    assert odd.effort == "medium"


@pytest.mark.parametrize("provider,host", [
    ("openai", "api.openai.com"), ("gemini", "generativelanguage.googleapis.com"),
    ("mistral", "api.mistral.ai"), ("groq", "api.groq.com"),
    ("openrouter", "openrouter.ai"), ("deepseek", "api.deepseek.com"),
    ("xai", "api.x.ai"),
])
def test_openai_compatible_services_have_their_endpoints(provider, host):
    llm = cloud.llm_from_config(_cfg(provider=provider, api_key="k", model="m"))
    assert isinstance(llm, cloud.OpenAICompatibleClient)
    assert llm.base_url.split("/")[2] == host


@pytest.mark.parametrize("provider,model", [
    ("gemini", "gemini-3.8-flash"), ("groq", "openai/gpt-oss-120b"),
    ("mistral", "mistral-small-latest"), ("openai", ""),
])
def test_the_free_services_need_only_a_key(provider, model):
    # Gemini, Groq and Mistral have free tiers; each gets a model that can
    # call tools, so a key alone is enough. The others still need one named.
    assert cloud.llm_from_config(_cfg(provider=provider, api_key="k")).model == model
    assert cloud.llm_from_config(
        _cfg(provider=provider, api_key="k", model="mine")).model == "mine"


def test_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("ODB_AI_KEY", "env-key")
    assert cloud.llm_from_config(_cfg(provider="openai", model="m")).key == "env-key"


def test_misconfiguration_never_stops_the_dashboard(tmp_path):
    from ai.service import AnalysisService
    repo = Repository(str(tmp_path / "a.db"))
    for bad in ({"provider": "nonsense"}, {"provider": "custom"}):
        svc = AnalysisService(_cfg(**bad), repo)
        assert svc.llm.where == "not configured"
        with pytest.raises(AIError):
            svc.llm.generate("x")
    repo.close()


# -- the VIN stays home --------------------------------------------------------------
def test_redact_removes_vins_everywhere():
    got = cloud.redact({"vehicle": {"vin": VIN, "model": "ID.3"},
                        "notes": [f"seen on {VIN}", "fine"], "n": 3}, VIN)
    assert got == {"vehicle": {"model": "ID.3"},
                   "notes": ["seen on [VIN]", "fine"], "n": 3}
    # An unknown VIN-shaped string is caught by its shape.
    assert cloud.redact("other WAUZZZ8V1KA000001 here") == "other [VIN] here"


def test_a_cloud_report_carries_no_vin(tmp_path):
    from ai.service import AnalysisService
    repo = Repository(str(tmp_path / "v.db"))
    repo.ensure_vehicle(VIN, make="VW", model="ID.3")
    repo.conn.commit()
    sent = {}

    class FakeCloud:
        sends_offsite, where, model = True, "Fake cloud", "fake-1"

        def generate(self, prompt, system=None, temperature=None):
            sent["prompt"], sent["system"] = prompt, system
            return "All fine.\n\nLIMITATIONS\n- synthetic"

    svc = AnalysisService(_cfg(), repo)
    svc.llm = FakeCloud()
    result = svc.ask(f"Is {VIN} healthy?")
    assert VIN not in sent["prompt"] and VIN not in (sent["system"] or "")
    assert VIN not in str(result["context"]) and VIN not in result["question"]
    stored = repo.conn.execute("SELECT * FROM llm_reports").fetchall()
    assert stored and all(VIN not in str(tuple(r)) for r in stored)
    repo.close()


# -- OpenAI-compatible request ----------------------------------------------------
class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else str(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


@pytest.fixture()
def http(monkeypatch):
    calls, replies = [], []

    def post(url, **kw):
        calls.append(("POST", url, kw))
        return replies.pop(0)

    def get(url, **kw):
        calls.append(("GET", url, kw))
        return replies.pop(0)
    monkeypatch.setattr(cloud.requests, "post", post)
    monkeypatch.setattr(cloud.requests, "get", get)
    return calls, replies


def _openai(**kw):
    return cloud.OpenAICompatibleClient("openai", kw.pop("key", "sk-test"),
                                        kw.pop("model", "some-model"), **kw)


def test_chat_completion_request_and_reply(http):
    calls, replies = http
    replies.append(_Reply(200, {"choices": [{"message": {"content": "Report text"},
                                             "finish_reason": "stop"}]}))
    got = _openai().generate("evidence", system="rules", temperature=0.2)
    assert got == "Report text"
    method, url, kw = calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert kw["headers"]["Authorization"] == "Bearer sk-test"
    body = kw["json"]
    assert body["messages"] == [{"role": "system", "content": "rules"},
                                {"role": "user", "content": "evidence"}]
    # Sampling and output caps are left to the service: several models reject them.
    assert "temperature" not in body and "max_tokens" not in body


def test_content_parts_and_truncation(http):
    _, replies = http
    replies.append(_Reply(200, {"choices": [{"message": {"content": [
        {"type": "text", "text": "Part one. "}, {"type": "text", "text": "Part two."}]},
        "finish_reason": "length"}]}))
    text = _openai().generate("q")
    assert text.startswith("Part one. Part two.") and cloud.TRUNCATED_MARKER in text


@pytest.mark.parametrize("reply,match", [
    (_Reply(401, "no"), "refused the API key"),
    (_Reply(404, "no"), "not found"),
    (_Reply(429, "slow"), "rate limit"),
    (_Reply(500, "boom"), "HTTP 500"),
    (_Reply(200, "<html>"), "non-JSON"),
    (_Reply(200, {"choices": []}), "Unexpected reply"),
    (_Reply(200, {"choices": [{"message": {"content": ""}}]}), "no text"),
])
def test_failures_are_plain_messages(http, reply, match):
    http[1].append(reply)
    with pytest.raises(cloud.CloudAIError, match=match):
        _openai().generate("q")


def test_no_key_and_no_model_say_what_to_do(http):
    with pytest.raises(cloud.CloudAIError, match="platform.openai.com"):
        _openai(key="").generate("q")
    http[1].append(_Reply(200, {"data": [{"id": "model-b"}, {"id": "model-a"}]}))
    with pytest.raises(cloud.CloudAIError, match="model-a, model-b"):
        _openai(model="").generate("q")


def test_network_failure_is_a_message(monkeypatch):
    def down(*a, **kw):
        raise cloud.requests.ConnectionError("offline")
    monkeypatch.setattr(cloud.requests, "post", down)
    with pytest.raises(cloud.CloudAIError, match="Could not reach OpenAI"):
        _openai().generate("q")


# -- Claude through the SDK --------------------------------------------------------------
@pytest.fixture()
def sdk(monkeypatch):
    """The anthropic client replaced by a recorder; the real exception classes stay."""
    import anthropic
    seen, replies = {}, []

    class FakeAnthropic:
        def __init__(self, api_key=None, timeout=None):
            seen["api_key"] = api_key

            def create(**kw):
                seen["kwargs"] = kw
                r = replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return seen, replies


def _message(blocks, stop="end_turn", details=None):
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=details)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def test_claude_request_shape(sdk):
    seen, replies = sdk
    replies.append(_message([
        SimpleNamespace(type="fallback"), SimpleNamespace(type="thinking", thinking=""),
        _text("Cells are balanced.")]))
    llm = cloud.AnthropicClient("sk-ant-test", effort="low")
    got = llm.generate("evidence", system="rules", temperature=0.2)
    assert got == "Cells are balanced."
    kw = seen["kwargs"]
    assert seen["api_key"] == "sk-ant-test"
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["system"] == "rules"
    assert kw["messages"] == [{"role": "user", "content": "evidence"}]
    assert kw["output_config"] == {"effort": "low"}
    # Refusal fallback opted in; no sampling parameters (rejected by Claude).
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["fallbacks"] == "default"
    assert "temperature" not in kw and "thinking" not in kw


def test_claude_refusal_and_truncation(sdk):
    _, replies = sdk
    replies.append(_message([], stop="refusal",
                            details=SimpleNamespace(category="cyber")))
    with pytest.raises(cloud.CloudAIError, match=r"declined.*cyber"):
        cloud.AnthropicClient("k").generate("q")
    replies.append(_message([_text("Long report")], stop="max_tokens"))
    assert cloud.TRUNCATED_MARKER in cloud.AnthropicClient("k").generate("q")


def test_claude_errors_are_plain_messages(sdk):
    import anthropic
    import httpx2
    _, replies = sdk
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    replies.append(anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=req), body=None))
    with pytest.raises(cloud.CloudAIError, match="refused the API key"):
        cloud.AnthropicClient("bad").generate("q")
    replies.append(anthropic.APIConnectionError(request=req))
    with pytest.raises(cloud.CloudAIError, match="Could not reach Anthropic"):
        cloud.AnthropicClient("k").generate("q")
    with pytest.raises(cloud.CloudAIError, match="console.anthropic.com"):
        cloud.AnthropicClient("").generate("q")


# -- the page says where the data goes ----------------------------------------------
def _page(tmp_path, **ai):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": str(tmp_path / "p.db")}, "ai": ai,
                  "web": {"host": "127.0.0.1"}})
    with TestClient(create_app(cfg, bind_host="127.0.0.1"),
                    raise_server_exceptions=False) as client:
        return client.get("/ai").text


def test_ai_page_names_the_service_and_warns_for_cloud(tmp_path):
    local = _page(tmp_path)
    assert "via local Ollama" in local and "Cloud AI:" not in local
    remote = _page(tmp_path, provider="anthropic", api_key="sk-ant-SECRET-123")
    assert "claude-opus-5-5 via Claude (Anthropic)" in remote
    assert "Cloud AI:" in remote and "VIN is removed" in remote
    assert "SECRET" not in remote                      # the key never reaches a page
