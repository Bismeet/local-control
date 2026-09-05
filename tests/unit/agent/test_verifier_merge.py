"""Unit tests for Verifier merge rules according to ARCHITECTURE Section 13."""

from datetime import UTC, datetime

import pytest

from local_control.agent.verifier import (
    Verifier,
    verify_done_proposal,
)
from local_control.core.actions import (
    ClickAction,
    DoneAction,
    FocusWindowAction,
    Point,
    WaitAction,
)
from local_control.core.types import (
    ActionResult,
    Assessment,
    ErrorInfo,
    ImageRef,
    Observation,
    Plan,
    PlannerResponse,
    PlanStep,
    Rect,
    ScreenGeometry,
    WindowInfo,
)


def make_dummy_obs(
    phash: str = "0000000000000000",
    fg_title: str = "Test Window",
    fg_handle: int = 1234,
) -> Observation:
    return Observation(
        step_index=0,
        captured_at=datetime.now(UTC),
        screen=ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0),
        image=ImageRef(
            path_original="",
            path_model="",
            model_width=1280,
            model_height=720,
            phash=phash,
        ),
        foreground=WindowInfo(
            handle=fg_handle,
            title=fg_title,
            process_name="app.exe",
            pid=999,
            bbox=Rect(x=0, y=0, width=500, height=500),
            is_foreground=True,
            is_minimized=False,
        ),
        cursor=Point(x=0, y=0),
    )


@pytest.mark.unit
def test_rule_1_tool_failure_yields_failure() -> None:
    verifier = Verifier(phash_threshold=6)
    action = ClickAction(x=100, y=100, target_description="btn", expected_outcome="opened")
    res = ActionResult(
        action_type="click",
        success=False,
        started_at=datetime.now(UTC),
        duration_ms=50,
        error=ErrorInfo(code="INPUT_ERROR", message="Mouse hardware failed"),
    )
    obs1 = make_dummy_obs()
    obs2 = make_dummy_obs()
    assessment = Assessment(
        screen_summary="Nothing",
        previous_action_outcome="success",  # Model assessment says success, but tool failed
        evidence="Screen looks good",
    )

    result = verifier.verify(action, res, obs1, obs2, assessment)
    assert result.outcome == "failure"
    assert "deterministic" in result.source
    assert "Mouse hardware failed" in result.evidence


@pytest.mark.unit
def test_rule_2_deterministic_postcondition_non_gui_success() -> None:
    verifier = Verifier(phash_threshold=6)
    # wait is a non-GUI action
    action = WaitAction(seconds=1.0, target_description="Wait 1s", expected_outcome="Waited")
    res = ActionResult(
        action_type="wait",
        success=True,
        started_at=datetime.now(UTC),
        duration_ms=1000,
        data={"waited_seconds": 1.0},
    )
    obs1 = make_dummy_obs()
    obs2 = make_dummy_obs()

    result = verifier.verify(action, res, obs1, obs2, assessment=None)
    assert result.outcome == "success"
    assert "deterministic" in result.source


@pytest.mark.unit
def test_rule_2_focus_window_postcondition_fails_when_handle_mismatch() -> None:
    verifier = Verifier(phash_threshold=6)
    action = FocusWindowAction(
        handle=9999, target_description="Target app", expected_outcome="Focused"
    )  # Target is 9999
    res = ActionResult(
        action_type="focus_window",
        success=True,
        started_at=datetime.now(UTC),
        duration_ms=10,
    )
    obs1 = make_dummy_obs()
    obs2 = make_dummy_obs(fg_handle=1234)  # Foreground is still 1234

    result = verifier.verify(action, res, obs1, obs2, assessment=None)
    assert result.outcome == "failure"
    assert "deterministic" in result.source
    assert "does not match target" in result.evidence


@pytest.mark.unit
def test_rule_3_gui_assessment_success() -> None:
    verifier = Verifier(phash_threshold=6)
    action = ClickAction(x=50, y=50, target_description="Menu", expected_outcome="Menu opens")
    res = ActionResult(
        action_type="click", success=True, started_at=datetime.now(UTC), duration_ms=20
    )
    obs1 = make_dummy_obs()
    obs2 = make_dummy_obs()
    assessment = Assessment(
        screen_summary="Menu is visible",
        previous_action_outcome="success",
        evidence="Dropdown menu with 5 items appeared",
    )

    result = verifier.verify(action, res, obs1, obs2, assessment)
    assert result.outcome == "success"
    assert "assessment" in result.source
    assert "Dropdown menu" in result.evidence


@pytest.mark.unit
def test_rule_3_gui_assessment_failure() -> None:
    verifier = Verifier(phash_threshold=6)
    action = ClickAction(x=50, y=50, target_description="Submit", expected_outcome="Form submits")
    res = ActionResult(
        action_type="click", success=True, started_at=datetime.now(UTC), duration_ms=20
    )
    obs1 = make_dummy_obs()
    obs2 = make_dummy_obs()
    assessment = Assessment(
        screen_summary="Error dialog visible",
        previous_action_outcome="failure",
        evidence="Validation error prompt appeared",
    )

    result = verifier.verify(action, res, obs1, obs2, assessment)
    assert result.outcome == "failure"
    assert "assessment" in result.source
    assert "Validation error" in result.evidence


@pytest.mark.unit
def test_rule_3_gui_assessment_unknown_with_screen_change() -> None:
    verifier = Verifier(phash_threshold=6)
    action = ClickAction(x=50, y=50, target_description="Refresh", expected_outcome="Data reloads")
    res = ActionResult(
        action_type="click", success=True, started_at=datetime.now(UTC), duration_ms=20
    )
    # Different hashes (hamming distance > 6)
    obs1 = make_dummy_obs(phash="0000000000000000")
    obs2 = make_dummy_obs(phash="ffffffffffffffff")
    assessment = Assessment(
        screen_summary="Page loaded",
        previous_action_outcome="unknown",
        evidence="Cannot tell if refreshed",
    )

    result = verifier.verify(action, res, obs1, obs2, assessment)
    assert result.outcome == "unknown_progress"
    assert "screen_signal" in result.source


@pytest.mark.unit
def test_rule_3_gui_assessment_unknown_screen_unchanged_fails_no_visible_change() -> None:
    verifier = Verifier(phash_threshold=6)
    action = ClickAction(x=50, y=50, target_description="Btn", expected_outcome="Dialog pops up")
    res = ActionResult(
        action_type="click", success=True, started_at=datetime.now(UTC), duration_ms=20
    )
    # Identical hashes
    obs1 = make_dummy_obs(phash="0000000000000000")
    obs2 = make_dummy_obs(phash="0000000000000000")
    assessment = Assessment(
        screen_summary="No change",
        previous_action_outcome="unknown",
        evidence="Uncertain",
    )

    result = verifier.verify(action, res, obs1, obs2, assessment)
    assert result.outcome == "failure"
    assert "no_visible_change" in result.evidence


@pytest.mark.unit
def test_verify_done_proposal_validations() -> None:
    # 1. Assessment previous_action_outcome is failure -> REJECT
    plan_resp1 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Failed",
            previous_action_outcome="failure",
            evidence="Button was disabled",
        ),
        action=DoneAction(
            summary="Finished",
            verification_notes="Done",
            target_description="Done",
            expected_outcome="Done",
        ),
        confidence=0.95,
        rationale="Done",
    )
    ok1, reason1 = verify_done_proposal(plan_resp1)
    assert not ok1
    assert "previous action outcome was failure" in reason1

    # 2. Confidence below 0.60 -> REJECT
    plan_resp2 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Maybe done",
            previous_action_outcome="success",
            evidence="Looks ok",
        ),
        action=DoneAction(
            summary="Finished",
            verification_notes="Done",
            target_description="Done",
            expected_outcome="Done",
        ),
        confidence=0.55,
        rationale="Done",
    )
    ok2, reason2 = verify_done_proposal(plan_resp2)
    assert not ok2
    assert "confidence 0.55 is below required threshold" in reason2

    # 3. Plan has active or pending steps -> REJECT
    plan_with_pending = Plan(
        steps=[
            PlanStep(index=0, description="Step 1", status="done"),
            PlanStep(index=1, description="Step 2", status="pending"),
        ],
        current_index=1,
    )
    plan_resp3 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Good",
            previous_action_outcome="success",
            evidence="Done",
        ),
        action=DoneAction(
            summary="Finished",
            verification_notes="Done",
            target_description="Done",
            expected_outcome="Done",
        ),
        confidence=0.9,
        rationale="Done",
    )
    ok3, reason3 = verify_done_proposal(plan_resp3, plan=plan_with_pending)
    assert not ok3
    assert "incomplete steps" in reason3

    # 4. Valid done -> ACCEPT
    plan_complete = Plan(
        steps=[
            PlanStep(index=0, description="Step 1", status="done"),
            PlanStep(index=1, description="Step 2", status="skipped"),
        ],
        current_index=1,
    )
    ok4, reason4 = verify_done_proposal(plan_resp3, plan=plan_complete)
    assert ok4
    assert reason4 == ""
