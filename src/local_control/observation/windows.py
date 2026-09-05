"""Window enumeration and foreground window detection for Windows."""

import ctypes
import os

import psutil
import structlog

from local_control.core.actions import Rect
from local_control.core.types import WindowInfo

logger = structlog.get_logger(__name__)


def ensure_interactive_desktop() -> None:
    """Ensure the calling thread is attached to the interactive 'Default' desktop on Windows."""
    if os.name == "nt":
        try:
            # Check current desktop name
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
                # DESKTOP_ALL_ACCESS = 0x01FF
                default_desk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if default_desk:
                    ctypes.windll.user32.SetThreadDesktop(default_desk)
        except Exception as e:
            logger.debug("windows.attach_desktop_failed", error=str(e))


class WindowManager:
    """Manages top-level window enumeration and inspection on Windows."""

    def __init__(self) -> None:
        ensure_interactive_desktop()

    def is_elevated(self, handle: int) -> bool | None:
        """Best-effort check whether the process owning a window is running elevated."""
        if os.name != "nt":
            return None

        try:
            import win32process
            import win32security

            _, pid = win32process.GetWindowThreadProcessId(handle)
            if pid <= 0:
                return None

            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            proc_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not proc_handle:
                return None

            try:
                token_handle = win32security.OpenProcessToken(
                    proc_handle, win32security.TOKEN_QUERY
                )
                elevation = win32security.GetTokenInformation(
                    token_handle, win32security.TokenElevation
                )
                return bool(elevation)
            finally:
                ctypes.windll.kernel32.CloseHandle(proc_handle)
        except Exception:
            return None

    def _get_window_info(self, hwnd: int, foreground_hwnd: int) -> WindowInfo | None:
        """Extract WindowInfo metadata for a given window handle."""
        try:
            import win32gui
            import win32process

            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return None

            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return None

            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            width = max(0, right - left)
            height = max(0, bottom - top)

            # Skip 0-size invisible helper windows
            if width == 0 and height == 0:
                return None

            is_minimized = bool(win32gui.IsIconic(hwnd))
            is_foreground = hwnd == foreground_hwnd

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = "unknown"
            if pid > 0:
                try:
                    process_name = psutil.Process(pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    process_name = "unknown"

            elevated = self.is_elevated(hwnd)

            return WindowInfo(
                handle=hwnd,
                title=title,
                process_name=process_name,
                pid=pid,
                bbox=Rect(x=left, y=top, width=width, height=height),
                is_foreground=is_foreground,
                is_minimized=is_minimized,
                is_elevated=elevated,
            )
        except Exception as e:
            logger.debug("windows.get_info_error", hwnd=hwnd, error=str(e))
            return None

    def list_windows(self) -> list[WindowInfo]:
        """Enumerate all visible top-level windows."""
        if os.name != "nt":
            return []

        ensure_interactive_desktop()
        import win32gui

        foreground_hwnd = win32gui.GetForegroundWindow()
        windows: list[WindowInfo] = []

        def enum_callback(hwnd: int, _: int) -> bool:
            info = self._get_window_info(hwnd, foreground_hwnd)
            if info is not None:
                windows.append(info)
            return True

        try:
            win32gui.EnumWindows(enum_callback, 0)
        except Exception as e:
            logger.error("windows.enum_error", error=str(e))

        return windows

    def foreground(self) -> WindowInfo | None:
        """Return WindowInfo for the current foreground window."""
        if os.name != "nt":
            return None

        ensure_interactive_desktop()
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        return self._get_window_info(hwnd, hwnd)
