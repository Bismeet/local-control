"""Unit tests for RunStore persistence and reload."""

from pathlib import Path

import pytest

from local_control.core.errors import RunStoreError
from local_control.core.events import Event
from local_control.core.run_store import RunStore
from local_control.core.types import Plan, PlanStep, TaskState


@pytest.mark.unit
def test_run_store_directory_layout(tmp_path: Path) -> None:
    """Verify RunStore creates the complete required directory layout."""
    store = RunStore(base_dir=tmp_path)
    run_id = "run-test-layout"
    run_dir = store.create_run(
        run_id=run_id,
        goal="Test directory layout",
        mode="assisted",
        settings_snapshot={"safety": {"autonomy_mode": "assisted"}},
    )

    assert run_dir.exists()
    assert (run_dir / "run.json").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "audit.jsonl").exists()
    assert (run_dir / "screenshots").is_dir()
    assert (run_dir / "steps").is_dir()


@pytest.mark.unit
def test_run_store_events_append_and_reload(tmp_path: Path) -> None:
    """Verify events written to events.jsonl reload as typed Event instances."""
    store = RunStore(base_dir=tmp_path)
    run_id = "run-test-events"
    store.create_run(run_id=run_id, goal="Test events", mode="step")

    e1 = Event(run_id=run_id, step_index=0, type="run.started", payload={"goal": "Test events"})
    e2 = Event(run_id=run_id, step_index=1, type="action.executed", payload={"action": "click"})

    store.append_event(run_id, e1)
    store.append_event(run_id, e2)

    meta, state, events = store.load_run(run_id)

    assert meta["run_id"] == run_id
    assert meta["goal"] == "Test events"
    assert len(events) == 2
    assert events[0].event_id == e1.event_id
    assert events[0].type == "run.started"
    assert events[1].event_id == e2.event_id
    assert events[1].type == "action.executed"


@pytest.mark.unit
def test_run_store_state_write_and_reload(tmp_path: Path) -> None:
    """Verify atomic state writing and deserialization."""
    store = RunStore(base_dir=tmp_path)
    run_id = "run-test-state"
    store.create_run(run_id=run_id, goal="Test state", mode="trusted")

    task_state = TaskState(
        run_id=run_id,
        goal="Test state",
        autonomy_mode="trusted",
        status="RUNNING",
        current_step=1,
        feedback_queue=["Previous action succeeded"],
        plan=Plan(
            steps=[PlanStep(index=0, description="Step 1", status="done")],
            current_index=0,
            revision=1,
        ),
    )

    store.write_state(run_id, task_state)

    meta, loaded_state, _ = store.load_run(run_id)
    assert loaded_state is not None
    assert loaded_state.run_id == run_id
    assert loaded_state.status == "RUNNING"
    assert loaded_state.current_step == 1
    assert loaded_state.feedback_queue == ["Previous action succeeded"]
    assert loaded_state.plan is not None
    assert loaded_state.plan.revision == 1


@pytest.mark.unit
def test_run_store_summary_writing(tmp_path: Path) -> None:
    """Verify summary.md is written properly."""
    store = RunStore(base_dir=tmp_path)
    run_id = "run-test-summary"
    run_dir = store.create_run(run_id=run_id, goal="Test summary", mode="step")

    store.write_summary(run_id, "# Run Summary\nCompleted 5 actions.")
    summary_file = run_dir / "summary.md"
    assert summary_file.exists()
    assert "Completed 5 actions." in summary_file.read_text(encoding="utf-8")


@pytest.mark.unit
def test_run_store_load_nonexistent(tmp_path: Path) -> None:
    """Verify loading a non-existent run raises RunStoreError."""
    store = RunStore(base_dir=tmp_path)
    with pytest.raises(RunStoreError):
        store.load_run("nonexistent-run-id")
