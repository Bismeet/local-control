"""Unit tests for Budget monitoring."""

from datetime import UTC, datetime, timedelta

import pytest

from local_control.agent.budget import Budget
from local_control.config.settings import Settings
from local_control.core.actions import ClickAction
from local_control.core.types import (
    ActionResult,
    Assessment,
    PlannerResponse,
    StepRecord,
    TaskState,
    Verdict,
)


def create_step_record(index: int, success: bool, outcome: str = "success") -> StepRecord:
    return StepRecord(
        step_index=index,
        observation_ref="",
        planner_response=PlannerResponse(
            assessment=Assessment(
                screen_summary="",
                previous_action_outcome=outcome,  # type: ignore[arg-type]
                evidence="",
            ),
            action=ClickAction(
                x=0,
                y=0,
                target_description="btn",
                expected_outcome="click",
            ),
            confidence=1.0,
            rationale="",
        ),
        verdict=Verdict(
            decision="allow",
            tier="SAFE",
            category="test",
            human_summary="",
        ),
        result=ActionResult(
            action_type="click",
            success=success,
            started_at=datetime.now(UTC),
            duration_ms=10,
        ),
    )


@pytest.mark.unit
def test_budget_within_limits() -> None:
    settings = Settings.load()
    budget = Budget(settings=settings)
    state = TaskState(run_id="r1", goal="g", autonomy_mode="step", current_step=5)

    status = budget.check(state)
    assert status.ok
    assert not status.warning


@pytest.mark.unit
def test_budget_step_warning_and_exceeded() -> None:
    settings = Settings.load()
    settings.budget.max_steps = 10
    budget = Budget(settings=settings)

    # 8 steps = 80% warning
    state = TaskState(run_id="r1", goal="g", autonomy_mode="step", current_step=8)
    status = budget.check(state)
    assert status.ok
    assert status.warning
    assert "80% reached" in (status.warning_message or "")

    # 10 steps = exceeded
    state.current_step = 10
    status2 = budget.check(state)
    assert not status2.ok
    assert "Step budget exceeded" in (status2.reason or "")


@pytest.mark.unit
def test_budget_time_exceeded() -> None:
    settings = Settings.load()
    settings.budget.max_time_s = 10.0
    start = datetime.now(UTC) - timedelta(seconds=15)
    budget = Budget(settings=settings, start_time=start)

    state = TaskState(run_id="r1", goal="g", autonomy_mode="step", current_step=1)
    status = budget.check(state)
    assert not status.ok
    assert "Time budget exceeded" in (status.reason or "")


@pytest.mark.unit
def test_budget_consecutive_failures() -> None:
    settings = Settings.load()
    settings.budget.consecutive_failures_limit = 3
    budget = Budget(settings=settings)

    state = TaskState(run_id="r1", goal="g", autonomy_mode="step", current_step=3)
    state.steps = [
        create_step_record(0, success=False, outcome="failure"),
        create_step_record(1, success=False, outcome="failure"),
        create_step_record(2, success=False, outcome="failure"),
    ]

    status = budget.check(state)
    assert not status.ok
    assert "Consecutive failure limit reached" in (status.reason or "")
