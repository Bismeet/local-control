"""Integration tests for verification and recovery ladder (Phase 6)."""

import tempfile
from pathlib import Path

import pytest

from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.events import Event, EventBus
from local_control.core.run_store import RunStore
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recovery_ladder_escalation_events() -> None:
    """Verify that repeated failure escalates: retry_hint -> retry_hint -> replan -> ask_user -> abort."""
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    # Scripted sequence of 6 turns: turn 0 starts, turns 1..5 report failure
    scripted = [
        # Turn 0: initial propose
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
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.9,
            "rationale": "Initial click",
        },
        # Turn 1: failure -> ladder emits retry_hint (1/2)
        {
            "assessment": {
                "screen_summary": "No change",
                "previous_action_outcome": "failure",
                "evidence": "Click did not open window",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 101,
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.8,
            "rationale": "Retry click 1",
        },
        # Turn 2: failure -> ladder emits retry_hint (2/2)
        {
            "assessment": {
                "screen_summary": "No change",
                "previous_action_outcome": "failure",
                "evidence": "Click did not open window again",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 102,
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.8,
            "rationale": "Retry click 2",
        },
        # Turn 3: failure -> ladder emits replan
        {
            "assessment": {
                "screen_summary": "No change",
                "previous_action_outcome": "failure",
                "evidence": "Retries exhausted",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 103,
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.8,
            "rationale": "Before replan",
        },
        # Replan proposal (called with replan_reason)
        {
            "assessment": {
                "screen_summary": "Replanning screen",
                "previous_action_outcome": "not_applicable",
                "evidence": "Replanned",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 104,
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.8,
            "rationale": "Click following replan",
        },
        # Turn 4: failure -> ladder emits ask_user
        {
            "assessment": {
                "screen_summary": "No change",
                "previous_action_outcome": "failure",
                "evidence": "Failed after replan",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 105,
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.8,
            "rationale": "Click after ask user",
        },
        # Turn 5: failure -> ladder emits abort!
        {
            "assessment": {
                "screen_summary": "No change",
                "previous_action_outcome": "failure",
                "evidence": "Still failing",
            },
            "action": {
                "type": "click",
                "x": 100,
                "y": 106,
                "target_description": "Target button",
                "expected_outcome": "Window opens",
            },
            "confidence": 0.8,
            "rationale": "Final failing click attempt",
        },
    ]

    event_bus = EventBus()
    recovery_events: list[Event] = []

    async def record_recovery(event: Event) -> None:
        recovery_events.append(event)

    event_bus.subscribe(record_recovery, event_type="recovery_decision")

    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings.load()
        settings.budget.consecutive_failures_limit = 10
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=AutoApprovalGate(approve=True, user_answer="try clicking harder"),
            run_store=run_store,
            event_bus=event_bus,
            settings=settings,
        )

        result = await runner.run(goal="Test recovery ladder escalation")
        assert result.status == "ABORTED_BY_AGENT"

        # Check exact sequence of recovery decisions
        kinds = [e.payload["kind"] for e in recovery_events]
        assert kinds == ["retry_hint", "retry_hint", "replan", "ask_user", "abort"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_done_verification_rejected_then_accepted() -> None:
    """Verify that done proposed with a failing assessment is rejected, and accepted after success."""
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    scripted = [
        # Turn 0: click action
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
                "target_description": "Target",
                "expected_outcome": "Opened",
            },
            "confidence": 0.9,
            "rationale": "Click",
        },
        # Turn 1: Propose DONE but previous action assessment is FAILURE -> REJECTED!
        {
            "assessment": {
                "screen_summary": "Nothing opened",
                "previous_action_outcome": "failure",
                "evidence": "Action failed to open target",
            },
            "action": {
                "type": "done",
                "summary": "Premature done",
                "verification_notes": "Trying to finish anyway",
                "target_description": "Done",
                "expected_outcome": "Finished",
            },
            "confidence": 0.9,
            "rationale": "Try done",
        },
        # Turn 2: Try wait to recover
        {
            "assessment": {
                "screen_summary": "Wait a bit",
                "previous_action_outcome": "not_applicable",
                "evidence": "Recovering",
            },
            "action": {
                "type": "wait",
                "seconds": 0.1,
                "target_description": "Wait",
                "expected_outcome": "Waited",
            },
            "confidence": 0.9,
            "rationale": "Wait",
        },
        # Turn 3: Propose DONE with assessment SUCCESS -> ACCEPTED!
        {
            "assessment": {
                "screen_summary": "All good",
                "previous_action_outcome": "success",
                "evidence": "Goal completely verified",
            },
            "action": {
                "type": "done",
                "summary": "Legitimate done",
                "verification_notes": "All steps verified",
                "target_description": "Done",
                "expected_outcome": "Finished",
            },
            "confidence": 0.95,
            "rationale": "Finish",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=AutoApprovalGate(approve=True),
            run_store=run_store,
        )

        result = await runner.run(goal="Test done verification")
        assert result.status == "COMPLETED"

        # Check that request #3 (index 2) contains the rejection notice for premature done
        assert len(provider.requests) >= 3
        req2 = provider.requests[2]
        msg2_text = getattr(req2.messages[0].parts[0], "text", "")
        assert "previous action outcome was failure" in msg2_text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ask_user_action_execution() -> None:
    """Verify that ask_user action pauses in WAITING_USER and feeds answer to prompt."""
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    scripted = [
        # Turn 0: Propose ask_user
        {
            "assessment": {
                "screen_summary": "Need help",
                "previous_action_outcome": "not_applicable",
                "evidence": "Unsure which button",
            },
            "action": {
                "type": "ask_user",
                "question": "Which button should I click?",
                "target_description": "Ask user",
                "expected_outcome": "User answers",
            },
            "confidence": 0.5,
            "rationale": "Ask user for button",
        },
        # Turn 1: Sees answer, proposes done
        {
            "assessment": {
                "screen_summary": "User answered",
                "previous_action_outcome": "success",
                "evidence": "Got button info",
            },
            "action": {
                "type": "done",
                "summary": "Completed after user guidance",
                "verification_notes": "Done",
                "target_description": "Done",
                "expected_outcome": "Done",
            },
            "confidence": 1.0,
            "rationale": "Finish",
        },
    ]

    gate = AutoApprovalGate(approve=True, user_answer="Click the green button")

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=gate,
            run_store=run_store,
        )

        result = await runner.run(goal="Test ask_user action")
        assert result.status == "COMPLETED"

        # Request #2 should have received the user answer
        assert len(provider.requests) >= 2
        req2 = provider.requests[1]
        msg2_text = getattr(req2.messages[0].parts[0], "text", "")
        assert "Click the green button" in msg2_text
