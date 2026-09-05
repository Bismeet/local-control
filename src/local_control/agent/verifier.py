"""Verification layer: merge deterministic tool postconditions, screen signals, and model assessments."""

from __future__ import annotations

import structlog

from local_control.core.actions import Action, DoneAction, FocusWindowAction
from local_control.core.types import (
    ActionResult,
    Assessment,
    Observation,
    Plan,
    PlannerResponse,
    VerificationResult,
    VerificationSource,
)
from local_control.observation.image import hamming_distance

logger = structlog.get_logger(__name__)

NON_GUI_ACTIONS = {
    "fs_list",
    "fs_read",
    "fs_stat",
    "fs_mkdir",
    "fs_write",
    "fs_copy",
    "fs_move",
    "fs_delete",
    "shell_run",
    "focus_window",
    "close_window",
    "wait",
}


def check_deterministic_postcondition(
    action: Action | None,
    result: ActionResult | None,
    obs_before: Observation | None,
    obs_after: Observation | None,
) -> tuple[bool | None, str]:
    """Check deterministic postconditions from tools.

    Returns (passed, evidence) where passed is True/False/None (None = no postcondition checked).
    """
    if action is None or result is None:
        return None, ""

    # 1. Explicit tool postcondition reported in data
    if "postcondition_passed" in result.data:
        passed = bool(result.data["postcondition_passed"])
        evidence = result.data.get(
            "postcondition_evidence",
            "Tool deterministic postcondition passed"
            if passed
            else "Tool deterministic postcondition failed",
        )
        return passed, evidence

    # 2. focus_window postcondition: foreground window handle matches target
    if isinstance(action, FocusWindowAction) and obs_after and obs_after.foreground:
        if obs_after.foreground.handle == action.handle:
            return True, f"Foreground window handle matches target {action.handle}"
        return (
            False,
            f"Foreground window handle {obs_after.foreground.handle} does not match target {action.handle}",
        )

    # 3. shell_run postcondition: exit code 0
    if action.type == "shell_run" and "exit_code" in result.data:
        exit_code = result.data["exit_code"]
        if exit_code == 0:
            return True, "Shell command exited with code 0"
        return False, f"Shell command failed with exit code {exit_code}"

    # 4. wait postcondition: completed cleanly
    if action.type == "wait" and result.success:
        return True, f"Wait completed successfully for {getattr(action, 'seconds', 0)} seconds"

    return None, ""


class Verifier:
    """Merges execution postconditions, visual signals, and assessments into VerificationResult."""

    def __init__(self, phash_threshold: int = 6) -> None:
        self.phash_threshold = phash_threshold

    def verify(
        self,
        action: Action | None,
        result: ActionResult | None,
        obs_before: Observation | None,
        obs_after: Observation | None,
        assessment: Assessment | None,
    ) -> VerificationResult:
        """Apply ordered merge rules from ARCHITECTURE Section 13."""
        sources: list[VerificationSource] = []

        # Rule 1: Tool reported success=False -> failure (error is the evidence)
        if result and not result.success:
            sources.append("deterministic")
            err_msg = result.error.message if result.error else "Action execution failed"
            return VerificationResult(
                outcome="failure",
                source=sources,
                evidence=f"Tool execution failed: {err_msg}",
            )

        # Rule 2: Deterministic postconditions
        post_passed, post_evidence = check_deterministic_postcondition(
            action=action,
            result=result,
            obs_before=obs_before,
            obs_after=obs_after,
        )
        if post_passed is False:
            sources.append("deterministic")
            return VerificationResult(
                outcome="failure",
                source=sources,
                evidence=f"Deterministic postcondition failed: {post_evidence}",
            )

        if post_passed is True and action and action.type in NON_GUI_ACTIONS:
            sources.append("deterministic")
            return VerificationResult(
                outcome="success",
                source=sources,
                evidence=post_evidence or "Deterministic postcondition passed for non-GUI action",
            )

        # Compute screen signals
        screen_changed = False
        dist = 0
        if obs_before and obs_after and obs_before.image.phash and obs_after.image.phash:
            dist = hamming_distance(obs_before.image.phash, obs_after.image.phash)
            if dist > self.phash_threshold:
                screen_changed = True

        if (
            obs_before
            and obs_after
            and obs_before.foreground
            and obs_after.foreground
            and obs_before.foreground.title != obs_after.foreground.title
        ):
            screen_changed = True

        # Rule 3: GUI actions and assessment
        if assessment:
            outcome = assessment.previous_action_outcome
            if outcome == "success":
                sources.append("assessment")
                return VerificationResult(
                    outcome="success",
                    source=sources,
                    evidence=assessment.evidence or "Assessment verified action success",
                )

            if outcome == "failure":
                sources.append("assessment")
                return VerificationResult(
                    outcome="failure",
                    source=sources,
                    evidence=assessment.evidence or "Assessment indicated action failure",
                )

            if outcome == "unknown":
                sources.append("assessment")
                sources.append("screen_signal")
                if screen_changed:
                    chg_desc = (
                        f"phash distance {dist} > {self.phash_threshold}"
                        if dist > self.phash_threshold
                        else "foreground title changed"
                    )
                    return VerificationResult(
                        outcome="unknown_progress",
                        source=sources,
                        evidence=f"Screen changed ({chg_desc}) despite unknown assessment",
                    )

                # Screen unchanged: if expected_outcome implies change -> failure (no_visible_change)
                expected = getattr(action, "expected_outcome", "") if action else ""
                expected_lower = str(expected).lower()
                no_change_keywords = ["no change", "remain", "unchanged", "stay"]
                implies_change = bool(expected) and not any(
                    k in expected_lower for k in no_change_keywords
                )
                # GUI interactions like click, type_text, drag, press_keys inherently imply change
                if action and action.type in {"click", "drag", "type_text", "press_keys"}:
                    implies_change = True

                if implies_change:
                    return VerificationResult(
                        outcome="failure",
                        source=sources,
                        evidence=f"no_visible_change: Screen perceptual hash distance {dist} <= {self.phash_threshold}",
                    )

                return VerificationResult(
                    outcome="unknown_progress",
                    source=sources,
                    evidence=f"Screen unchanged ({dist} <= {self.phash_threshold}), but action did not mandate visual change",
                )

            if outcome == "not_applicable":
                sources.append("assessment")
                return VerificationResult(
                    outcome="not_applicable",
                    source=sources,
                    evidence=assessment.evidence or "Initial or non-applicable assessment",
                )

        # Fallback if no assessment available
        if post_passed is True:
            sources.append("deterministic")
            return VerificationResult(
                outcome="success",
                source=sources,
                evidence=post_evidence or "Postcondition succeeded",
            )

        if screen_changed:
            sources.append("screen_signal")
            return VerificationResult(
                outcome="unknown_progress",
                source=sources,
                evidence=f"Screen changed (distance {dist} > {self.phash_threshold})",
            )

        sources.append("deterministic")
        return VerificationResult(
            outcome="success" if (result and result.success) else "failure",
            source=sources,
            evidence="Default verification fallback",
        )


def verify_done_proposal(
    plan_resp: PlannerResponse,
    plan: Plan | None = None,
) -> tuple[bool, str]:
    """Verify that a proposed `done` action satisfies all required preconditions.

    Rules:
    1. assessment.previous_action_outcome != 'failure'
    2. confidence >= 0.6
    3. all plan steps must be 'done' or 'skipped'
    """
    if not isinstance(plan_resp.action, DoneAction):
        return True, ""

    # Check 1: Previous action assessment cannot be failure
    if plan_resp.assessment.previous_action_outcome == "failure":
        return (
            False,
            f"Goal check rejected: previous action outcome was failure ({plan_resp.assessment.evidence})",
        )

    # Check 2: Confidence threshold >= 0.6
    if plan_resp.confidence < 0.6:
        return (
            False,
            f"Goal check rejected: confidence {plan_resp.confidence:.2f} is below required threshold 0.60",
        )

    # Check 3: All plan steps done or skipped
    if plan and plan.steps:
        incomplete_steps = [s for s in plan.steps if s.status not in ("done", "skipped")]
        if incomplete_steps:
            descriptions = [
                f"Step {s.index}: {s.description} ({s.status})" for s in incomplete_steps
            ]
            return (
                False,
                f"Goal check rejected: plan has incomplete steps: {', '.join(descriptions)}",
            )

    return True, ""
