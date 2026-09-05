"""Unit tests for FakeModelProvider."""

import pytest

from local_control.core.actions import ClickAction
from local_control.core.errors import ProviderError
from local_control.core.types import Assessment, PlannerResponse
from local_control.models.fake import FakeModelProvider
from local_control.models.provider import Message, ModelRequest, TextPart


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fake_model_provider_scripted() -> None:
    resp1 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Desktop visible",
            previous_action_outcome="not_applicable",
            evidence="Desktop is visible",
        ),
        action=ClickAction(
            x=100,
            y=100,
            target_description="Button",
            expected_outcome="Clicked",
        ),
        confidence=0.9,
        rationale="Click the button",
    )

    provider = FakeModelProvider(scripted_responses=[resp1])
    req = ModelRequest(messages=[Message(role="user", parts=[TextPart(text="test")])])

    res = await provider.complete(req)
    assert res.parsed is not None
    assert res.parsed["action"]["type"] == "click"
    assert len(provider.requests) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fake_model_provider_exhausted_raises() -> None:
    provider = FakeModelProvider(scripted_responses=[])
    req = ModelRequest(messages=[Message(role="user", parts=[TextPart(text="test")])])

    with pytest.raises(ProviderError):
        await provider.complete(req)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fake_model_provider_generator() -> None:
    def gen(req: ModelRequest) -> dict:
        return {
            "assessment": {
                "screen_summary": "Gen summary",
                "previous_action_outcome": "success",
                "evidence": "Gen evidence",
            },
            "action": {
                "type": "wait",
                "seconds": 1.0,
                "target_description": "pause",
                "expected_outcome": "wait",
            },
            "confidence": 0.8,
            "rationale": "Generated action",
        }

    provider = FakeModelProvider(generator=gen)
    req = ModelRequest(messages=[Message(role="user", parts=[TextPart(text="test")])])

    res = await provider.complete(req)
    assert res.parsed is not None
    assert res.parsed["action"]["type"] == "wait"
