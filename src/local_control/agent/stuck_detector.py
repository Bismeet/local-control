"""Stuck detection: detect repeated identical actions or visually frozen screens."""

from __future__ import annotations

import json

from local_control.core.actions import Action
from local_control.observation.image import hamming_distance


class StuckDetector:
    """Tracks action repetitions and screen immobility to detect stuck agent loops."""

    def __init__(
        self,
        repetition_threshold: int = 3,
        phash_threshold: int = 6,
    ) -> None:
        self.repetition_threshold = repetition_threshold
        self.phash_threshold = phash_threshold

        self.last_action_repr: str | None = None
        self.action_repeat_count: int = 0

        self.last_phash: str | None = None
        self.unchanged_screen_count: int = 0

    def _serialize_action(self, action: Action) -> str:
        """Serialize core action fields to a deterministic string."""
        data = action.model_dump()
        # Exclude ephemeral / non-functional fields from identity check
        data.pop("settle_ms", None)
        data.pop("target_description", None)
        data.pop("expected_outcome", None)
        return json.dumps(data, sort_keys=True, default=str)

    def record_action(self, action: Action) -> None:
        """Record an attempted action and update repetition counter."""
        act_repr = self._serialize_action(action)
        if self.last_action_repr == act_repr:
            self.action_repeat_count += 1
        else:
            self.last_action_repr = act_repr
            self.action_repeat_count = 1

    def record_screen(self, phash: str | None, expects_change: bool = True) -> None:
        """Record screen perceptual hash and update unchanged counter if change was expected."""
        if not phash or not expects_change:
            return

        if self.last_phash is not None:
            dist = hamming_distance(self.last_phash, phash)
            if dist <= self.phash_threshold:
                self.unchanged_screen_count += 1
            else:
                self.unchanged_screen_count = 0
        else:
            self.unchanged_screen_count = 0

        self.last_phash = phash

    def record_progress(self) -> None:
        """Reset stuck counters when verified forward progress occurs."""
        self.action_repeat_count = 0
        self.unchanged_screen_count = 0
        self.last_action_repr = None

    def check_stuck(self) -> tuple[bool, str]:
        """Check whether any stuck condition has been met.

        Returns (is_stuck, reason).
        """
        if self.action_repeat_count >= self.repetition_threshold:
            return (
                True,
                f"Agent performed identical action {self.action_repeat_count} times in a row",
            )

        if self.unchanged_screen_count >= self.repetition_threshold:
            return (
                True,
                f"Screen perceptual hash remained unchanged over {self.unchanged_screen_count} actions that expected change",
            )

        return False, ""

    def reset(self) -> None:
        """Fully reset the detector state."""
        self.action_repeat_count = 0
        self.unchanged_screen_count = 0
        self.last_action_repr = None
        self.last_phash = None
