"""Recovery policy and escalation ladder for local-control agent."""

from __future__ import annotations

import structlog

from local_control.core.types import RecoveryDecision, VerificationResult

logger = structlog.get_logger(__name__)


class RecoveryPolicy:
    """Escalation ladder: continue -> retry_hint -> replan -> ask_user -> abort."""

    def __init__(
        self,
        max_retries_per_step: int = 2,
        low_confidence_threshold: float = 0.3,
        max_failures_after_ask: int = 1,
    ) -> None:
        self.max_retries_per_step = max_retries_per_step
        self.low_confidence_threshold = low_confidence_threshold
        self.max_failures_after_ask = max_failures_after_ask

        # Failure counts per plan step
        self.step_failures: dict[int, int] = {}
        self.step_replanned: dict[int, bool] = {}
        self.step_asked: dict[int, bool] = {}

        self.consecutive_low_confidence: int = 0
        self.failures_after_ask: int = 0

    def reset(self) -> None:
        """Reset state tracking for a new run."""
        self.step_failures.clear()
        self.step_replanned.clear()
        self.step_asked.clear()
        self.consecutive_low_confidence = 0
        self.failures_after_ask = 0

    def record_proposal_confidence(self, confidence: float) -> None:
        """Track planner confidence to trigger ask_user when low twice consecutively."""
        if confidence < self.low_confidence_threshold:
            self.consecutive_low_confidence += 1
        else:
            self.consecutive_low_confidence = 0

    def decide(
        self,
        verification: VerificationResult,
        step_index: int = 0,
        is_stuck: bool = False,
        stuck_reason: str = "",
        blocked_action: bool = False,
        user_stopped: bool = False,
    ) -> RecoveryDecision:
        """Apply escalation ladder based on verification result and execution context."""
        # 1. Check user stop request
        if user_stopped:
            return RecoveryDecision(kind="abort", hint="User requested stop")

        # 2. Check blocked safety violation
        if blocked_action:
            return RecoveryDecision(
                kind="ask_user",
                hint="Action was blocked by safety policy. Human intervention required.",
            )

        # 3. Check low confidence escalation (2 consecutive proposals < threshold)
        if self.consecutive_low_confidence >= 2:
            return RecoveryDecision(
                kind="ask_user",
                hint=(
                    f"Model confidence has been below {self.low_confidence_threshold:.2f} "
                    f"for {self.consecutive_low_confidence} consecutive proposals. Asking user for guidance."
                ),
            )

        # 4. Check stuck loop escalation
        if is_stuck:
            if not self.step_replanned.get(step_index, False):
                self.step_replanned[step_index] = True
                return RecoveryDecision(
                    kind="replan",
                    hint=f"Stuck loop detected ({stuck_reason}). Replan required with incremented revision.",
                )
            if not self.step_asked.get(step_index, False):
                self.step_asked[step_index] = True
                return RecoveryDecision(
                    kind="ask_user",
                    hint=f"Agent remains stuck after replan ({stuck_reason}). User assistance required.",
                )
            return RecoveryDecision(
                kind="abort",
                hint=f"Cannot break stuck loop ({stuck_reason}). Aborting run.",
            )

        # 5. Successful or progressing verification -> CONTINUE
        if verification.outcome in ("success", "unknown_progress", "not_applicable"):
            if verification.outcome == "success":
                # Reset step failure counts upon verified success
                self.step_failures[step_index] = 0
                self.step_replanned[step_index] = False
                self.step_asked[step_index] = False
                self.failures_after_ask = 0
            return RecoveryDecision(kind="continue")

        # 6. Verification FAILURE -> Escalation Ladder
        # Ladder order: retry_hint (up to max_retries) -> replan -> ask_user -> abort
        self.step_failures[step_index] = self.step_failures.get(step_index, 0) + 1
        current_fails = self.step_failures[step_index]

        # Check if already asked user on this step
        if self.step_asked.get(step_index, False):
            self.failures_after_ask += 1
            if self.failures_after_ask >= self.max_failures_after_ask:
                return RecoveryDecision(
                    kind="abort",
                    hint=(
                        f"Repeated failure on step {step_index} after user assistance "
                        f"({verification.evidence}). Aborting."
                    ),
                )

        # Retry with hint (attempt <= max_retries_per_step)
        if current_fails <= self.max_retries_per_step:
            return RecoveryDecision(
                kind="retry_hint",
                hint=(
                    f"Action failed ({verification.evidence}). "
                    f"Retry attempt {current_fails}/{self.max_retries_per_step}. "
                    "Adjust coordinates or try an alternative interaction."
                ),
            )

        # Replan (after max retries failed)
        if not self.step_replanned.get(step_index, False):
            self.step_replanned[step_index] = True
            return RecoveryDecision(
                kind="replan",
                hint=(
                    f"Step {step_index} failed {current_fails} times consecutively ({verification.evidence}). "
                    "Replan required: emit a revised plan with revision incremented."
                ),
            )

        # Ask user (after replan failed on this step)
        if not self.step_asked.get(step_index, False):
            self.step_asked[step_index] = True
            return RecoveryDecision(
                kind="ask_user",
                hint=(
                    f"Step {step_index} failed again after replanning ({verification.evidence}). "
                    "User intervention or clarification required."
                ),
            )

        # Abort
        return RecoveryDecision(
            kind="abort",
            hint=f"Exhausted recovery ladder on step {step_index} ({verification.evidence}). Aborting.",
        )

    def reset_step(self, step_index: int) -> None:
        """Reset state tracking for a specific step."""
        self.step_failures[step_index] = 0
        self.step_replanned[step_index] = False
        self.step_asked[step_index] = False
