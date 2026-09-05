"""Unit tests for UIA tree extraction and Set-of-Marks visual annotation."""

from dataclasses import dataclass
from typing import Any

import pytest
from PIL import Image

from local_control.core.actions import Rect
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ImageRef, ScreenGeometry, UiElement
from local_control.observation.uia import (
    RawElementData,
    UiElementExtractor,
    annotate_set_of_marks,
)


@dataclass
class MockElement:
    control_type: str
    name: str
    visible: bool
    rectangle: Any
    enabled: bool = True
    _children: list[Any] = None  # type: ignore

    def children(self) -> list[Any]:
        return self._children or []


@dataclass
class MockRect:
    left: int
    top: int
    right: int
    bottom: int


@pytest.mark.unit
def test_uia_extractor_tree_walking() -> None:
    extractor = UiElementExtractor(max_depth=4, max_elements=20)

    # Build a simulated UIA element hierarchy
    btn1 = MockElement(
        control_type="Button",
        name="Save",
        visible=True,
        rectangle=MockRect(left=100, top=200, right=180, bottom=230),
    )
    edit1 = MockElement(
        control_type="Edit",
        name="Filename",
        visible=True,
        rectangle=MockRect(left=100, top=150, right=300, bottom=180),
    )
    invisible_btn = MockElement(
        control_type="Button",
        name="Hidden",
        visible=False,
        rectangle=MockRect(left=100, top=100, right=200, bottom=130),
    )
    non_interactive = MockElement(
        control_type="Pane",
        name="",
        visible=True,
        rectangle=MockRect(left=0, top=0, right=500, bottom=500),
        _children=[btn1, edit1, invisible_btn],
    )

    results: list[RawElementData] = []
    extractor._walk_tree(non_interactive, depth=0, results=results)

    # Should capture interactive visible elements (Save, Filename) and skip invisible_btn and empty Pane
    assert len(results) == 2
    assert results[0].role == "Button"
    assert results[0].name == "Save"
    assert results[1].role == "Edit"
    assert results[1].name == "Filename"


@pytest.mark.unit
def test_uia_extractor_coordinate_mapping() -> None:
    extractor = UiElementExtractor()

    # Screen 1920x1080 -> Model Image 1280x720 (scale 1.5)
    screen_geom = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
    image_ref = ImageRef(
        path_original="",
        path_model="",
        model_width=1280,
        model_height=720,
        phash="0000000000000000",
    )
    mapper = CoordinateMapper(screen=screen_geom, image=image_ref)

    # Test extract with invalid handle returns empty list
    assert extractor.extract(0, mapper) == []
    assert extractor.extract(-1, mapper) == []


@pytest.mark.unit
def test_annotate_set_of_marks_badges() -> None:
    img = Image.new("RGB", (640, 480), color=(240, 240, 240))
    elements = [
        UiElement(
            ref="e1",
            role="Button",
            name="Submit",
            bbox=Rect(x=50, y=50, width=80, height=30),
            states=["enabled"],
        ),
        UiElement(
            ref="e2",
            role="Edit",
            name="Search",
            bbox=Rect(x=150, y=50, width=120, height=30),
            states=["enabled"],
        ),
    ]

    annotated = annotate_set_of_marks(img, elements)
    assert annotated is not None
    assert annotated.size == (640, 480)
    assert annotated.mode == "RGB"

    # Image should have changed visually from plain background
    assert annotated.tobytes() != img.tobytes()

    # Calling with empty elements returns unmodified image
    unmod = annotate_set_of_marks(img, [])
    assert unmod == img
