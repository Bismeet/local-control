"""Unit tests for SafetyValidator autonomy modes, per-run grants, and rate limits."""

from datetime import UTC, datetime

from local_control.core.actions import ClickAction, WaitAction
from local_control.core.types import (
    ImageRef,
    Observation,
    Point,
    RunPermissions,
    ScreenGeometry,
)
from local_control.safety.validator import SafetyValidator


def _make_obs() -> Observation:
    return Observation(
        step_index=0,
        captured_at=datetime.now(UTC),
        screen=ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0),
        image=ImageRef(
            path_original="",
            path_model="",
            model_width=960,
            model_height=540,
            phash="0" * 16,
        ),
        screen_state="normal",
        foreground=None,
        windows=[],
        cursor=Point(x=100, y=100),
    )


def test_autonomy_modes_safe_action() -> None:
    validator = SafetyValidator()
    obs = _make_obs()
    safe_action = WaitAction(seconds=1.0, target_description="Wait", expected_outcome="Waited")

    # 1. Step mode requires confirmation for all actions including SAFE
    v_step = validator.validate(safe_action, obs, mode="step")
    assert v_step.tier == "SAFE"
    assert v_step.decision == "needs_confirmation"

    # 2. Assisted mode automatically allows SAFE actions
    v_assisted = validator.validate(safe_action, obs, mode="assisted")
    assert v_assisted.tier == "SAFE"
    assert v_assisted.decision == "allow"

    # 3. Trusted mode automatically allows SAFE actions
    v_trusted = validator.validate(safe_action, obs, mode="trusted")
    assert v_trusted.tier == "SAFE"
    assert v_trusted.decision == "allow"


def test_blocked_actions_never_allowed_in_any_mode() -> None:
    validator = SafetyValidator()
    obs = _make_obs()
    # Out-of-bounds click (B-01)
    blocked_action = ClickAction(
        x=-50,
        y=100,
        target_description="Out of bounds",
        expected_outcome="Fail",
    )

    perms = RunPermissions(granted_categories={"B-01", "all"})

    for mode in ("step", "assisted", "trusted"):
        verdict = validator.validate(blocked_action, obs, permissions=perms, mode=mode)
        assert verdict.tier == "BLOCKED"
        assert verdict.decision == "blocked", (
            f"BLOCKED action must never be allowed in mode '{mode}'"
        )


def test_trusted_mode_per_run_grants() -> None:
    validator = SafetyValidator()
    obs = _make_obs()

    # Move action in allowed_root is C-01 (grantable_for_run = True)
    from local_control.core.actions import FsMoveAction

    confirm_action = FsMoveAction(
        src="~/Downloads/a.txt",
        dst="~/Downloads/b.txt",
        target_description="Move file",
        expected_outcome="Moved",
    )

    # Without grant: needs confirmation
    perms_empty = RunPermissions()
    v_no_grant = validator.validate(confirm_action, obs, permissions=perms_empty, mode="trusted")
    assert v_no_grant.tier == "CONFIRM"
    assert v_no_grant.decision == "needs_confirmation"

    # With grant for C-01: auto-allowed in trusted mode
    perms_granted = RunPermissions(granted_categories={"C-01"})
    v_granted = validator.validate(confirm_action, obs, permissions=perms_granted, mode="trusted")
    assert v_granted.tier == "CONFIRM"
    assert v_granted.decision == "allow"

    # But in assisted mode: grant does NOT bypass confirmation
    v_assisted = validator.validate(confirm_action, obs, permissions=perms_granted, mode="assisted")
    assert v_assisted.decision == "needs_confirmation"


def test_low_confidence_c14_elevation() -> None:
    validator = SafetyValidator()
    obs = _make_obs()
    safe_action = WaitAction(seconds=1.0, target_description="Wait", expected_outcome="Waited")

    # High confidence -> SAFE, allow in assisted
    v_high = validator.validate(safe_action, obs, mode="assisted", confidence=0.85)
    assert v_high.tier == "SAFE"
    assert v_high.decision == "allow"

    # Low confidence (< 0.4) -> elevated to CONFIRM (C-14), needs_confirmation
    v_low = validator.validate(safe_action, obs, mode="assisted", confidence=0.35)
    assert v_low.tier == "CONFIRM"
    assert v_low.category == "C-14"
    assert v_low.decision == "needs_confirmation"


def test_rate_limiting_c15() -> None:
    validator = SafetyValidator(max_actions_per_minute=3)
    obs = _make_obs()
    safe_action = WaitAction(seconds=0.1, target_description="Wait", expected_outcome="Waited")

    # First 3 actions are fine
    v1 = validator.validate(safe_action, obs, mode="assisted")
    assert v1.tier == "SAFE"
    v2 = validator.validate(safe_action, obs, mode="assisted")
    assert v2.tier == "SAFE"
    v3 = validator.validate(safe_action, obs, mode="assisted")
    assert v3.tier == "SAFE"

    # 4th action exceeds rate limit -> elevated to C-15 CONFIRM
    v4 = validator.validate(safe_action, obs, mode="assisted")
    assert v4.tier == "CONFIRM"
    assert v4.category == "C-15"
    assert v4.decision == "needs_confirmation"
