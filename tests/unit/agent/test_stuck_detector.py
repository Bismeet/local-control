"""Unit tests for StuckDetector."""

import pytest

from local_control.agent.stuck_detector import StuckDetector
from local_control.core.actions import ClickAction


@pytest.mark.unit
def test_stuck_detector_repeated_identical_actions() -> None:
    detector = StuckDetector(repetition_threshold=3)

    act1 = ClickAction(x=100, y=100, target_description="btn", expected_outcome="opened")
    act2 = ClickAction(x=100, y=100, target_description="btn", expected_outcome="opened")
    act3 = ClickAction(x=100, y=100, target_description="btn", expected_outcome="opened")

    detector.record_action(act1)
    stuck, _ = detector.check_stuck()
    assert not stuck

    detector.record_action(act2)
    stuck, _ = detector.check_stuck()
    assert not stuck

    detector.record_action(act3)
    stuck, reason = detector.check_stuck()
    assert stuck
    assert "identical action 3 times" in reason


@pytest.mark.unit
def test_stuck_detector_different_actions_resets_repetition() -> None:
    detector = StuckDetector(repetition_threshold=3)

    act1 = ClickAction(x=100, y=100, target_description="btn", expected_outcome="opened")
    act_diff = ClickAction(x=200, y=200, target_description="other", expected_outcome="opened")

    detector.record_action(act1)
    detector.record_action(act1)
    # Different action breaks the streak
    detector.record_action(act_diff)
    stuck, _ = detector.check_stuck()
    assert not stuck
    assert detector.action_repeat_count == 1


@pytest.mark.unit
def test_stuck_detector_unchanged_screen_phash() -> None:
    detector = StuckDetector(repetition_threshold=3, phash_threshold=6)

    # Initial screen
    detector.record_screen("0000000000000000", expects_change=True)
    assert not detector.check_stuck()[0]

    # Action 1 unchanged
    detector.record_screen("0000000000000000", expects_change=True)
    assert not detector.check_stuck()[0]

    # Action 2 unchanged
    detector.record_screen("0000000000000000", expects_change=True)
    assert not detector.check_stuck()[0]

    # Action 3 unchanged -> STUCK!
    detector.record_screen("0000000000000000", expects_change=True)
    stuck, reason = detector.check_stuck()
    assert stuck
    assert "unchanged over 3 actions" in reason


@pytest.mark.unit
def test_stuck_detector_progress_resets_all() -> None:
    detector = StuckDetector(repetition_threshold=3)

    act = ClickAction(x=50, y=50, target_description="btn", expected_outcome="ok")
    detector.record_action(act)
    detector.record_action(act)
    detector.record_screen("1111111111111111", expects_change=True)
    detector.record_screen("1111111111111111", expects_change=True)

    detector.record_progress()

    assert detector.action_repeat_count == 0
    assert detector.unchanged_screen_count == 0
    assert not detector.check_stuck()[0]
