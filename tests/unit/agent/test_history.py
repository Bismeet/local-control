"""Unit tests for history condensation and token budgeting."""

from datetime import UTC, datetime

from local_control.agent.history import HistoryCondenser, estimate_tokens
from local_control.core.actions import ClickAction, DoneAction
from local_control.core.types import (
    ActionResult,
    Assessment,
    PlannerResponse,
    StepRecord,
    Verdict,
)


def _make_step(step_idx: int, success: bool = True) -> StepRecord:
    return StepRecord(
        step_index=step_idx,
        observation_ref=f"obs-{step_idx}.png",
        planner_response=PlannerResponse(
            assessment=Assessment(
                screen_summary=f"Screen at step {step_idx}",
                previous_action_outcome="success" if success else "failure",
                evidence=f"Evidence for step {step_idx}",
            ),
            action=ClickAction(
                x=100 + step_idx,
                y=200 + step_idx,
                target_description=f"Button {step_idx}",
                expected_outcome=f"Outcome {step_idx}",
            )
            if step_idx < 39
            else DoneAction(
                summary="Completed all tasks",
                verification_notes="Target verified",
                target_description="Done marker",
                expected_outcome="Run complete",
            ),
            confidence=0.9,
            rationale=f"Rationale {step_idx}",
        ),
        verdict=Verdict(
            decision="allow",
            tier="SAFE",
            category="input",
            human_summary=f"Click {step_idx}",
        ),
        result=ActionResult(
            action_type="click" if step_idx < 39 else "done",
            success=success,
            started_at=datetime.now(UTC),
            duration_ms=45,
        ),
    )


def test_history_condenser_empty() -> None:
    condenser = HistoryCondenser()
    assert condenser.condense([]) == []


def test_history_condenser_few_steps() -> None:
    condenser = HistoryCondenser(full_steps_count=6)
    steps = [_make_step(0), _make_step(1)]
    lines = condenser.condense(steps)
    text = "\n".join(lines)

    assert "# Execution History" in text
    assert "## Recent Steps (Detailed)" in text
    assert "## Earlier Steps (Summary)" not in text
    assert "Step 0" in text
    assert "Step 1" in text


def test_history_condenser_older_and_recent() -> None:
    condenser = HistoryCondenser(full_steps_count=3)
    steps = [_make_step(i) for i in range(7)]
    lines = condenser.condense(steps)
    text = "\n".join(lines)

    assert "## Earlier Steps (Summary)" in text
    assert "## Recent Steps (Detailed)" in text
    # Earlier steps (0 to 3) summarized
    assert "- Step 0: proposed `click` -> SUCCESS" in text
    assert "- Step 3: proposed `click` -> SUCCESS" in text
    # Recent steps (4 to 6) detailed
    assert "### Step 4" in text
    assert "### Step 6" in text


def test_history_condenser_token_budget_across_40_steps() -> None:
    condenser = HistoryCondenser(full_steps_count=6, max_history_tokens=1500)
    steps = [_make_step(i) for i in range(40)]
    lines = condenser.condense(steps)
    text = "\n".join(lines)

    tokens = estimate_tokens(text)
    assert tokens <= 1500
