"""Unit tests for Executor and tool dispatch."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_control.config.settings import Settings
from local_control.core.actions import (
    Action,
    ClickAction,
    TypeTextAction,
    WaitAction,
)
from local_control.core.coordinates import CoordinateMapper
from local_control.core.events import Event, EventBus
from local_control.core.types import ActionResult, ImageRef, ScreenGeometry
from local_control.execution.executor import Executor
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.execution.tools.input_backend import FakeInputBackend
from local_control.execution.tools.input_tool import InputTool
from local_control.execution.tools.wait_tool import WaitTool
from local_control.safety.kill_switch import StopToken


class FakeTool(Tool):
    """Test tool supporting controlled delays and failure simulation."""

    def __init__(self, should_fail: bool = False, delay_s: float = 0.0) -> None:
        self.should_fail = should_fail
        self.delay_s = delay_s
        self.executed_actions: list[Action] = []

    @property
    def handles(self) -> frozenset[str]:
        return frozenset({"click", "move_mouse"})

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        self.executed_actions.append(action)
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)

        if self.should_fail:
            raise RuntimeError("FakeTool intentional failure")

        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=datetime.now(UTC),
            duration_ms=int(self.delay_s * 1000),
            data={"status": "fake_success"},
        )


@pytest.fixture
def execution_context() -> ExecutionContext:
    screen = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
    image = ImageRef(
        path_original="",
        path_model="",
        model_width=960,
        model_height=540,
        phash="0" * 16,
    )
    mapper = CoordinateMapper(screen=screen, image=image)
    return ExecutionContext(
        run_id="test-run",
        stop=StopToken(),
        mapper=mapper,
        settings=Settings.load(),
        workdir=Path("."),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_dispatch_success(execution_context: ExecutionContext) -> None:
    tool = FakeTool()
    executor = Executor(tools=[tool])
    action = ClickAction(
        x=100,
        y=100,
        target_description="test button",
        expected_outcome="button clicked",
        settle_ms=0,
    )

    result = await executor.execute(action, execution_context)
    assert result.success
    assert result.action_type == "click"
    assert result.data.get("status") == "fake_success"
    assert len(tool.executed_actions) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_no_tool_found(execution_context: ExecutionContext) -> None:
    tool = FakeTool()
    executor = Executor(tools=[tool])
    action = WaitAction(
        seconds=1.0,
        target_description="pause",
        expected_outcome="wait completed",
    )

    result = await executor.execute(action, execution_context)
    assert not result.success
    assert result.error is not None
    assert result.error.code == "NO_TOOL_FOUND"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_exception_wrapping(execution_context: ExecutionContext) -> None:
    tool = FakeTool(should_fail=True)
    executor = Executor(tools=[tool])
    action = ClickAction(
        x=50,
        y=50,
        target_description="fail button",
        expected_outcome="should fail",
        settle_ms=0,
    )

    result = await executor.execute(action, execution_context)
    assert not result.success
    assert result.error is not None
    assert result.error.code == "EXECUTION_ERROR"
    assert "FakeTool intentional failure" in result.error.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_stop_token_aborts(execution_context: ExecutionContext) -> None:
    tool = FakeTool()
    executor = Executor(tools=[tool])
    execution_context.stop.set("manual_stop")

    action = ClickAction(
        x=50,
        y=50,
        target_description="button",
        expected_outcome="clicked",
        settle_ms=0,
    )
    result = await executor.execute(action, execution_context)
    assert not result.success
    assert result.error is not None
    assert result.error.code == "STOPPED_BY_USER"
    assert "manual_stop" in result.error.message
    assert len(tool.executed_actions) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_events_emitted(execution_context: ExecutionContext) -> None:
    bus = EventBus()
    events: list[Event] = []

    async def on_event(ev: Event) -> None:
        events.append(ev)

    bus.subscribe(on_event)

    tool = FakeTool()
    executor = Executor(tools=[tool], event_bus=bus)
    action = ClickAction(
        x=50,
        y=50,
        target_description="button",
        expected_outcome="clicked",
        settle_ms=0,
    )

    result = await executor.execute(action, execution_context, step_index=1)
    assert result.success
    assert len(events) == 2
    assert events[0].type == "action_started"
    assert events[0].step_index == 1
    assert events[1].type == "action_finished"
    assert events[1].step_index == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_tool_coordinate_mapping(execution_context: ExecutionContext) -> None:
    fake_backend = FakeInputBackend()
    tool = InputTool(backend=fake_backend)
    executor = Executor(tools=[tool])

    # Model image is 960x540, screen is 1920x1080 (scale = 2.0)
    # Click at (100, 200) model space should map to (200, 400) screen space
    action = ClickAction(
        x=100,
        y=200,
        button="left",
        clicks=1,
        target_description="button at 100,200",
        expected_outcome="button clicked",
        settle_ms=0,
    )
    result = await executor.execute(action, execution_context)

    assert result.success
    assert len(fake_backend.calls) == 1
    name, args = fake_backend.calls[0]
    assert name == "click"
    assert args["x"] == 200
    assert args["y"] == 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_tool_type_text_stop_token(execution_context: ExecutionContext) -> None:
    fake_backend = FakeInputBackend()
    tool = InputTool(backend=fake_backend)
    executor = Executor(tools=[tool])

    # Normal typing succeeds
    action = TypeTextAction(
        text="hello world",
        target_description="text input",
        expected_outcome="text entered",
        settle_ms=0,
    )
    res = await executor.execute(action, execution_context)
    assert res.success
    assert fake_backend.calls[-1] == ("type_text", {"text": "hello world"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_wait_tool_execution(execution_context: ExecutionContext) -> None:
    tool = WaitTool()
    executor = Executor(tools=[tool])

    action = WaitAction(
        seconds=0.1,
        target_description="short pause",
        expected_outcome="pause completed",
    )
    res = await executor.execute(action, execution_context)
    assert res.success
    assert res.action_type == "wait"
    assert res.duration_ms >= 80


@pytest.mark.unit
@pytest.mark.asyncio
async def test_wait_tool_stops_promptly(execution_context: ExecutionContext) -> None:
    tool = WaitTool()
    executor = Executor(tools=[tool])

    # Schedule stop token trigger in 100ms
    async def trigger_stop() -> None:
        await asyncio.sleep(0.1)
        execution_context.stop.set("timed_stop")

    asyncio.create_task(trigger_stop())

    # Wait action for 5.0 seconds
    action = WaitAction(
        seconds=5.0,
        target_description="long pause",
        expected_outcome="interrupted",
    )
    res = await executor.execute(action, execution_context)

    assert not res.success
    assert res.error is not None
    assert res.error.code == "STOPPED_BY_USER"
    # Should stop in less than 500ms
    assert res.duration_ms < 500
