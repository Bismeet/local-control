"""Deterministic SafetyValidator evaluating actions against policy rules, autonomy modes, and rate limits."""

import time
from collections import deque

import structlog

from local_control.config.settings import Settings
from local_control.core.actions import Action
from local_control.core.types import (
    Observation,
    RunPermissions,
    Verdict,
    VerdictDecision,
)
from local_control.safety import policy

logger = structlog.get_logger(__name__)


class SafetyValidator:
    """Pure deterministic validation gate classifying actions into SAFE / CONFIRM / BLOCKED verdicts."""

    def __init__(
        self,
        settings: Settings | None = None,
        max_destructive_per_run: int = 50,
        max_actions_per_minute: int = 20,
    ) -> None:
        self.settings = settings or Settings.load()
        self.max_destructive_per_run = max_destructive_per_run
        self.max_actions_per_minute = max_actions_per_minute
        self._destructive_action_count: int = 0
        self._action_timestamps: deque[float] = deque()

    def reset_run(self) -> None:
        """Reset per-run counters."""
        self._destructive_action_count = 0
        self._action_timestamps.clear()

    def validate(
        self,
        action: Action,
        obs: Observation,
        permissions: RunPermissions | None = None,
        mode: str = "assisted",
        confidence: float | None = None,
    ) -> Verdict:
        """Validate an action proposal and produce a deterministic Verdict."""
        perms = permissions or RunPermissions()

        # 1. Classify using policy tables
        tier, category, reasons, grantable, summary = policy.classify(
            action=action,
            obs=obs,
            settings=self.settings,
        )

        # 2. Check C-14: Low confidence (< 0.4) elevates SAFE to CONFIRM (exempt harmless app launch)
        if tier == "SAFE" and category not in ("S-08",) and confidence is not None and confidence < 0.4:
            tier = "CONFIRM"
            category = "C-14"
            reasons = [f"Planner confidence {confidence:.2f} is below 0.40"]
            grantable = False
            summary = f"Low confidence ({confidence:.2f}): {summary}"

        # 3. Check C-15: Rate limits
        now = time.monotonic()
        while self._action_timestamps and now - self._action_timestamps[0] > 60.0:
            self._action_timestamps.popleft()

        self._action_timestamps.append(now)

        if len(self._action_timestamps) > self.max_actions_per_minute:
            tier = "CONFIRM"
            category = "C-15"
            reasons = [
                f"Rate limit exceeded: > {self.max_actions_per_minute} actions in 60 seconds"
            ]
            grantable = False

        if category in ("C-01", "C-02", "C-03"):
            self._destructive_action_count += 1
            if self._destructive_action_count > self.max_destructive_per_run:
                tier = "CONFIRM"
                category = "C-15"
                reasons = [
                    f"Destructive operation budget exceeded: > {self.max_destructive_per_run} operations"
                ]
                grantable = False

        # 4. Determine VerdictDecision based on tier, autonomy mode, and permissions
        decision: VerdictDecision
        if tier == "BLOCKED":
            decision = "blocked"
        elif tier == "CONFIRM":
            # trusted mode can auto-allow pre-approved categories if grantable
            if mode == "trusted" and category in perms.granted_categories and grantable:
                decision = "allow"
            else:
                decision = "needs_confirmation"
        else:  # SAFE
            decision = "needs_confirmation" if mode == "step" else "allow"

        verdict = Verdict(
            decision=decision,
            tier=tier,
            category=category,
            reasons=reasons,
            human_summary=summary,
            grantable_for_run=grantable,
        )

        logger.info(
            "validator.verdict",
            action_type=action.type,
            tier=verdict.tier,
            category=verdict.category,
            decision=verdict.decision,
            mode=mode,
        )

        return verdict
