"""Unit tests for workflow recording, sanitization, and replay preparation."""

from datetime import UTC, datetime
from pathlib import Path

from local_control.core.actions import (
    DoneAction,
    FsListAction,
    FsMkdirAction,
    ShellRunAction,
)
from local_control.core.types import (
    ActionResult,
    ApprovalDecision,
    Assessment,
    PlannerResponse,
    StepRecord,
    Verdict,
)
from local_control.memory.sanitizer import Sanitizer
from local_control.memory.store import MemoryStore
from local_control.memory.workflows import WorkflowRecorder, WorkflowReplayer


def test_sanitizer_secrets_and_paths() -> None:
    sanitizer = Sanitizer(user_home="C:\\Users\\testuser")

    # 1. Detect and redact API keys
    key_text = "Use key sk-1234567890abcdef12345678 and token ghp_abcdefghijklmnopqrstuvwxyz1234"
    assert sanitizer.contains_secrets(key_text)
    sanitized_key = sanitizer.sanitize_secrets(key_text)
    assert "sk-" not in sanitized_key
    assert "ghp_" not in sanitized_key
    assert "[REDACTED_KEY]" in sanitized_key

    # 2. Detect and redact passwords
    pw_text = 'connect with password="SuperSecretP@ssw0rd!" or -password mysecret'
    sanitized_pw = sanitizer.sanitize_secrets(pw_text)
    assert "SuperSecretP@ssw0rd!" not in sanitized_pw
    assert "mysecret" not in sanitized_pw
    assert "[REDACTED]" in sanitized_pw

    # 3. Path parameterization
    params: dict[str, str] = {}
    path_text = "Organize files in C:\\Users\\testuser\\Downloads to subfolders"
    sanitized_path = sanitizer.parameterize_paths(path_text, params)
    assert "C:\\Users\\testuser\\Downloads" not in sanitized_path
    assert "{{downloads_dir}}" in sanitized_path
    assert params["downloads_dir"] == "C:\\Users\\testuser\\Downloads"


def test_memory_never_contains_secrets(tmp_path: Path) -> None:
    """Acceptance criteria test: memory never stores raw API keys or passwords."""
    db_file = tmp_path / "memory_safety.db"
    store = MemoryStore(db_path=db_file)
    recorder = WorkflowRecorder()

    # Step with an action attempting to use a secret or key
    action_with_secret = ShellRunAction(
        command="curl -H 'Authorization: Bearer secret_token_abc123456789012345678' https://example.com",
        target_description="Fetch data with secret_token_abc123456789012345678",
        expected_outcome="Success",
    )
    plan_resp = PlannerResponse(
        assessment=Assessment(
            screen_summary="Running secret command",
            previous_action_outcome="not_applicable",
            evidence="Initial step",
        ),
        action=action_with_secret,
        confidence=0.9,
        rationale="Call endpoint",
    )
    step = StepRecord(
        step_index=0,
        observation_ref="",
        planner_response=plan_resp,
        verdict=Verdict(decision="allow", tier="SAFE", category="terminal", human_summary="Run"),
        approval=ApprovalDecision(decision="approved"),
        result=ActionResult(
            action_type="shell_run",
            success=True,
            started_at=datetime.now(UTC),
            duration_ms=10,
        ),
    )

    recorder.record_from_run(
        name="fetch_secret",
        goal="Run command with secret_token_abc123456789012345678",
        steps=[step],
        store=store,
    )

    # Verify memory database content
    wf_from_db = store.get_workflow("fetch_secret")
    assert wf_from_db is not None
    assert "secret_token_abc123456789012345678" not in wf_from_db.goal_template
    assert "secret_token_abc123456789012345678" not in wf_from_db.steps_json
    assert "[REDACTED" in wf_from_db.steps_json

    store.close()


def test_workflow_recorder_and_replayer_roundtrip(tmp_path: Path) -> None:
    db_file = tmp_path / "workflows_roundtrip.db"
    store = MemoryStore(db_path=db_file)
    sanitizer = Sanitizer(user_home="C:\\Users\\alice")
    recorder = WorkflowRecorder(sanitizer=sanitizer)
    replayer = WorkflowReplayer(store=store)

    # Simulate steps from a successful run
    dl_dir = "C:\\Users\\alice\\Downloads"
    steps = [
        StepRecord(
            step_index=0,
            observation_ref="",
            planner_response=PlannerResponse(
                assessment=Assessment(
                    screen_summary="List downloads",
                    previous_action_outcome="not_applicable",
                    evidence="Start",
                ),
                action=FsListAction(
                    path=dl_dir,
                    target_description="List files in Downloads",
                    expected_outcome="Listed",
                ),
                confidence=0.95,
                rationale="List files",
            ),
            verdict=Verdict(
                decision="allow", tier="SAFE", category="fs_read", human_summary="List"
            ),
            approval=ApprovalDecision(decision="approved"),
            result=ActionResult(
                action_type="fs_list",
                success=True,
                started_at=datetime.now(UTC),
                duration_ms=5,
            ),
        ),
        StepRecord(
            step_index=1,
            observation_ref="",
            planner_response=PlannerResponse(
                assessment=Assessment(
                    screen_summary="Make PDF folder",
                    previous_action_outcome="success",
                    evidence="Folder needed",
                ),
                action=FsMkdirAction(
                    path=f"{dl_dir}\\PDFs",
                    target_description="Create PDFs directory",
                    expected_outcome="Folder created",
                ),
                confidence=0.95,
                rationale="Create folder",
            ),
            verdict=Verdict(
                decision="allow", tier="SAFE", category="fs_write", human_summary="Mkdir"
            ),
            approval=ApprovalDecision(decision="approved"),
            result=ActionResult(
                action_type="fs_mkdir",
                success=True,
                started_at=datetime.now(UTC),
                duration_ms=5,
            ),
        ),
        StepRecord(
            step_index=2,
            observation_ref="",
            planner_response=PlannerResponse(
                assessment=Assessment(
                    screen_summary="Done organizing",
                    previous_action_outcome="success",
                    evidence="Completed",
                ),
                action=DoneAction(
                    summary="Organized downloads successfully",
                    verification_notes="All items categorized",
                    target_description="Finished",
                    expected_outcome="Done",
                ),
                confidence=1.0,
                rationale="Complete",
            ),
            verdict=Verdict(
                decision="allow", tier="SAFE", category="observation", human_summary="Done"
            ),
            approval=ApprovalDecision(decision="approved"),
            result=ActionResult(
                action_type="done",
                success=True,
                started_at=datetime.now(UTC),
                duration_ms=5,
            ),
        ),
    ]

    wf = recorder.record_from_run(
        name="organize_dl",
        goal=f"Organize {dl_dir} into categories",
        steps=steps,
        description="Categorize download items",
        store=store,
    )

    assert wf.name == "organize_dl"
    assert "{{downloads_dir}}" in wf.goal_template
    assert "alice" not in wf.goal_template

    # Replay with new parameters for a different user
    new_dl = "D:\\Users\\bob\\Downloads"
    wf_loaded, rendered_goal, actions, plan = replayer.prepare_replay(
        name="organize_dl",
        params={"downloads_dir": new_dl},
    )

    assert wf_loaded.name == "organize_dl"
    assert new_dl in rendered_goal
    assert len(actions) == 2
    assert isinstance(actions[0], FsListAction)
    assert actions[0].path == new_dl
    assert isinstance(actions[1], FsMkdirAction)
    assert actions[1].path == f"{new_dl}\\PDFs"

    assert len(plan.steps) == 2
    assert plan.current_index == 0

    store.close()
