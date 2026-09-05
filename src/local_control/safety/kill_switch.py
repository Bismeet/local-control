"""Kill switch, cancellation tokens, and failsafe monitors for local-control."""

import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class StopRequestedError(Exception):
    """Raised when execution is aborted due to a StopToken being set."""

    def __init__(self, reason: str = "user") -> None:
        super().__init__(f"Execution stopped: {reason}")
        self.reason = reason


class StopToken:
    """Thread-safe cancellation token wrapping an event and a stop reason."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        """Check if stop has been requested."""
        return self._event.is_set()

    def reason(self) -> str | None:
        """Get the reason why stop was requested."""
        with self._lock:
            return self._reason

    def set(self, reason: str = "user") -> None:
        """Request execution to stop with the given reason."""
        with self._lock:
            if not self._event.is_set():
                self._reason = reason
                self._event.set()
                logger.info("stop_token.set", reason=reason)

    def check(self) -> None:
        """Raise StopRequestedError if stop has been requested."""
        if self.is_set():
            raise StopRequestedError(self.reason() or "user")

    def clear(self) -> None:
        """Reset the stop token (for testing or reuse)."""
        with self._lock:
            self._reason = None
            self._event.clear()


def get_default_stop_file_path() -> Path:
    """Return the default stop file path in %LOCALAPPDATA%/local-control/STOP."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "local-control" / "STOP"


class KillSwitch:
    """Background monitor for emergency stops: mouse corners, hotkey, stop file."""

    def __init__(
        self,
        token: StopToken | None = None,
        poll_interval_s: float = 0.1,
        corner_hold_time_s: float = 0.3,
        corner_margin_px: int = 5,
        stop_file_path: Path | None = None,
        hotkey: str = "<ctrl>+<alt>+<shift>+q",
    ) -> None:
        self.token = token or StopToken()
        self.poll_interval_s = poll_interval_s
        self.corner_hold_time_s = corner_hold_time_s
        self.corner_margin_px = corner_margin_px
        self.stop_file_path = stop_file_path or get_default_stop_file_path()
        self.hotkey = hotkey

        self._running = threading.Event()
        self._poller_thread: threading.Thread | None = None
        self._hotkey_listener: Any | None = None
        self._corner_start_time: float | None = None

    def start(self) -> None:
        """Start the kill switch poller and hotkey listener."""
        if self._running.is_set():
            return

        self._running.set()

        # Start poller thread for corner detection and stop file
        self._poller_thread = threading.Thread(
            target=self._poll_loop,
            name="KillSwitchPoller",
            daemon=True,
        )
        self._poller_thread.start()

        # Start pynput global hotkey listener
        try:
            from pynput import keyboard

            def on_hotkey_triggered() -> None:
                logger.warning("kill_switch.hotkey_triggered", hotkey=self.hotkey)
                self.token.set("hotkey")

            self._hotkey_listener = keyboard.GlobalHotKeys({self.hotkey: on_hotkey_triggered})
            self._hotkey_listener.start()
        except Exception as e:
            logger.debug("kill_switch.hotkey_listener_unavailable", error=str(e))
            self._hotkey_listener = None

    def stop(self) -> None:
        """Stop all background monitors."""
        self._running.clear()
        if self._hotkey_listener:
            with contextlib.suppress(Exception):
                self._hotkey_listener.stop()
            self._hotkey_listener = None

        if self._poller_thread and self._poller_thread.is_alive():
            self._poller_thread.join(timeout=1.0)
            self._poller_thread = None

    def trigger(self, reason: str = "manual") -> None:
        """Programmatically trigger the kill switch."""
        self.token.set(reason)

    def _check_stop_file(self) -> bool:
        """Check if the stop file exists."""
        try:
            return self.stop_file_path.exists()
        except Exception:
            return False

    def _is_cursor_in_corner(self) -> bool:
        """Check if cursor is within corner_margin_px of any screen corner."""
        if os.name != "nt":
            return False

        try:
            import win32api
            import win32gui

            from local_control.observation.screen import ensure_interactive_desktop

            ensure_interactive_desktop()

            x, y = win32gui.GetCursorPos()
            w = win32api.GetSystemMetrics(0)
            h = win32api.GetSystemMetrics(1)

            if w <= 0 or h <= 0:
                return False

            in_x = x <= self.corner_margin_px or x >= (w - self.corner_margin_px)
            in_y = y <= self.corner_margin_px or y >= (h - self.corner_margin_px)

            return bool(in_x and in_y)
        except Exception as e:
            logger.debug("kill_switch.cursor_check_failed", error=str(e))
            return False

    def _poll_loop(self) -> None:
        """Periodic background loop checking stop conditions."""
        if os.name == "nt":
            try:
                from local_control.observation.screen import ensure_interactive_desktop

                ensure_interactive_desktop()
            except Exception:
                pass

        while self._running.is_set():
            if self.token.is_set():
                break

            # 1. Check stop file
            if self._check_stop_file():
                logger.warning("kill_switch.stop_file_detected", path=str(self.stop_file_path))
                self.token.set("stop_file")
                break

            # 2. Check screen corners
            if self._is_cursor_in_corner():
                now = time.monotonic()
                if self._corner_start_time is None:
                    self._corner_start_time = now
                elif (now - self._corner_start_time) >= self.corner_hold_time_s:
                    logger.warning("kill_switch.corner_failsafe_triggered")
                    self.token.set("corner")
                    break
            else:
                self._corner_start_time = None

            time.sleep(self.poll_interval_s)

    def __enter__(self) -> "KillSwitch":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
