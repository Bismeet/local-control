"""Unit tests for OpenAiCompatProvider with mocked HTTP transport."""

import json

import httpx
import pytest

from local_control.core.errors import ProviderError
from local_control.models.openai_compat import OpenAiCompatProvider
from local_control.models.provider import Message, ModelRequest, TextPart


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_compat_success() -> None:
    expected_content = json.dumps(
        {
            "assessment": {
                "screen_summary": "Clean desktop",
                "previous_action_outcome": "not_applicable",
                "evidence": "Observed windows",
            },
            "action": {
                "type": "click",
                "x": 200,
                "y": 300,
                "target_description": "Submit",
                "expected_outcome": "Form submitted",
            },
            "confidence": 0.95,
            "rationale": "Click submit button",
        }
    )

    def handle_request(request: httpx.Request) -> httpx.Response:
        data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"role": "assistant", "content": expected_content}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }
        return httpx.Response(200, json=data)

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport)

    provider = OpenAiCompatProvider(client=client)
    req = ModelRequest(
        system="System prompt",
        messages=[Message(role="user", parts=[TextPart(text="Hello")])],
    )

    resp = await provider.complete(req)
    assert resp.parsed is not None
    assert resp.parsed["action"]["type"] == "click"
    assert resp.usage.input_tokens == 50
    assert resp.usage.output_tokens == 20


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_compat_retry_on_server_error() -> None:
    attempts = 0

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"error": "Internal Server Error"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action": {"type": "wait"}}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport)

    provider = OpenAiCompatProvider(client=client)
    req = ModelRequest(messages=[Message(role="user", parts=[TextPart(text="test")])])

    resp = await provider.complete(req)
    assert attempts == 2
    assert resp.parsed == {"action": {"type": "wait"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_compat_exhausted_retries_raises() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "Service Unavailable"})

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport)

    provider = OpenAiCompatProvider(client=client)
    req = ModelRequest(messages=[Message(role="user", parts=[TextPart(text="test")])])

    with pytest.raises(ProviderError):
        await provider.complete(req)
