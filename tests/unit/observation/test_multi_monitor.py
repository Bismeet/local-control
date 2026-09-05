"""Unit tests for multi-monitor geometry, coordinate mapping, and capture."""

from unittest.mock import MagicMock, patch

import pytest

from local_control.core.actions import Point
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ImageRef, ScreenGeometry
from local_control.observation.screen import ScreenCapture


@pytest.mark.unit
def test_screen_geometry_multi_monitor_fields() -> None:
    # Primary monitor at (0, 0)
    primary = ScreenGeometry(
        width_px=1920,
        height_px=1080,
        scale_factor=1.0,
        monitor_index=1,
        left_px=0,
        top_px=0,
    )
    assert primary.left_px == 0
    assert primary.top_px == 0
    assert primary.monitor_index == 1

    # Secondary monitor placed to the left at (-1920, 0)
    secondary = ScreenGeometry(
        width_px=1920,
        height_px=1080,
        scale_factor=1.0,
        monitor_index=2,
        left_px=-1920,
        top_px=0,
    )
    assert secondary.left_px == -1920
    assert secondary.top_px == 0
    assert secondary.monitor_index == 2


@pytest.mark.unit
def test_coordinate_mapper_with_secondary_left_monitor() -> None:
    # Secondary monitor: 1920x1080 at left=-1920, top=0
    # Captured model image: 1280x720
    screen = ScreenGeometry(
        width_px=1920,
        height_px=1080,
        scale_factor=1.0,
        monitor_index=2,
        left_px=-1920,
        top_px=0,
    )
    img = ImageRef(
        path_original="",
        path_model="",
        model_width=1280,
        model_height=720,
        phash="0000000000000000",
    )
    mapper = CoordinateMapper(screen=screen, image=img)

    # (0, 0) in model image maps to (-1920, 0) in screen coordinates
    screen_pt = mapper.to_screen(Point(x=0, y=0))
    assert screen_pt.x == -1920
    assert screen_pt.y == 0

    # (1280, 720) in model image maps to (-1920 + 1920 - 1, 1080 - 1) = (-1, 1079)
    screen_pt_br = mapper.to_screen(Point(x=1280, y=720))
    assert screen_pt_br.x == -1
    assert screen_pt_br.y == 1079

    # Inverse mapping from physical screen coordinate to image coordinate
    img_pt = mapper.to_image(Point(x=-1920, y=0))
    assert img_pt.x == 0
    assert img_pt.y == 0

    img_pt_mid = mapper.to_image(Point(x=-960, y=540))
    assert 630 <= img_pt_mid.x <= 650
    assert 350 <= img_pt_mid.y <= 370


@pytest.mark.unit
def test_coordinate_mapper_with_secondary_right_monitor() -> None:
    # Secondary monitor: 1920x1080 at left=1920, top=0
    screen = ScreenGeometry(
        width_px=1920,
        height_px=1080,
        scale_factor=1.0,
        monitor_index=2,
        left_px=1920,
        top_px=0,
    )
    img = ImageRef(
        path_original="",
        path_model="",
        model_width=1280,
        model_height=720,
        phash="0000000000000000",
    )
    mapper = CoordinateMapper(screen=screen, image=img)

    screen_pt = mapper.to_screen(Point(x=0, y=0))
    assert screen_pt.x == 1920
    assert screen_pt.y == 0

    img_pt = mapper.to_image(Point(x=1920, y=0))
    assert img_pt.x == 0
    assert img_pt.y == 0


@pytest.mark.unit
def test_screen_capture_list_monitors() -> None:
    capture = ScreenCapture()
    mock_monitors = [
        {"left": 0, "top": 0, "width": 3840, "height": 1080},  # All monitors combined
        {"left": 0, "top": 0, "width": 1920, "height": 1080},  # Monitor 1
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},  # Monitor 2
    ]

    with patch("mss.MSS") as mock_mss_cls:
        mock_instance = MagicMock()
        mock_instance.monitors = mock_monitors
        mock_mss_cls.return_value.__enter__.return_value = mock_instance

        monitors = capture.list_monitors()
        assert len(monitors) == 3
        assert monitors[0]["is_all"] is True
        assert monitors[1]["is_primary"] is True
        assert monitors[2]["is_primary"] is False
        assert monitors[2]["left"] == 1920
