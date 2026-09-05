"""Table-driven unit tests validating all policy rules from tests/fixtures/policy_cases.yaml."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from local_control.core.actions import Action
from local_control.core.types import (
    ImageRef,
    Observation,
    Point,
    Rect,
    ScreenGeometry,
    WindowInfo,
)
from local_control.safety import policy

CASES_FILE = Path(__file__).parents[2] / "fixtures" / "policy_cases.yaml"


def load_policy_cases() -> list[dict]:
    with CASES_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


@pytest.mark.parametrize("case", load_policy_cases(), ids=lambda c: c["id"])
def test_policy_case(case: dict) -> None:
    action = TypeAdapter(Action).validate_python(case["action"])

    # Build observation
    screen_state = case.get("obs_screen_state", "normal")
    fg = None
    if "obs_foreground" in case:
        raw_fg = case["obs_foreground"]
        fg = WindowInfo(
            handle=raw_fg.get("handle", 1000),
            title=raw_fg.get("title", "Window"),
            process_name=raw_fg.get("process_name", "app.exe"),
            pid=raw_fg.get("pid", 1234),
            bbox=Rect(x=0, y=0, width=800, height=600),
            is_foreground=True,
            is_minimized=False,
        )

    windows = []
    if "obs_windows" in case:
        for raw_w in case["obs_windows"]:
            windows.append(
                WindowInfo(
                    handle=raw_w.get("handle", 2000),
                    title=raw_w.get("title", "Window"),
                    process_name=raw_w.get("process_name", "app.exe"),
                    pid=raw_w.get("pid", 2345),
                    bbox=Rect(x=0, y=0, width=800, height=600),
                    is_foreground=False,
                    is_minimized=False,
                )
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
        windows=windows,
        cursor=Point(x=100, y=100),
    )

    tier, category, reasons, grantable, summary = policy.classify(action, obs)

    assert tier == case["expected_tier"], (
        f"Case '{case['id']}': expected tier {case['expected_tier']}, got {tier}. Reasons: {reasons}"
    )
    assert category == case["expected_category"], (
        f"Case '{case['id']}': expected category {case['expected_category']}, got {category}"
    )


def test_unclassified_action_defaults_to_c17() -> None:
    from local_control.core.actions import ActionBase

    # Dummy action type not in any specific B/C/S rules
    class UnknownAction(ActionBase):
        type: str = "quantum_teleport"

    action = UnknownAction(target_description="test", expected_outcome="teleport")
    tier, category, reasons, grantable, _ = policy.classify(action)

    assert tier == "CONFIRM"
    assert category == "C-17"
    assert grantable is False
