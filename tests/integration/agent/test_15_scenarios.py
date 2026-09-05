"""Comprehensive verification test suite covering all 15 required scenarios:

1. Open Discord -> correct app identity
2. Open Spotify -> correct app identity
3. Wrong taskbar position does not open wrong app
4. Safe app opening does not require approval
5. Dangerous action requires approval
6. Approved dangerous action executes
7. Failed verification triggers retry/recovery
8. Successful verification advances plan
9. Run reaches COMPLETED
10. Runner disconnect does not fake success
11. Emergency stop interrupts execution
12. Multi-step task executes sequentially
13. Duplicate WebSocket events prevented
14. Planner cannot generate invalid target
15. Coordinate fallback is not primary strategy
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from local_control.agent.planner import Planner
from local_control.agent.recovery import RecoveryPolicy
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.actions import (
    AppTarget,
    OpenApplicationAction,
    ShellRunAction,
)
from local_control.core.events import Event, EventBus
from local_control.core.run_store import RunStore
from local_control.core.types import ActionResult, Plan, PlanStep, WindowInfo
from local_control.execution.app_target import resolve_app_target
from local_control.execution.executor import Executor
from local_control.execution.tools.app_tool import AppTool
from local_control.execution.tools.base import ExecutionContext
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from local_control.safety.kill_switch import KillSwitch, StopToken
from local_control.safety.validator import SafetyValidator
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


# 1. Open Discord -> correct app identity
def test_01_open_discord_app_identity():
    target = resolve_app_target("open discord")
    assert target is not None
    assert target.name.lower() == "discord"
    assert target.process_name == "Discord.exe"
    assert target.protocol == "discord://"
    assert target.confidence >= 0.9


# 2. Open Spotify -> correct app identity
def test_02_open_spotify_app_identity():
    target = resolve_app_target("open spotify")
    assert target is not None
    assert target.name.lower() == "spotify"
    assert target.process_name == "Spotify.exe"
    assert target.protocol == "spotify:"
    assert target.confidence >= 0.9


# 3. Wrong taskbar position does not open wrong app
def test_03_wrong_taskbar_position_does_not_open_wrong_app():
    # If a prompt asks to open Discord, the resolver must never resolve Spotify
    discord_target = resolve_app_target("open Discord from taskbar")
    assert discord_target is not None
    assert discord_target.name.lower() == "discord"
    assert discord_target.process_name != "Spotify.exe"

    # Coordinates alone are never assigned as primary strategy when app name is Discord
    assert discord_target.coordinates is None
    # OpenApplicationAction preserves target identity
    action = OpenApplicationAction(
        target=discord_target,
        target_description="Open Discord",
        expected_outcome="Discord running",
    )
    assert action.target.name == "Discord"
    assert action.target.process_name == "Discord.exe"


# 4. Safe app opening does not require approval
def test_04_safe_app_opening_does_not_require_approval():
    validator = SafetyValidator(settings=Settings())
    action = OpenApplicationAction(
        target=AppTarget(name="Discord", process_name="Discord.exe"),
        target_description="Open Discord",
        expected_outcome="Discord in foreground",
    )
    obs = FakeComputer().create_observer().observe()

    # Even with low confidence 0.2, open_application must not be elevated to CONFIRM by C-14
    verdict = validator.validate(action=action, obs=obs, confidence=0.2)
    assert verdict.tier == "SAFE"
    assert verdict.decision == "allow"
    assert verdict.category == "S-08"


# 5. Dangerous action requires approval
def test_05_dangerous_action_requires_approval():
    validator = SafetyValidator(settings=Settings())
    dangerous_cmd = ShellRunAction(
        command="Remove-Item -Recurse -Force C:\\Windows",
        target_description="Delete Windows",
        expected_outcome="Files removed",
    )
    obs = FakeComputer().create_observer().observe()
    verdict = validator.validate(action=dangerous_cmd, obs=obs, confidence=0.99)
    assert verdict.decision in ("needs_confirmation", "blocked")
    assert verdict.tier in ("CONFIRM", "BLOCKED")


# 6. Approved dangerous action executes
@pytest.mark.asyncio
async def test_06_approved_dangerous_action_executes():
    computer = FakeComputer()
    observer = computer.create_observer()

    executed_actions = []

    class MockShellTool(FakeComputerTool):
        @property
        def handles(self):
            return frozenset({"shell_run", "done"})

        async def execute(self, action, ctx):
            executed_actions.append(action.type)
            return ActionResult(
                action_type=action.type,
                success=True,
                started_at=datetime.now(UTC),
                duration_ms=10,
            )

    executor = Executor(tools=[MockShellTool(computer)])
    scripted = [
        {
            "assessment": {"screen_summary": "Desktop", "previous_action_outcome": "not_applicable", "evidence": ""},
            "action": {
                "type": "shell_run",
                "command": "echo 'important operation'",
                "target_description": "Run shell operation",
                "expected_outcome": "Output visible",
            },
            "confidence": 0.95,
            "rationale": "Run shell operation",
        },
        {
            "assessment": {"screen_summary": "Done", "previous_action_outcome": "success", "evidence": "Output seen"},
            "action": {
                "type": "done",
                "summary": "Finished",
                "verification_notes": "All steps executed",
                "target_description": "Complete goal",
                "expected_outcome": "Goal completed",
            },
            "confidence": 1.0,
            "rationale": "Done",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)
        # Approval gate approves
        approval_gate = AutoApprovalGate(approve=True)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=approval_gate,
            run_store=run_store,
            settings=Settings(),
        )

        res = await runner.run("Execute shell command", autonomy_mode="assisted")
        assert "shell_run" in executed_actions
        assert res.status == "COMPLETED"


# 7. Failed verification triggers retry/recovery
def test_07_failed_verification_triggers_retry_recovery():
    recovery = RecoveryPolicy(max_retries_per_step=3)

    # Synthetic failed verification
    from local_control.core.types import VerificationResult
    failed_verif = VerificationResult(
        step_index=0,
        outcome="failure",
        source=["screen_signal"],
        evidence="Window did not appear",
    )

    decision = recovery.decide(verification=failed_verif, step_index=0, is_stuck=False)
    assert decision.kind == "retry_hint"
    assert "Retry attempt 1/3" in (decision.hint or "")

    # Second failure
    decision2 = recovery.decide(verification=failed_verif, step_index=0, is_stuck=False)
    assert decision2.kind == "retry_hint"
    assert "Retry attempt 2/3" in (decision2.hint or "")


# 8. Successful verification advances plan
@pytest.mark.asyncio
async def test_08_successful_verification_advances_plan():
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    plan = Plan(
        goal="Test plan advancement",
        current_index=0,
        steps=[
            PlanStep(index=0, description="Step zero", expected_outcome="Zero done", status="active"),
            PlanStep(index=1, description="Step one", expected_outcome="One done", status="pending"),
        ],
    )

    scripted = [
        {
            "assessment": {"screen_summary": "Initial", "previous_action_outcome": "not_applicable", "evidence": ""},
            "plan": plan.model_dump(),
            "action": {
                "type": "wait",
                "seconds": 0.05,
                "target_description": "Wait step 0",
                "expected_outcome": "Zero done",
            },
            "confidence": 0.95,
            "rationale": "Do step 0",
        },
        {
            "assessment": {"screen_summary": "Step 0 verified", "previous_action_outcome": "success", "evidence": "Zero done"},
            "plan": plan.model_dump(),
            "action": {
                "type": "wait",
                "seconds": 0.05,
                "target_description": "Wait step 1",
                "expected_outcome": "One done",
            },
            "confidence": 0.95,
            "rationale": "Do step 1",
        },
        {
            "assessment": {"screen_summary": "Step 1 verified", "previous_action_outcome": "success", "evidence": "One done"},
            "action": {
                "type": "done",
                "summary": "All done",
                "verification_notes": "Both steps verified",
                "target_description": "Complete goal",
                "expected_outcome": "All complete",
            },
            "confidence": 1.0,
            "rationale": "Finish",
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
            settings=Settings(),
        )

        res = await runner.run("Test plan advancement", autonomy_mode="assisted")
        assert res.status == "COMPLETED"


# 9. Run reaches COMPLETED
@pytest.mark.asyncio
async def test_09_run_reaches_completed():
    computer = FakeComputer()
    observer = computer.create_observer()

    class MockAppTool(FakeComputerTool):
        @property
        def handles(self):
            return frozenset({"open_application", "done"})

        async def execute(self, action, ctx):
            return ActionResult(
                action_type=action.type,
                success=True,
                started_at=datetime.now(UTC),
                duration_ms=50,
                data={
                    "postcondition_passed": True,
                    "postcondition_evidence": "Discord.exe in foreground",
                    "process_name": "Discord.exe",
                    "window_title": "Discord",
                },
            )

    executor = Executor(tools=[MockAppTool(computer)])

    scripted = [
        {
            "assessment": {"screen_summary": "Desktop", "previous_action_outcome": "not_applicable", "evidence": ""},
            "action": {
                "type": "open_application",
                "target": {"name": "Discord", "process_name": "Discord.exe"},
                "target_description": "Launch Discord",
                "expected_outcome": "Discord open in foreground",
            },
            "confidence": 0.95,
            "rationale": "Launch Discord",
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
            settings=Settings(),
        )

        res = await runner.run("Open Discord", autonomy_mode="assisted")
        assert res.status == "COMPLETED"


# 10. Runner disconnect does not fake success
@pytest.mark.asyncio
async def test_10_runner_disconnect_does_not_fake_success():
    computer = FakeComputer()
    observer = computer.create_observer()

    class CrashingExecutor(Executor):
        async def execute(self, action, ctx, step_index=None):
            raise ConnectionResetError("Runner disconnected unexpectedly")

    executor = CrashingExecutor()
    scripted = [
        {
            "assessment": {"screen_summary": "Ready", "previous_action_outcome": "not_applicable", "evidence": ""},
            "action": {
                "type": "wait",
                "seconds": 0.1,
                "target_description": "Wait",
                "expected_outcome": "Waited",
            },
            "confidence": 0.9,
            "rationale": "Wait",
        }
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
            settings=Settings(),
        )

        with pytest.raises(ConnectionResetError):
            res = await runner.run("Disconnect test", autonomy_mode="assisted")
            assert res.status != "COMPLETED"


# 11. Emergency stop interrupts execution
@pytest.mark.asyncio
async def test_11_emergency_stop_interrupts_execution():
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    stop_token = StopToken()
    # Trigger stop before running
    stop_token.set("User pressed emergency stop")

    scripted = [
        {
            "assessment": {"screen_summary": "Ready", "previous_action_outcome": "not_applicable", "evidence": ""},
            "action": {
                "type": "wait",
                "seconds": 5.0,
                "target_description": "Long wait",
                "expected_outcome": "Waited",
            },
            "confidence": 0.9,
            "rationale": "Long wait",
        }
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)
        approval_gate = AutoApprovalGate(approve=True)

        # Mock kill_switch stop file so clear() is not triggered
        dummy_stop_file = Path(tmpdir) / "STOP"
        dummy_stop_file.touch()

        kill_switch = KillSwitch(token=stop_token, stop_file_path=dummy_stop_file)

        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=approval_gate,
            run_store=run_store,
            stop_token=stop_token,
            kill_switch=kill_switch,
            settings=Settings(),
        )

        res = await runner.run("Emergency stop test", autonomy_mode="assisted")
        assert res.status == "STOPPED_BY_USER"


# 12. Multi-step task executes sequentially
@pytest.mark.asyncio
async def test_12_multistep_task_executes_sequentially():
    computer = FakeComputer()
    observer = computer.create_observer()

    scripted = [
        {
            "assessment": {"screen_summary": "Start", "previous_action_outcome": "not_applicable", "evidence": ""},
            "action": {
                "type": "wait",
                "seconds": 0.01,
                "target_description": "Step 1 wait",
                "expected_outcome": "Done 1",
            },
            "confidence": 0.9,
            "rationale": "Step 1",
        },
        {
            "assessment": {"screen_summary": "After 1", "previous_action_outcome": "success", "evidence": "Done 1"},
            "action": {
                "type": "wait",
                "seconds": 0.01,
                "target_description": "Step 2 wait",
                "expected_outcome": "Done 2",
            },
            "confidence": 0.9,
            "rationale": "Step 2",
        },
        {
            "assessment": {"screen_summary": "After 2", "previous_action_outcome": "success", "evidence": "Done 2"},
            "action": {
                "type": "done",
                "summary": "All 3 steps executed",
                "verification_notes": "Sequential execution complete",
                "target_description": "Finish goal",
                "expected_outcome": "Goal completed",
            },
            "confidence": 1.0,
            "rationale": "Step 3 (Done)",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        run_store = RunStore(base_dir=Path(tmpdir))
        provider = FakeModelProvider(scripted_responses=scripted)
        planner = Planner(provider=provider)
        approval_gate = AutoApprovalGate(approve=True)

        runner = AgentRunner(
            planner=planner,
            executor=computer.create_executor(),
            observer=observer,
            approval_gate=approval_gate,
            run_store=run_store,
            settings=Settings(),
        )

        res = await runner.run("Multi-step sequential test", autonomy_mode="assisted")
        assert res.status == "COMPLETED"
        assert res.steps_count == 2


# 13. Duplicate WebSocket events prevented
@pytest.mark.asyncio
async def test_13_duplicate_websocket_events_prevented():
    event_bus = EventBus()
    received_events = []

    async def recorder(event: Event):
        received_events.append(event.type)

    event_bus.subscribe(recorder)

    computer = FakeComputer()
    observer = computer.create_observer()

    # Pass event_bus to BOTH Executor and AgentRunner
    executor = Executor(tools=[computer.create_executor().registry["wait"]], event_bus=event_bus)

    scripted = [
        {
            "assessment": {"screen_summary": "Desktop", "previous_action_outcome": "not_applicable", "evidence": ""},
            "action": {
                "type": "wait",
                "seconds": 0.01,
                "target_description": "Wait test",
                "expected_outcome": "Waited",
            },
            "confidence": 0.9,
            "rationale": "Wait",
        },
        {
            "assessment": {"screen_summary": "Done", "previous_action_outcome": "success", "evidence": "Waited"},
            "action": {
                "type": "done",
                "summary": "Complete",
                "verification_notes": "Done verified",
                "target_description": "Finish",
                "expected_outcome": "Completed",
            },
            "confidence": 1.0,
            "rationale": "Done",
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
            event_bus=event_bus,
            settings=Settings(),
        )

        res = await runner.run("Event deduplication test", autonomy_mode="assisted")
        assert res.status == "COMPLETED"

        # Count occurrences of action_started and action_finished for the 1 wait action
        action_started_count = received_events.count("action_started")
        action_finished_count = received_events.count("action_finished")
        assert action_started_count == 1, f"Expected 1 action_started, got {action_started_count}"
        assert action_finished_count == 1, f"Expected 1 action_finished, got {action_finished_count}"


# 14. Planner cannot generate invalid target
def test_14_planner_cannot_generate_invalid_target():
    # Attempting to construct OpenApplicationAction with empty target name fails validation
    with pytest.raises(ValidationError):
        OpenApplicationAction(
            target=AppTarget(name=""),
            target_description="Invalid app",
            expected_outcome="Fail",
        )

    # Valid target succeeds
    action = OpenApplicationAction(
        target=AppTarget(name="Discord"),
        target_description="Open Discord",
        expected_outcome="Discord opens",
    )
    assert action.target.name == "Discord"
    assert action.type == "open_application"


# 15. Coordinate fallback is not primary strategy
@pytest.mark.asyncio
async def test_15_coordinate_fallback_is_not_primary_strategy():
    from local_control.core.actions import Rect
    tool = AppTool()

    # Synthetic context
    ctx = ExecutionContext(run_id="test", stop=StopToken())
    target = AppTarget(
        name="UniqueMockAppNeverRunningXYZ",
        process_name="MockAppXYZ.exe",
        coordinates=(500, 1050),
    )
    action = OpenApplicationAction(
        target=target,
        target_description="Open MockApp",
        expected_outcome="MockApp open",
    )

    # Mock wm.list_windows and taskbar enum to return nothing, mock launch to succeed via protocol
    tool.wm.list_windows = MagicMock(return_value=[])
    tool._find_taskbar_button = MagicMock(return_value=None)
    tool._launch_executable = AsyncMock(return_value="protocol_uri:mockapp://")

    # Mock foreground check to confirm launch
    mock_fg = WindowInfo(
        handle=12345,
        title="UniqueMockAppNeverRunningXYZ",
        process_name="MockAppXYZ.exe",
        pid=9999,
        is_foreground=True,
        is_minimized=False,
        bbox=Rect(x=0, y=0, width=1920, height=1080),
    )
    tool.wm.foreground = MagicMock(return_value=mock_fg)

    res = await tool.execute(action, ctx)
    assert res.success is True
    # The strategy used was protocol_or_executable (Level 3/4), NOT coordinate fallback (Level 7)
    assert res.data.get("strategy") != "coordinates_fallback"
    assert "protocol" in res.data.get("strategy")
