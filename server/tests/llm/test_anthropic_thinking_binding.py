"""Exercise prefix-binding recovery through the pinned Anthropic SDK transport.

These requests reuse signed thinking under a new delegated system prompt,
the condition that otherwise gets a 400 on current Claude models. Assert
the actual HTTP body/header, since older SDK TypedDicts omit the beta field.
"""

from copy import deepcopy
import json

import anthropic
import httpx
import pytest

from services.llm.protocol import Message, ThinkingConfig, ToolCall, ToolDef, message_to_wire
from services.llm.providers.anthropic import AnthropicProvider


BINDING_BETA = "thinking-binding-controls-2026-08-01"


def _stream_response(model):
    events = [
        {"type": "message_start", "message": {
            "id": "msg_recovered", "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0},
        }},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Recovered"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)


@pytest.fixture
async def transport_provider(monkeypatch):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append((request, body))
        return _stream_response(body["model"])

    sdk_client = anthropic.AsyncAnthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: sdk_client(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ))
    provider = AnthropicProvider("sk-test", max_retries=0)
    try:
        yield provider, requests
    finally:
        await provider._client.close()


def _delegated_history():
    # Empty thinking text still carries the original signature and must not
    # be stripped or rewritten. Text, tool_use and tool_result also survive.
    content = [
        {"type": "thinking", "thinking": "", "signature": "original-signature"},
        {"type": "redacted_thinking", "data": "original-redacted-data"},
        {"type": "text", "text": "Checking the source"},
        {"type": "tool_use", "id": "call_1", "name": "source", "input": {}},
    ]
    return [
        Message(role="system", content="A new delegated mission"),
        Message(role="user", content="The earlier mission"),
        Message(role="assistant", content="Checking the source",
                tool_calls=[ToolCall(id="call_1", name="source", args={})],
                provider_state={"provider": "anthropic", "payload": {"content": content}}),
        Message(role="tool", content="Source material", tool_call_id="call_1"),
        Message(role="user", content="Continue with the new mission"),
    ]


@pytest.mark.parametrize("thinking", [None, ThinkingConfig(enabled=False), ThinkingConfig(enabled=True)])
async def test_changed_delegated_prefix_keeps_signed_history_and_sends_recovery(transport_provider, thinking):
    provider, requests = transport_provider
    messages = _delegated_history()
    before = deepcopy([message_to_wire(message) for message in messages])
    result = await provider.chat(
        messages, model="claude-opus-5-5", max_tokens=128_000,
        thinking=thinking, tools=[ToolDef(name="source", description="Read", parameters={"type": "object"})],
    )

    assert result.content == "Recovered"
    assert len(requests) == 1
    request, body = requests[0]
    assert BINDING_BETA in request.headers["anthropic-beta"].split(",")
    assert body["thinking"]["type"] == "adaptive"
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert body["thinking"].get("display") == ("summarized" if thinking and thinking.enabled else None)
    assert "budget_tokens" not in body["thinking"]
    assert "temperature" not in body
    assert body["system"] == "A new delegated mission"
    assert body["messages"][1]["content"] == before[2]["provider_state"]["payload"]["content"]
    assert body["messages"][2]["content"][0]["tool_use_id"] == "call_1"
    assert [message_to_wire(message) for message in messages] == before


@pytest.mark.parametrize("model", [
    "claude-fable-5-1", "claude-mythos-5-1", "claude-opus-5-5",
    "claude-sonnet-5-5", "claude-haiku-5-5",
])
async def test_prefix_binding_is_enabled_for_configured_models(transport_provider, model):
    provider, requests = transport_provider
    await provider.chat([Message(role="user", content="Hello")], model=model)
    request, body = requests[0]
    assert body["thinking"]["block_binding"]["prefix_mismatch_behavior"] == "drop_block"
    assert BINDING_BETA in request.headers["anthropic-beta"]


@pytest.mark.parametrize("model", ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-sonnet-4-6"])
async def test_older_models_keep_existing_request_shape(transport_provider, model):
    provider, requests = transport_provider
    await provider.chat([Message(role="user", content="Hello")], model=model)
    request, body = requests[0]
    assert "thinking" not in body
    assert "anthropic-beta" not in request.headers
    assert body.get("temperature") == (0.7 if model == "claude-sonnet-4-6" else None)


async def test_binding_compaction_and_gateway_betas_are_merged_without_mutation(transport_provider):
    provider, requests = transport_provider
    headers = {
        "x-litellm-session-id": "session-1",
        "anthropic-beta": f"custom-beta,{BINDING_BETA}",
        "Anthropic-Beta": "other-beta",
    }
    extras = {"cache": {"use-cache": True, "namespace": "test-replay", "ttl": 172800}}
    before = deepcopy((headers, extras))
    await provider.chat(
        _delegated_history(), model="claude-opus-5-5",
        context_management={"type": "compaction", "compact_threshold": 150000},
        extra_headers=headers, extra_body=extras,
    )

    request, body = requests[0]
    betas = request.headers["anthropic-beta"].split(",")
    assert set(betas) == {BINDING_BETA, "compact-2026-01-12", "custom-beta", "other-beta"}
    assert len(betas) == len(set(betas))
    assert request.headers["x-litellm-session-id"] == "session-1"
    assert body["cache"] == extras["cache"]
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert body["context_management"]["edits"][0]["trigger"] == {"type": "input_tokens", "value": 150000}
    assert (headers, extras) == before
