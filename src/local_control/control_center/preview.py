"""Live desktop screen preview publisher for Control Center."""

from __future__ import annotations

import asyncio
import io
import time
from typing import Any

import structlog
from PIL import Image, ImageDraw

from local_control.core.events import Event, EventBus

logger = structlog.get_logger(__name__)


class PreviewPublisher:
    """Publishes live screen preview JPEG frames at ~2 fps while a run is active."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        target_fps: float = 2.0,
        quality: int = 60,
    ) -> None:
        self.event_bus = event_bus
        self.target_fps = target_fps
        self.quality = quality
        self.interval = 1.0 / target_fps if target_fps > 0 else 0.5

        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._latest_frame: bytes | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_latest_frame(self) -> bytes:
        """Return the latest encoded JPEG frame or a fallback placeholder."""
        if self._latest_frame:
            return self._latest_frame
        return self._create_placeholder_frame("No active preview")

    def start(self) -> None:
        """Start the background preview capture loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info("preview_publisher.started", fps=self.target_fps, quality=self.quality)

    def stop(self) -> None:
        """Stop the background preview capture loop."""
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("preview_publisher.stopped")

    async def _capture_loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                frame_bytes = await asyncio.to_thread(self._capture_and_encode)
                self._latest_frame = frame_bytes

                if self.event_bus:
                    # Publish preview event
                    import base64

                    b64 = base64.b64encode(frame_bytes).decode("ascii")
                    await self.event_bus.publish(
                        Event(
                            run_id="current",
                            type="preview_frame",
                            payload={"image_base64": b64, "timestamp": time.time()},
                        )
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("preview_publisher.capture_error", error=str(e))

            elapsed = time.monotonic() - t0
            sleep_time = max(0.05, self.interval - elapsed)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    def _capture_and_encode(self) -> bytes:
        """Capture screen and encode as JPEG quality 60."""
        try:
            import mss

            cls = getattr(mss, "MSS", None) or mss.mss
            with cls() as sct:
                # Primary monitor is monitors[1]
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                # Downscale to model max width 1280 to save network bandwidth
                if img.width > 1280:
                    ratio = 1280.0 / img.width
                    img = img.resize(
                        (1280, int(img.height * ratio)),
                        resample=Image.Resampling.LANCZOS,
                    )

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self.quality)
                return buf.getvalue()
        except Exception:
            return self._create_placeholder_frame("Screen capture unavailable")

    def _create_placeholder_frame(self, message: str) -> bytes:
        """Generate a lightweight SVG-like image buffer as placeholder."""
        img = Image.new("RGB", (640, 360), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)
        draw.text((220, 170), message, fill=(180, 180, 190))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()
