"""Shared pytest fixtures for local-control tests."""

from pathlib import Path

import pytest

from local_control.config.settings import Settings
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ImageRef, ScreenGeometry


@pytest.fixture
def temp_run_dir(tmp_path: Path) -> Path:
    """Fixture providing an isolated temporary directory for runs."""
    run_dir = tmp_path / "test_runs"
    run_dir.mkdir()
    return run_dir


@pytest.fixture
def sample_screen_geometry() -> ScreenGeometry:
    """Fixture providing a standard 1920x1080 screen geometry."""
    return ScreenGeometry(
        width_px=1920,
        height_px=1080,
        scale_factor=1.0,
        monitor_index=0,
    )


@pytest.fixture
def sample_image_ref() -> ImageRef:
    """Fixture providing a standard 1280x720 model image reference."""
    return ImageRef(
        path_original="runs/test/screenshots/0001.png",
        path_model="runs/test/screenshots/0001.model.png",
        model_width=1280,
        model_height=720,
        phash="0123456789abcdef",
    )


@pytest.fixture
def coordinate_mapper(
    sample_screen_geometry: ScreenGeometry, sample_image_ref: ImageRef
) -> CoordinateMapper:
    """Fixture providing a CoordinateMapper at 1.5x scale."""
    return CoordinateMapper(sample_screen_geometry, sample_image_ref)


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing fresh default Settings."""
    return Settings()
