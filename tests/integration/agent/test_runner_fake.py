"""Integration tests for AgentRunner using FakeModelProvider and FakeComputer."""

import tempfile
from pathlib import Path

import pytest

from local_control.agent.budget import Budget
from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.run_store import RunStore
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_executes_three_actions_and_done() -> None:
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    scripted = [
        {
            "assessment": {
                "screen_summary": "Desktop ready",
                "previous_action_outcome": "not_applicable",
                "evidence": "Start",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 100,
                "target_description": "First button",
                "expected_outcome": "First clicked",
            },
            "confidence": 0.9,
            "rationale": "Click 1",
        },
        {
            "assessment": {
                "screen_summary": "Clicked 1",
                "previous_action_outcome": "success",
                "evidence": "Button depressed",
            },
            "action": {
                "type": "type_text",
                "text": "Hello world",
                "target_description": "Text box",
                "expected_outcome": "Text typed",
            },
            "confidence": 0.9,
            "rationale": "Type text",
        },
        {
            "assessment": {
                "screen_summary": "Text entered",
                "previous_action_outcome": "success",
                "evidence": "Text visible",
            },
            "action": {
                "type": "wait",
                "seconds": 0.05,
                "target_description": "Brief wait",
                "expected_outcome": "Waited",
            },
            "confidence": 0.95,
            "rationale": "Settle",
        },
        {
            "assessment": {
                "screen_summary": "Done state",
                "previous_action_outcome": "success",
                "evidence": "All complete",
            },
            "action": {
                "type": "done",
                "summary": "Three actions executed successfully",
                "verification_notes": "All verified",
                "target_description": "Finish goal",
                "expected_outcome": "Goal complete",
            },
            "confidence": 1.0,
            "rationale": "Complete",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)
        approval_gate = AutoApprovalGate(approve=True)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=approval_gate,
            run_store=run_store,
        )

        result = await runner.run(goal="Execute three actions and complete")

        assert result.status == "COMPLETED"
        assert result.steps_count == 3
        assert len(computer.clicks) == 1
        assert computer.typed_texts == ["Hello world"]

        # Check summary.md was written
        run_dir = run_store.get_run_dir(result.run_id)
        summary_path = run_dir / "summary.md"
        assert summary_path.exists()
        summary_content = summary_path.read_text(encoding="utf-8")
        assert "**Final Status**: COMPLETED" in summary_content
        assert "Step 0" in summary_content
        assert "Step 1" in summary_content
        assert "Step 2" in summary_content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_feedback_queue_captured_in_next_prompt() -> None:
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    # Turn 1: Propose click -> User DENIES
    # Turn 2: Sees feedback notice -> Proposes wait
    # Turn 3: Proposes done
    scripted = [
        {
            "assessment": {
                "screen_summary": "Start",
                "previous_action_outcome": "not_applicable",
                "evidence": "Ready",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 100,
                "target_description": "Denied button",
                "expected_outcome": "Clicked",
            },
            "confidence": 0.8,
            "rationale": "Try clicking",
        },
        {
            "assessment": {
                "screen_summary": "After denial",
                "previous_action_outcome": "failure",
                "evidence": "Action denied by user",
            },
            "action": {
                "type": "wait",
                "seconds": 0.05,
                "target_description": "Wait instead",
                "expected_outcome": "Waited",
            },
            "confidence": 0.9,
            "rationale": "Fallback wait",
        },
        {
            "assessment": {
                "screen_summary": "Finish",
                "previous_action_outcome": "success",
                "evidence": "Finished",
            },
            "action": {
                "type": "done",
                "summary": "Recovered from denial",
                "verification_notes": "OK",
                "target_description": "Done",
                "expected_outcome": "Done",
            },
            "confidence": 1.0,
            "rationale": "Finish",
        },
    ]

    class DenyFirstGate:
        def __init__(self) -> None:
            self.count = 0

        async def arequest(self, action: object, prompt: str = "") -> bool:
            self.count += 1
            # Deny first action, approve subsequent
            return self.count > 1

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=DenyFirstGate(),  # type: ignore[arg-type]
            run_store=run_store,
        )

        result = await runner.run(goal="Test feedback queue")
        assert result.status == "COMPLETED"

        # Check that request #2 contains the denial feedback notice
        assert len(provider.requests) >= 2
        req2 = provider.requests[1]
        msg2_text = getattr(req2.messages[0].parts[0], "text", "")
        assert "NOTICE: Action 'click' was denied by human user." in msg2_text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_budget_exceeded_ends_with_failed_budget() -> None:
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    # Endless clicks
    def gen(req: object) -> dict:
        return {
            "assessment": {
                "screen_summary": "Looping",
                "previous_action_outcome": "success",
                "evidence": "Still running",
            },
            "action": {
                "type": "click",
                "x": 50,
                "y": 50,
                "target_description": "Loop click",
                "expected_outcome": "Clicked",
            },
            "confidence": 0.8,
            "rationale": "Keep clicking",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings.load()
        settings.budget.max_steps = 3  # Low limit for fast test
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(generator=gen)
        planner = Planner(provider=provider)
        budget = Budget(settings=settings)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=AutoApprovalGate(approve=True),
            run_store=run_store,
            budget=budget,
            settings=settings,
        )

        result = await runner.run(goal="Exceed budget")
        assert result.status == "FAILED_BUDGET"
        assert result.steps_count == 3
