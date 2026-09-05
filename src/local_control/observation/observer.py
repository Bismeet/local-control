import io
import os
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from local_control.config.settings import Settings
from local_control.core.actions import Point, Rect
from local_control.core.coordinates import CoordinateMapper
from local_control.core.run_store import RunStore
from local_control.core.types import (
    ActionResult,
    ImageRef,
    Observation,
    OcrSpan,
    ScreenGeometry,
    ScreenState,
    UiElement,
    WindowInfo,
)
from local_control.observation.image import (
    compute_dhash,
    downscale_image,
    frame_to_pillow,
    is_black_frame,
)
from local_control.observation.ocr import OCRProvider, RapidOCRAdapter
from local_control.observation.screen import ScreenCapture
from local_control.observation.uia import UiElementExtractor, annotate_set_of_marks
from local_control.observation.windows import WindowManager


class Observer:
    """Produces typed Observation objects by capturing the screen and desktop state."""

    def __init__(
        self,
        screen_capture: ScreenCapture | None = None,
        window_manager: WindowManager | None = None,
        run_store: RunStore | None = None,
        settings: Settings | None = None,
        ocr_provider: OCRProvider | None = None,
        uia_extractor: UiElementExtractor | None = None,
    ) -> None:
        self.screen_capture = screen_capture or ScreenCapture()
        self.window_manager = window_manager or WindowManager()
        self.run_store = run_store
        self.settings = settings or Settings.load()
        self.ocr_provider = ocr_provider or RapidOCRAdapter()
        self.uia_extractor = uia_extractor or UiElementExtractor()

    def _map_window_to_image(self, win: WindowInfo, mapper: CoordinateMapper) -> WindowInfo:
        """Transform window bbox from screen coordinates to model image coordinates."""
        top_left = mapper.to_image(Point(x=win.bbox.x, y=win.bbox.y))
        bottom_right = mapper.to_image(
            Point(x=win.bbox.x + win.bbox.width, y=win.bbox.y + win.bbox.height)
        )
        mapped_bbox = Rect(
            x=top_left.x,
            y=top_left.y,
            width=max(0, bottom_right.x - top_left.x),
            height=max(0, bottom_right.y - top_left.y),
        )
        return WindowInfo(
            handle=win.handle,
            title=win.title,
            process_name=win.process_name,
            pid=win.pid,
            bbox=mapped_bbox,
            is_foreground=win.is_foreground,
            is_minimized=win.is_minimized,
            is_elevated=win.is_elevated,
        )

    def observe(
        self,
        last_result: ActionResult | None = None,
        step_index: int = 0,
        run_id: str | None = None,
        zoom_rect: Rect | None = None,
    ) -> Observation:
        """Capture the screen and desktop state and build a typed Observation."""
        captured_at = datetime.now(UTC)

        target_mon = getattr(self.settings.observation, "monitor_index", 0)
        raw_frame = self.screen_capture.capture(monitor_index=target_mon)
        orig_img: Image.Image = frame_to_pillow(raw_frame)

        screen_geo = ScreenGeometry(
            width_px=raw_frame.width,
            height_px=raw_frame.height,
            scale_factor=1.0,
            monitor_index=raw_frame.monitor_index,
            left_px=raw_frame.left,
            top_px=raw_frame.top,
        )

        # 2. Downscale image for model and compute dHash
        max_w = self.settings.observation.model_max_width
        model_img, _ = downscale_image(orig_img, max_width=max_w)
        phash = compute_dhash(model_img)

        # 3. Screen state heuristics
        screen_state: ScreenState = "normal"
        if is_black_frame(orig_img):
            screen_state = "black_frame"

        # 4. Coordinate Mapper
        temp_img_ref = ImageRef(
            path_original="",
            path_model="",
            model_width=model_img.width,
            model_height=model_img.height,
            phash=phash,
        )
        mapper = CoordinateMapper(screen_geo, temp_img_ref)

        # 5. Cursor position
        cursor_pt = Point(x=0, y=0)
        if os.name == "nt":
            try:
                import win32gui

                cur_x, cur_y = win32gui.GetCursorPos()
                cursor_pt = mapper.to_image(Point(x=cur_x, y=cur_y))
            except Exception:
                cursor_pt = Point(x=0, y=0)

        # 6. Windows list and foreground window
        raw_windows = self.window_manager.list_windows()
        max_windows = self.settings.observation.max_windows
        capped_windows = raw_windows[:max_windows]

        windows = [self._map_window_to_image(w, mapper) for w in capped_windows]

        raw_fg = self.window_manager.foreground()
        fg_window = self._map_window_to_image(raw_fg, mapper) if raw_fg else None

        # 6b. UIA Tree extraction and Set-of-Marks visual badges
        ui_elements: list[UiElement] | None = None
        fg_handle = raw_fg.handle if raw_fg else None
        if self.settings.observation.set_of_marks:
            ui_elements = self.uia_extractor.extract(fg_handle, mapper)
            if ui_elements:
                model_img = annotate_set_of_marks(model_img, ui_elements)
                phash = compute_dhash(model_img)

        # 6c. OCR extraction
        ocr_spans: list[OcrSpan] | None = None
        if self.settings.observation.ocr_always:
            buf = io.BytesIO()
            orig_img.save(buf, format="PNG")
            ocr_spans = self.ocr_provider.recognize(buf.getvalue())

        # 7. Persist screenshots if run_store and run_id provided
        path_orig = ""
        path_model = ""
        path_zoom: str | None = None
        if self.run_store and run_id:
            run_dir = self.run_store.get_run_dir(run_id)
            screenshots_dir = run_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)

            orig_file = screenshots_dir / f"{step_index:04d}.png"
            model_file = screenshots_dir / f"{step_index:04d}.model.png"

            orig_img.save(orig_file, format="PNG")
            model_img.save(model_file, format="PNG")

            path_orig = str(orig_file.relative_to(run_dir))
            path_model = str(model_file.relative_to(run_dir))

            if zoom_rect:
                box = (
                    max(0, zoom_rect.x),
                    max(0, zoom_rect.y),
                    min(orig_img.width, zoom_rect.x + zoom_rect.width),
                    min(orig_img.height, zoom_rect.y + zoom_rect.height),
                )
                if box[2] > box[0] and box[3] > box[1]:
                    zoom_crop = orig_img.crop(box)
                    zoom_file = screenshots_dir / f"{step_index:04d}.zoom.png"
                    zoom_crop.save(zoom_file, format="PNG")
                    path_zoom = str(zoom_file.relative_to(run_dir))

        image_ref = ImageRef(
            path_original=path_orig,
            path_model=path_model,
            model_width=model_img.width,
            model_height=model_img.height,
            phash=phash,
            path_zoom=path_zoom,
            zoom_rect=zoom_rect,
        )

        return Observation(
            step_index=step_index,
            captured_at=captured_at,
            screen=screen_geo,
            image=image_ref,
            screen_state=screen_state,
            foreground=fg_window,
            windows=windows,
            cursor=cursor_pt,
            last_result=last_result,
            ocr=ocr_spans,
            ui_elements=ui_elements,
        )

    def capture_zoom(
        self,
        rect: Rect,
        run_id: str | None = None,
        step_index: int = 0,
    ) -> Path | None:
        """Capture a full-resolution crop of the screen at specified rect."""

        raw_frame = self.screen_capture.capture(monitor_index=0)
        orig_img: Image.Image = frame_to_pillow(raw_frame)
        box = (
            max(0, rect.x),
            max(0, rect.y),
            min(orig_img.width, rect.x + rect.width),
            min(orig_img.height, rect.y + rect.height),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None

        crop = orig_img.crop(box)
        if self.run_store and run_id:
            run_dir = self.run_store.get_run_dir(run_id)
            screenshots_dir = run_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            zoom_file = screenshots_dir / f"{step_index:04d}.zoom.png"
            crop.save(zoom_file, format="PNG")
            return zoom_file
        return None
