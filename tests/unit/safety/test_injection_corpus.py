"""Unit tests evaluating SafetyValidator against 40+ prompt-injection test cases."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from local_control.core.actions import Action
from local_control.core.types import ImageRef, Observation, Point, ScreenGeometry
from local_control.safety.validator import SafetyValidator

INJECTION_CORPUS_FILE = Path(__file__).parents[2] / "fixtures" / "injection_corpus.yaml"


def load_injection_cases() -> list[dict]:
    with INJECTION_CORPUS_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


@pytest.mark.parametrize("case", load_injection_cases(), ids=lambda c: c["id"])
def test_prompt_injection_case(case: dict) -> None:
    action = TypeAdapter(Action).validate_python(case["attacker_proposal"])
    validator = SafetyValidator()

    # Determine screen state from case if specified
    screen_state = "normal"
    if "locked" in case["id"]:
        screen_state = "secure_desktop_or_locked"

    fg = None
    if "outlook" in case["id"] or "outlook" in case["observation_text"].lower():
        from local_control.core.types import Rect, WindowInfo

        fg = WindowInfo(
            handle=5555,
            title="Inbox - Outlook",
            process_name="outlook.exe",
            pid=1234,
            bbox=Rect(x=0, y=0, width=800, height=600),
            is_foreground=True,
            is_minimized=False,
        )

    obs = Observation(
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
        screen_state=screen_state,
        foreground=fg,
        windows=[],
        cursor=Point(x=100, y=100),
    )

    verdict = validator.validate(action, obs, mode="assisted")

    # In assisted mode, the injected action MUST NOT be auto-allowed if it is BLOCKED or CONFIRM
    assert verdict.tier == case["expected_tier"], (
        f"Injection '{case['id']}': expected tier {case['expected_tier']}, got {verdict.tier}. "
        f"Reasons: {verdict.reasons}"
    )

    if case["expected_tier"] in ("BLOCKED", "CONFIRM"):
        assert verdict.decision != "allow", (
            f"Dangerous injected action in '{case['id']}' was improperly allowed!"
        )


def test_corpus_count_at_least_40() -> None:
    cases = load_injection_cases()
    assert len(cases) >= 40, f"Injection corpus must contain at least 40 cases, got {len(cases)}"
