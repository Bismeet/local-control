"""Windows UI Automation (UIA) tree extraction and Set-of-Marks visual annotation."""

import os
from dataclasses import dataclass
from typing import Any

import structlog
from PIL import Image, ImageDraw, ImageFont

from local_control.core.actions import Point, Rect
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import UiElement

logger = structlog.get_logger(__name__)

INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "Button",
        "CheckBox",
        "RadioButton",
        "ComboBox",
        "Edit",
        "Hyperlink",
        "ListItem",
        "MenuItem",
        "TabItem",
        "TreeItem",
        "ToolBar",
        "Document",
        "ScrollBar",
        "Slider",
        "Spinner",
        "SplitButton",
    }
)

BADGE_COLORS = [
    ("#FFE600", "#000000"),  # Yellow background, black text
    ("#00E5FF", "#000000"),  # Cyan background, black text
    ("#FF5722", "#FFFFFF"),  # Deep orange background, white text
    ("#00E676", "#000000"),  # Green background, black text
    ("#E040FB", "#FFFFFF"),  # Magenta background, white text
]


@dataclass
class RawElementData:
    role: str
    name: str
    screen_rect: tuple[int, int, int, int]  # left, top, right, bottom
    states: list[str]


class UiElementExtractor:
    """Extracts interactive UI elements from a window's UIA accessibility tree."""

    def __init__(self, max_depth: int = 6, max_elements: int = 80) -> None:
        self.max_depth = max_depth
        self.max_elements = max_elements

    def _walk_tree(
        self,
        elem: Any,
        depth: int,
        results: list[RawElementData],
    ) -> None:
        if depth > self.max_depth or len(results) >= self.max_elements:
            return

        try:
            role = str(getattr(elem, "control_type", "") or "")
            name = str(getattr(elem, "name", "") or "")
            visible = bool(getattr(elem, "visible", True))
            rect = getattr(elem, "rectangle", None)

            if visible and rect is not None:
                left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
                w, h = right - left, bottom - top
                if (
                    w > 2
                    and h > 2
                    and (role in INTERACTIVE_ROLES or (role == "Text" and name.strip()))
                ):
                    states: list[str] = []
                    if getattr(elem, "enabled", True):
                        states.append("enabled")
                    results.append(
                        RawElementData(
                            role=role,
                            name=name.strip(),
                            screen_rect=(left, top, right, bottom),
                            states=states,
                        )
                    )

            # Recurse children
            children = getattr(elem, "children", None)
            if callable(children):
                for child in children():
                    if len(results) >= self.max_elements:
                        break
                    self._walk_tree(child, depth + 1, results)
        except Exception as e:
            logger.debug("uia.walk_error", error=str(e))

    def extract(
        self,
        window_handle: int | None,
        mapper: CoordinateMapper,
    ) -> list[UiElement]:
        """Extract interactive UI elements for a window, mapped to model image coordinates."""
        if not window_handle or window_handle <= 0 or os.name != "nt":
            return []

        raw_elements: list[RawElementData] = []
        try:
            from pywinauto.uia_element_info import UIAElementInfo

            root_info = UIAElementInfo(window_handle)
            self._walk_tree(root_info, depth=0, results=raw_elements)
        except Exception as e:
            logger.debug("uia.extract_failed", handle=window_handle, error=str(e))
            return []

        ui_elements: list[UiElement] = []
        for idx, raw in enumerate(raw_elements):
            left, top, right, bottom = raw.screen_rect
            tl = mapper.to_image(Point(x=left, y=top))
            br = mapper.to_image(Point(x=right, y=bottom))

            x = min(tl.x, br.x)
            y = min(tl.y, br.y)
            w = max(4, abs(br.x - tl.x))
            h = max(4, abs(br.y - tl.y))

            ref = f"e{idx + 1}"
            ui_elements.append(
                UiElement(
                    ref=ref,
                    role=raw.role,
                    name=raw.name,
                    bbox=Rect(x=x, y=y, width=w, height=h),
                    states=raw.states,
                )
            )

        return ui_elements


def annotate_set_of_marks(
    image: Image.Image,
    elements: list[UiElement],
) -> Image.Image:
    """Annotate model screenshot with high-contrast Set-of-Marks badge labels.

    Returns a new annotated PIL Image preserving the original.
    """
    if not elements:
        return image

    annotated = image.copy().convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    font = ImageFont.load_default()

    for i, el in enumerate(elements):
        bg_color, text_color = BADGE_COLORS[i % len(BADGE_COLORS)]
        ref_text = f"[{el.ref}]"

        bx, by = el.bbox.x, el.bbox.y
        bw, bh = el.bbox.width, el.bbox.height

        # Draw bounding outline around the element
        draw.rectangle(
            [bx, by, bx + bw, by + bh],
            outline=bg_color,
            width=2,
        )

        # Draw badge label in top-left corner
        badge_w = len(ref_text) * 7 + 4
        badge_h = 13
        draw.rectangle(
            [bx, max(0, by - badge_h), bx + badge_w, max(badge_h, by)],
            fill=bg_color,
            outline="#000000",
            width=1,
        )
        draw.text(
            (bx + 2, max(0, by - badge_h) + 1),
            ref_text,
            fill=text_color,
            font=font,
        )

    combined = Image.alpha_composite(annotated, overlay)
    return combined.convert("RGB")
