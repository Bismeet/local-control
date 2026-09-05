"""Unit tests for CoordinateMapper scaling, clamping, and invertibility."""

import pytest

from local_control.core.actions import Point
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ImageRef, ScreenGeometry


@pytest.mark.unit
def test_coordinate_mapping_scale_1_0() -> None:
    """Verify 1.0x scale (exact 1:1 mapping)."""
    screen = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
    image = ImageRef(
        path_original="orig.png",
        path_model="model.png",
        model_width=1920,
        model_height=1080,
        phash="abc",
    )
    mapper = CoordinateMapper(screen, image)

    pt = Point(x=500, y=300)
    screen_pt = mapper.to_screen(pt)
    assert screen_pt.x == 500
    assert screen_pt.y == 300

    img_pt = mapper.to_image(screen_pt)
    assert img_pt.x == 500
    assert img_pt.y == 300


@pytest.mark.unit
def test_coordinate_mapping_scale_1_5() -> None:
    """Verify 1.5x scale (e.g. 1920x1080 screen downscaled to 1280x720)."""
    screen = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
    image = ImageRef(
        path_original="orig.png",
        path_model="model.png",
        model_width=1280,
        model_height=720,
        phash="abc",
    )
    mapper = CoordinateMapper(screen, image)

    # (640, 360) is the center of 1280x720 -> maps to (960, 540) on 1920x1080
    model_pt = Point(x=640, y=360)
    screen_pt = mapper.to_screen(model_pt)
    assert screen_pt.x == 960
    assert screen_pt.y == 540

    reloaded_model_pt = mapper.to_image(screen_pt)
    assert reloaded_model_pt.x == 640
    assert reloaded_model_pt.y == 360


@pytest.mark.unit
def test_coordinate_mapping_scale_2_0() -> None:
    """Verify 2.0x scale (e.g. 3840x2160 4K screen downscaled to 1920x1080)."""
    screen = ScreenGeometry(width_px=3840, height_px=2160, scale_factor=1.0)
    image = ImageRef(
        path_original="orig.png",
        path_model="model.png",
        model_width=1920,
        model_height=1080,
        phash="abc",
    )
    mapper = CoordinateMapper(screen, image)

    screen_pt = mapper.to_screen(Point(x=100, y=200))
    assert screen_pt.x == 200
    assert screen_pt.y == 400

    img_pt = mapper.to_image(screen_pt)
    assert img_pt.x == 100
    assert img_pt.y == 200


@pytest.mark.unit
def test_coordinate_clamping() -> None:
    """Verify out-of-bounds coordinates clamp to boundary edges."""
    screen = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
    image = ImageRef(
        path_original="orig.png",
        path_model="model.png",
        model_width=1280,
        model_height=720,
        phash="abc",
    )
    mapper = CoordinateMapper(screen, image)

    # Negative coordinates
    screen_neg = mapper.to_screen(Point(x=-50, y=-20))
    assert screen_neg.x == 0
    assert screen_neg.y == 0

    # Overflow coordinates
    screen_overflow = mapper.to_screen(Point(x=2000, y=1000))
    assert screen_overflow.x == 1919
    assert screen_overflow.y == 1079

    # Inverse negative clamping
    img_neg = mapper.to_image(Point(x=-100, y=-50))
    assert img_neg.x == 0
    assert img_neg.y == 0

    # Inverse overflow clamping
    img_overflow = mapper.to_image(Point(x=5000, y=3000))
    assert img_overflow.x == 1279
    assert img_overflow.y == 719


@pytest.mark.unit
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_coordinate_invertibility(scale: float) -> None:
    """Verify to_image(to_screen(p)) == p within 1 px error across scaling factors."""
    screen_w = int(1280 * scale)
    screen_h = int(720 * scale)
    screen = ScreenGeometry(width_px=screen_w, height_px=screen_h, scale_factor=1.0)
    image = ImageRef(
        path_original="orig.png",
        path_model="model.png",
        model_width=1280,
        model_height=720,
        phash="abc",
    )
    mapper = CoordinateMapper(screen, image)

    test_points = [
        Point(x=0, y=0),
        Point(x=100, y=150),
        Point(x=640, y=360),
        Point(x=1200, y=700),
        Point(x=1279, y=719),
    ]

    for orig_pt in test_points:
        scr = mapper.to_screen(orig_pt)
        inv = mapper.to_image(scr)
        assert abs(inv.x - orig_pt.x) <= 1
        assert abs(inv.y - orig_pt.y) <= 1
