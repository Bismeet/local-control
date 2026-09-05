"""DPI awareness initialization and screen capture abstraction for Windows."""

import ctypes
import os
from dataclasses import dataclass

import mss
import structlog

from local_control.core.errors import ExecutionError

logger = structlog.get_logger(__name__)

_DPI_INITIALIZED = False


def ensure_interactive_desktop() -> None:
    """Ensure the calling thread is attached to the interactive 'Default' desktop on Windows."""
    if os.name == "nt":
        try:
            hdesk = ctypes.windll.user32.GetThreadDesktop(
                ctypes.windll.kernel32.GetCurrentThreadId()
            )
            name_buf = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_ulong(0)
            if (
                ctypes.windll.user32.GetUserObjectInformationW(
                    hdesk, 2, name_buf, 256, ctypes.byref(needed)
                )
                and name_buf.value.lower() != "default"
            ):
                default_desk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if default_desk:
                    ctypes.windll.user32.SetThreadDesktop(default_desk)
        except Exception as e:
            logger.debug("screen.attach_desktop_failed", error=str(e))


def init_dpi_awareness() -> bool:
    """Initialize Per-Monitor v2 DPI awareness on Windows.

    Must be the first Windows API call in the process to ensure physical screen
    coordinates match mss screen dimensions.
    """
    global _DPI_INITIALIZED
    ensure_interactive_desktop()
    if _DPI_INITIALIZED:
        return True

    if os.name != "nt":
        _DPI_INITIALIZED = True
        return True

    success = False
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if hr == 0:
            success = True
            logger.debug("dpi_awareness.set", mode="per_monitor_v2")
    except Exception as e:
        logger.debug("dpi_awareness.shcore_failed", error=str(e))

    if not success:
        try:
            res = ctypes.windll.user32.SetProcessDPIAware()
            if res != 0:
                success = True
                logger.debug("dpi_awareness.set", mode="system_aware")
        except Exception as e:
            logger.warning("dpi_awareness.user32_failed", error=str(e))

    _DPI_INITIALIZED = True
    return success


@dataclass(frozen=True)
class RawFrame:
    """Raw screen capture frame containing BGRA pixel data in physical dimensions."""

    width: int
    height: int
    raw_bytes: bytes
    monitor_index: int = 0


class ScreenCapture:
    """Primary monitor screen capture using mss."""

    def __init__(self) -> None:
        init_dpi_awareness()

    def capture(self, monitor_index: int = 0) -> RawFrame:
        """Capture the screen of the primary monitor in physical pixels.

        In mss, monitor 0 is all monitors combined, and monitor 1 is the primary physical monitor.
        """
        ensure_interactive_desktop()
        try:
            with mss.MSS() as sct:
                monitors = sct.monitors
                if not monitors:
                    raise ExecutionError("mss did not detect any active monitors.")

                # Target monitor: monitor_index=0 maps to monitors[1] if available, else monitors[0]
                target_idx = monitor_index + 1 if len(monitors) > 1 else 0
                if target_idx >= len(monitors):
                    target_idx = 0

                mon = monitors[target_idx]
                shot = sct.grab(mon)

                if shot.width <= 0 or shot.height <= 0:
                    raise ExecutionError(
                        f"Captured invalid screen dimensions: {shot.width}x{shot.height}"
                    )

                raw_data = bytes(shot.raw)
                expected_size = shot.width * shot.height * 4
                if len(raw_data) != expected_size:
                    raise ExecutionError(
                        f"Captured buffer size mismatch: expected {expected_size} bytes, got {len(raw_data)}"
                    )

                return RawFrame(
                    width=shot.width,
                    height=shot.height,
                    raw_bytes=raw_data,
                    monitor_index=monitor_index,
                )
        except Exception as e:
            if isinstance(e, ExecutionError):
                raise
            raise ExecutionError(f"Screen capture failed: {e}") from e
