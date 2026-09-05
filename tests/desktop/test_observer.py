"""Desktop integration tests for Observer on Windows."""

import os
from pathlib import Path

import pytest

from local_control.config.settings import Settings
from local_control.core.run_store import RunStore
from local_control.observation.observer import Observer


@pytest.mark.desktop
def test_observer_observe_construction() -> None:
    """Verify Observer produces a complete, typed Observation with mapped coordinates."""
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    settings = Settings.load()
    observer = Observer(settings=settings)

    obs = observer.observe(step_index=0)

    assert obs.step_index == 0
    assert obs.screen.width_px > 0
    assert obs.screen.height_px > 0
    assert obs.image.model_width <= settings.observation.model_max_width
    assert len(obs.image.phash) == 16
    assert obs.screen_state == "normal"

    # Cursor must map inside model image space
    assert 0 <= obs.cursor.x < obs.image.model_width
    assert 0 <= obs.cursor.y < obs.image.model_height

    # Windows should have mapped bboxes
    for win in obs.windows:
        assert win.bbox.width >= 0
        assert win.bbox.height >= 0


@pytest.mark.desktop
def test_observer_screenshot_persistence(tmp_path: Path) -> None:
    """Verify Observer writes original and model PNG screenshots to run directory."""
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    store = RunStore(base_dir=tmp_path)
    run_id = "run-observer-test"
    store.create_run(run_id=run_id, goal="Test observation persistence", mode="step")

    observer = Observer(run_store=store)
    obs = observer.observe(step_index=1, run_id=run_id)

    run_dir = store.get_run_dir(run_id)
    orig_file = run_dir / obs.image.path_original
    model_file = run_dir / obs.image.path_model

    assert orig_file.exists()
    assert model_file.exists()
    assert orig_file.stat().st_size > 0
    assert model_file.stat().st_size > 0
