"""Unit tests for Planner prompt building, JSON parsing, and retry handling."""

import json
from datetime import UTC, datetime

import pytest

from local_control.agent.planner import Planner, strip_markdown_fences
from local_control.core.actions import Point
from local_control.core.errors import PlannerError
from local_control.core.types import (
    ImageRef,
    Observation,
    ScreenGeometry,
    TaskState,
)
from local_control.models.fake import FakeModelProvider


@pytest.fixture
def dummy_observation() -> Observation:
    return Observation(
        step_index=0,
        captured_at=datetime.now(UTC),
        screen=ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0),
        image=ImageRef(
            path_original="",
            path_model="",
            model_width=960,
            model_height=540,
            phash="0" * 16,
        ),
        cursor=Point(x=100, y=100),
    )


@pytest.fixture
def dummy_state() -> TaskState:
    return TaskState(
        run_id="test-run",
        goal="Click the button",
        autonomy_mode="step",
    )


VALID_PROPOSAL = {
    "assessment": {
        "screen_summary": "Window open",
        "previous_action_outcome": "not_applicable",
        "evidence": "Observed window title",
    },
    "action": {
        "type": "click",
        "x": 150,
        "y": 250,
        "target_description": "Target button",
        "expected_outcome": "Button clicked",
    },
    "confidence": 0.9,
    "rationale": "Directly clicking button",
}


@pytest.mark.unit
def test_strip_markdown_fences() -> None:
    raw = '```json\n{"key": "val"}\n```'
    assert strip_markdown_fences(raw) == '{"key": "val"}'

    raw2 = '{"key": "val"}'
    assert strip_markdown_fences(raw2) == '{"key": "val"}'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_propose_valid_json(
    dummy_state: TaskState, dummy_observation: Observation
) -> None:
    provider = FakeModelProvider(scripted_responses=[VALID_PROPOSAL])
    planner = Planner(provider=provider)

    resp = await planner.propose(dummy_state, dummy_observation)
    assert resp.action.type == "click"
    assert resp.confidence == 0.9
    assert len(provider.requests) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_propose_fenced_markdown(
    dummy_state: TaskState, dummy_observation: Observation
) -> None:
    fenced_str = f"```json\n{json.dumps(VALID_PROPOSAL)}\n```"
    provider = FakeModelProvider(scripted_responses=[fenced_str])
    planner = Planner(provider=provider)

    resp = await planner.propose(dummy_state, dummy_observation)
    assert resp.action.type == "click"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_retry_on_invalid_json(
    dummy_state: TaskState, dummy_observation: Observation
) -> None:
    # First response invalid text, second valid
    provider = FakeModelProvider(
        scripted_responses=[
            "This is not JSON at all!",
            VALID_PROPOSAL,
        ]
    )
    planner = Planner(provider=provider)

    resp = await planner.propose(dummy_state, dummy_observation)
    assert resp.action.type == "click"
    assert len(provider.requests) == 2

    # Verify retry feedback was included in the second prompt
    second_request = provider.requests[1]
    second_msg = second_request.messages[0].parts[0]
    assert "RETRY ERROR" in getattr(second_msg, "text", "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_exhausted_retries_raises(
    dummy_state: TaskState, dummy_observation: Observation
) -> None:
    # 3 invalid responses
    provider = FakeModelProvider(
        scripted_responses=[
            "invalid 1",
            "invalid 2",
            "invalid 3",
        ]
    )
    planner = Planner(provider=provider)

    with pytest.raises(PlannerError):
        await planner.propose(dummy_state, dummy_observation)
    assert len(provider.requests) == 3
