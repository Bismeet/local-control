"""Integration tests for memory hint retrieval and prompt injection."""

from datetime import UTC, datetime

from local_control.agent.planner import Planner
from local_control.core.types import (
    ImageRef,
    Observation,
    Point,
    Rect,
    ScreenGeometry,
    TaskState,
    WindowInfo,
)
from local_control.memory.models import Hint
from local_control.models.fake import FakeModelProvider


def test_planner_prompt_hint_injection() -> None:
    provider = FakeModelProvider()
    planner = Planner(provider=provider)

    state = TaskState(
        run_id="run-hint-1",
        goal="Open Spotify and play playlist",
        autonomy_mode="assisted",
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
        cursor=Point(x=100, y=100),
        foreground=WindowInfo(
            handle=1,
            pid=1234,
            title="Spotify Free",
            process_name="spotify.exe",
            bbox=Rect(x=0, y=0, width=800, height=600),
            is_foreground=True,
            is_minimized=False,
        ),
    )

    hints = [
        Hint(
            app="spotify.exe",
            key="hotkey_play",
            value="Spacebar toggles play/pause",
            confidence=0.9,
            created_at=datetime.now(UTC).isoformat(),
        ),
        Hint(
            app="*",
            key="window_focus",
            value="Click inside window before sending keyboard shortcuts",
            confidence=0.8,
            created_at=datetime.now(UTC).isoformat(),
        ),
    ]

    req = planner.build_request(state=state, obs=obs, hints=hints)
    prompt_text = req.messages[0].parts[0].text  # type: ignore[union-attr]

    assert "# Known Hints" in prompt_text
    assert "[spotify.exe] hotkey_play: Spacebar toggles play/pause" in prompt_text
    assert "window_focus: Click inside window before sending keyboard shortcuts" in prompt_text


def test_planner_hint_token_capping() -> None:
    """Acceptance criteria test: hints in prompts are capped at ~500 tokens (approx 2000 chars)."""
    provider = FakeModelProvider()
    planner = Planner(provider=provider)

    state = TaskState(
        run_id="run-hint-cap",
        goal="Test large hints cap",
        autonomy_mode="assisted",
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
        cursor=Point(x=0, y=0),
    )

    # Generate 50 verbose hints that exceed 2000 characters
    large_hints = [
        Hint(
            app="test_app",
            key=f"hint_key_{i}",
            value=f"Very long hint detail with extensive instructions and recommendations #{i} "
            * 5,
            confidence=0.9,
            created_at=datetime.now(UTC).isoformat(),
        )
        for i in range(50)
    ]

    req = planner.build_request(state=state, obs=obs, hints=large_hints)
    prompt_text = req.messages[0].parts[0].text  # type: ignore[union-attr]

    assert "# Known Hints" in prompt_text
    hints_section = prompt_text.split("# Known Hints")[1].split("# Current Desktop State")[0]
    # The hint section must be capped around 2000 chars
    assert len(hints_section) <= 2200
