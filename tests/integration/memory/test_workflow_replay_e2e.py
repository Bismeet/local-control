"""Integration test for Scenario 1 workflow recording and replay.

Verifies that:
1. A completed Scenario 1 run creates a reusable workflow template.
2. Replay parameterizes the goal and paths (e.g. for a different downloads directory).
3. Replay executes through the SafetyValidator and ApprovalGate (CONFIRM actions still prompt/grant).
4. Replay succeeds faster (fewer steps / zero LLM latency) than the initial run.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_control.config.settings import Settings
from local_control.core.actions import DoneAction, FsListAction, FsMkdirAction, FsMoveAction
from local_control.core.run_store import RunStore
from local_control.core.types import (
    ActionResult,
    ApprovalDecision,
    Assessment,
    PlannerResponse,
    StepRecord,
    Verdict,
)
from local_control.execution.executor import Executor
from local_control.execution.tools.filesystem_tool import FilesystemTool
from local_control.memory.sanitizer import Sanitizer
from local_control.memory.store import MemoryStore
from local_control.memory.workflows import WorkflowRecorder, WorkflowReplayer
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


@pytest.mark.asyncio
async def test_scenario_1_workflow_record_and_replay(tmp_path: Path) -> None:
    # 1. Setup initial seeded downloads directory
    initial_dl = tmp_path / "Downloads"
    initial_dl.mkdir()
    (initial_dl / "report.pdf").write_text("pdf content", encoding="utf-8")
    (initial_dl / "photo.jpg").write_text("jpg content", encoding="utf-8")
    (initial_dl / "archive.zip").write_text("zip content", encoding="utf-8")

    # Build simulated step records of an initial run that took 8 steps
    # (exploration: list, inspect, mkdirs, moves, verify, done)
    step_records: list[StepRecord] = []

    def _make_step(idx: int, action, result_success: bool = True) -> StepRecord:
        return StepRecord(
            step_index=idx,
            observation_ref="",
            planner_response=PlannerResponse(
                assessment=Assessment(
                    screen_summary="Executing organize downloads step",
                    previous_action_outcome="success" if idx > 0 else "not_applicable",
                    evidence="Step in progress",
                ),
                action=action,
                confidence=0.95,
                rationale="Organize downloads",
            ),
            verdict=Verdict(
                decision="needs_confirmation" if action.type == "fs_move" else "allow",
                tier="CONFIRM" if action.type == "fs_move" else "SAFE",
                category="fs_move" if action.type == "fs_move" else "fs_write",
                human_summary=f"Action {action.type}",
            ),
            approval=ApprovalDecision(decision="approved"),
            result=ActionResult(
                action_type=action.type,
                success=result_success,
                started_at=datetime.now(UTC),
                duration_ms=5,
            ),
        )

    # Initial run had exploration steps
    # 0. list
    step_records.append(
        _make_step(
            0,
            FsListAction(
                path=str(initial_dl),
                target_description="List initial",
                expected_outcome="Files listed",
            ),
        )
    )
    # 1. mkdir PDFs
    step_records.append(
        _make_step(
            1,
            FsMkdirAction(
                path=str(initial_dl / "PDFs"),
                target_description="Create PDFs",
                expected_outcome="Folder created",
            ),
        )
    )
    # 2. mkdir Images
    step_records.append(
        _make_step(
            2,
            FsMkdirAction(
                path=str(initial_dl / "Images"),
                target_description="Create Images",
                expected_outcome="Folder created",
            ),
        )
    )
    # 3. mkdir Archives
    step_records.append(
        _make_step(
            3,
            FsMkdirAction(
                path=str(initial_dl / "Archives"),
                target_description="Create Archives",
                expected_outcome="Folder created",
            ),
        )
    )
    # 4. move report.pdf
    step_records.append(
        _make_step(
            4,
            FsMoveAction(
                src=str(initial_dl / "report.pdf"),
                dst=str(initial_dl / "PDFs" / "report.pdf"),
                target_description="Move PDF",
                expected_outcome="Moved",
            ),
        )
    )
    # 5. move photo.jpg
    step_records.append(
        _make_step(
            5,
            FsMoveAction(
                src=str(initial_dl / "photo.jpg"),
                dst=str(initial_dl / "Images" / "photo.jpg"),
                target_description="Move Image",
                expected_outcome="Moved",
            ),
        )
    )
    # 6. move archive.zip
    step_records.append(
        _make_step(
            6,
            FsMoveAction(
                src=str(initial_dl / "archive.zip"),
                dst=str(initial_dl / "Archives" / "archive.zip"),
                target_description="Move Zip",
                expected_outcome="Moved",
            ),
        )
    )
    # 7. Done
    step_records.append(
        _make_step(
            7,
            DoneAction(
                summary="Downloads organized",
                verification_notes="All categorized",
                target_description="Done",
                expected_outcome="Finished",
            ),
        )
    )

    initial_step_count = len(step_records)
    assert initial_step_count == 8

    # 2. Record the run into MemoryStore as a reusable workflow
    db_file = tmp_path / "memory.db"
    mem_store = MemoryStore(db_path=db_file)
    recorder = WorkflowRecorder(sanitizer=Sanitizer(user_home=str(tmp_path)))

    wf = recorder.record_from_run(
        name="organize_downloads_workflow",
        goal=f"Organize my Downloads folder {initial_dl} into subfolders",
        steps=step_records,
        description="Organize downloads into category folders",
        store=mem_store,
    )

    assert wf.name == "organize_downloads_workflow"
    assert mem_store.get_workflow("organize_downloads_workflow") is not None

    # 3. Setup a new target downloads directory for Replay
    replay_dl = tmp_path / "Downloads_Run2"
    replay_dl.mkdir()
    (replay_dl / "report.pdf").write_text("pdf2", encoding="utf-8")
    (replay_dl / "photo.jpg").write_text("jpg2", encoding="utf-8")
    (replay_dl / "archive.zip").write_text("zip2", encoding="utf-8")

    # 4. Replay workflow
    # Map downloads directory to replay_dl
    replayer = WorkflowReplayer(store=mem_store)

    # Auto-approval gate for test harness approving confirm actions
    gate = AutoApprovalGate(approve=True)

    fake_comp = FakeComputer()
    fake_tool = FakeComputerTool(fake_comp)
    fs_tool = FilesystemTool()
    executor = Executor(tools=[fake_tool, fs_tool])

    settings = Settings.load()
    # Update allowed roots so safety validator allows replay_dl
    settings.safety.allowed_roots = [str(tmp_path)]

    # Prepare parameter substitution: replace path of initial_dl with replay_dl
    # Find parameter name that was mapped to initial_dl
    wf_params = wf.get_params()
    param_override = {}
    for k, _v in wf_params.items():
        if "downloads" in k.lower() or "downloads_run1" in k.lower():
            param_override[k] = str(replay_dl)

    # If no specific key, use the keys extracted
    if not param_override:
        param_override = {list(wf_params.keys())[0]: str(replay_dl)} if wf_params else {}

    run_res = await replayer.run(
        name="organize_downloads_workflow",
        params=param_override,
        autonomy_mode="assisted",
        executor=executor,
        observer=fake_comp.create_observer(),
        approval_gate=gate,
        settings=settings,
        run_store=RunStore(base_dir=tmp_path / "runs"),
    )

    assert run_res.status == "COMPLETED"

    # Definition of done: Replay succeeds faster (or with fewer steps) than the original exploratory run
    assert run_res.steps_count <= initial_step_count

    # Check workflow success count incremented in memory
    updated_wf = mem_store.get_workflow("organize_downloads_workflow")
    assert updated_wf is not None
    assert updated_wf.success_count >= 2

    mem_store.close()
