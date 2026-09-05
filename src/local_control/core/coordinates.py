"""Coordinate mapping between model image space and physical screen pixels."""

from local_control.core.actions import Point
from local_control.core.errors import CoordinateMappingError
from local_control.core.types import ImageRef, Rect, ScreenGeometry


class CoordinateMapper:
    """Pure mathematical transformation between model image space and screen coordinates."""

    def __init__(
        self,
        screen: ScreenGeometry,
        image: ImageRef,
        origin_x: int | None = None,
        origin_y: int | None = None,
    ) -> None:
        if image.model_width <= 0 or image.model_height <= 0:
            raise CoordinateMappingError("Model image dimensions must be positive.")
        if screen.width_px <= 0 or screen.height_px <= 0:
            raise CoordinateMappingError("Screen dimensions must be positive.")

        self.screen = screen
        self.image = image
        self.origin_x = screen.left_px if origin_x is None else origin_x
        self.origin_y = screen.top_px if origin_y is None else origin_y

        self.scale_x = screen.width_px / image.model_width
        self.scale_y = screen.height_px / image.model_height

    def to_screen(self, point: Point) -> Point:
        """Map a Point from model image space to physical screen pixel coordinates.

        Clamps coordinates to the screen boundary.
        """
        raw_x = round(point.x * self.scale_x) + self.origin_x
        raw_y = round(point.y * self.scale_y) + self.origin_y

        clamped_x = max(self.origin_x, min(raw_x, self.origin_x + self.screen.width_px - 1))
        clamped_y = max(self.origin_y, min(raw_y, self.origin_y + self.screen.height_px - 1))

        return Point(x=clamped_x, y=clamped_y)

    def to_image(self, point: Point) -> Point:
        """Map a Point from physical screen pixel coordinates to model image space.

        Clamps coordinates to the model image boundary.
        """
        rel_x = point.x - self.origin_x
        rel_y = point.y - self.origin_y

        raw_x = round(rel_x / self.scale_x)
        raw_y = round(rel_y / self.scale_y)

        clamped_x = max(0, min(raw_x, self.image.model_width - 1))
        clamped_y = max(0, min(raw_y, self.image.model_height - 1))

        return Point(x=clamped_x, y=clamped_y)

    def from_zoom(self, point: Point, zoom_rect: Rect) -> Point:
        """Map a Point from zoom crop coordinates back to physical screen coordinates."""
        screen_x = self.origin_x + zoom_rect.x + point.x
        screen_y = self.origin_y + zoom_rect.y + point.y
        clamped_x = max(self.origin_x, min(screen_x, self.origin_x + self.screen.width_px - 1))
        clamped_y = max(self.origin_y, min(screen_y, self.origin_y + self.screen.height_px - 1))
        return Point(x=clamped_x, y=clamped_y)

    def to_zoom(self, point: Point, zoom_rect: Rect) -> Point:
        """Map a physical screen Point into relative zoom crop coordinates."""
        rel_x = point.x - (self.origin_x + zoom_rect.x)
        rel_y = point.y - (self.origin_y + zoom_rect.y)
        clamped_x = max(0, min(rel_x, zoom_rect.width - 1))
        clamped_y = max(0, min(rel_y, zoom_rect.height - 1))
        return Point(x=clamped_x, y=clamped_y)
