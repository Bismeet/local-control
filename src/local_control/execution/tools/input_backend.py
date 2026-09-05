"""Input backend interface, PyAutoGUI implementation, and fake backend for tests."""

import contextlib
import sys
import time
from typing import Any, Protocol, runtime_checkable

import structlog

from local_control.safety.kill_switch import StopToken

logger = structlog.get_logger(__name__)


@runtime_checkable
class InputBackend(Protocol):
    """Low-level OS input injection backend interface."""

    def move_to(self, x: int, y: int, duration_s: float = 0.0) -> None: ...

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None: ...

    def mouse_down(self, x: int, y: int, button: str = "left") -> None: ...

    def mouse_up(self, x: int, y: int, button: str = "left") -> None: ...

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_s: float = 0.5,
        button: str = "left",
    ) -> None: ...

    def scroll(
        self,
        delta_x: int,
        delta_y: int,
        x: int | None = None,
        y: int | None = None,
    ) -> None: ...

    def type_text(self, text: str, stop_token: StopToken | None = None) -> None: ...

    def press_key(self, key: str) -> None: ...

    def press_keys(self, keys: list[str]) -> None: ...

    def key_down(self, key: str) -> None: ...

    def key_up(self, key: str) -> None: ...

    def release_all(self) -> None: ...


KEY_MAP = {
    "control": "ctrl",
    "super": "win",
    "windows": "win",
    "command": "win",
    "cmd": "win",
    "return": "enter",
    "esc": "escape",
    "del": "delete",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "ins": "insert",
}


def normalize_key(key: str) -> str:
    """Normalize common key name aliases to PyAutoGUI key names."""
    k = key.strip().lower()
    return KEY_MAP.get(k, k)


class PyAutoGuiBackend:
    """Windows input injection backend using PyAutoGUI and win32clipboard for Unicode."""

    def __init__(self) -> None:
        import pyautogui

        # Disable pyautogui's internal failsafe since KillSwitch manages corner detection cleanly
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.05
        self._pyautogui = pyautogui

    def move_to(self, x: int, y: int, duration_s: float = 0.0) -> None:
        self._pyautogui.moveTo(x, y, duration=duration_s)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self._pyautogui.click(x=x, y=y, clicks=clicks, button=button)

    def mouse_down(self, x: int, y: int, button: str = "left") -> None:
        self._pyautogui.mouseDown(x=x, y=y, button=button)

    def mouse_up(self, x: int, y: int, button: str = "left") -> None:
        self._pyautogui.mouseUp(x=x, y=y, button=button)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_s: float = 0.5,
        button: str = "left",
    ) -> None:
        self._pyautogui.moveTo(start_x, start_y)
        self._pyautogui.dragTo(end_x, end_y, duration=duration_s, button=button)

    def scroll(
        self,
        delta_x: int,
        delta_y: int,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        # PyAutoGUI scroll accepts positive for up, negative for down
        if x is not None and y is not None:
            self._pyautogui.moveTo(x, y)
        if delta_y != 0:
            self._pyautogui.scroll(delta_y)
        if delta_x != 0 and hasattr(self._pyautogui, "hscroll"):
            self._pyautogui.hscroll(delta_x)

    def type_text(self, text: str, stop_token: StopToken | None = None) -> None:
        """Type text into focused element, checking stop token every 50 characters."""
        if not text:
            return

        # Check StopToken before starting
        if stop_token:
            stop_token.check()

        # For non-ASCII or multi-character Unicode strings, use Windows clipboard paste
        has_non_ascii = any(ord(c) >= 128 for c in text)

        if has_non_ascii and sys.platform == "win32":
            self._paste_unicode_text(text, stop_token=stop_token)
        else:
            # Type in chunks of 50 chars, checking stop_token between chunks
            chunk_size = 50
            for i in range(0, len(text), chunk_size):
                if stop_token:
                    stop_token.check()
                chunk = text[i : i + chunk_size]
                self._pyautogui.write(chunk, interval=0.01)

        if stop_token:
            stop_token.check()

    def _paste_unicode_text(self, text: str, stop_token: StopToken | None = None) -> None:
        """Paste Unicode text via Windows clipboard, with state backup and restoration."""
        if sys.platform != "win32":
            self._pyautogui.write(text)
            return

        import win32clipboard
        import win32con

        old_clipboard: str | None = None
        # Try to backup previous clipboard text
        try:
            win32clipboard.OpenClipboard(0)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                old_clipboard = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except Exception:
            with contextlib.suppress(Exception):
                win32clipboard.CloseClipboard()

        # Set target text on clipboard
        try:
            win32clipboard.OpenClipboard(0)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
        except Exception as e:
            with contextlib.suppress(Exception):
                win32clipboard.CloseClipboard()
            logger.error("paste_unicode_text.set_failed", error=str(e))
            self._pyautogui.write(text)
            return

        if stop_token:
            stop_token.check()

        # Send Ctrl+V
        self._pyautogui.hotkey("ctrl", "v")
        time.sleep(0.05)

        # Restore previous clipboard text if there was any
        if old_clipboard is not None:
            try:
                win32clipboard.OpenClipboard(0)
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, old_clipboard)
                win32clipboard.CloseClipboard()
            except Exception:
                with contextlib.suppress(Exception):
                    win32clipboard.CloseClipboard()

    def press_key(self, key: str) -> None:
        k = normalize_key(key)
        self._pyautogui.press(k)

    def press_keys(self, keys: list[str]) -> None:
        norm_keys = [normalize_key(k) for k in keys]
        self._pyautogui.hotkey(*norm_keys)

    def key_down(self, key: str) -> None:
        k = normalize_key(key)
        self._pyautogui.keyDown(k)

    def key_up(self, key: str) -> None:
        k = normalize_key(key)
        self._pyautogui.keyUp(k)

    def release_all(self) -> None:
        """Safety release of any held mouse buttons or modifier keys."""
        for btn in ("left", "right", "middle"):
            with contextlib.suppress(Exception):
                self._pyautogui.mouseUp(button=btn)
        for k in ("ctrl", "alt", "shift", "win"):
            with contextlib.suppress(Exception):
                self._pyautogui.keyUp(k)


class FakeInputBackend:
    """In-memory fake input backend recording calls for unit testing and CI."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def move_to(self, x: int, y: int, duration_s: float = 0.0) -> None:
        self.calls.append(("move_to", {"x": x, "y": y, "duration_s": duration_s}))

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self.calls.append(("click", {"x": x, "y": y, "button": button, "clicks": clicks}))

    def mouse_down(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("mouse_down", {"x": x, "y": y, "button": button}))

    def mouse_up(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("mouse_up", {"x": x, "y": y, "button": button}))

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_s: float = 0.5,
        button: str = "left",
    ) -> None:
        self.calls.append(
            (
                "drag",
                {
                    "start_x": start_x,
                    "start_y": start_y,
                    "end_x": end_x,
                    "end_y": end_y,
                    "duration_s": duration_s,
                    "button": button,
                },
            )
        )

    def scroll(
        self,
        delta_x: int,
        delta_y: int,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        self.calls.append(("scroll", {"delta_x": delta_x, "delta_y": delta_y, "x": x, "y": y}))

    def type_text(self, text: str, stop_token: StopToken | None = None) -> None:
        if stop_token:
            stop_token.check()
        # Simulate check every 50 chars
        chunk_size = 50
        for _i in range(0, len(text), chunk_size):
            if stop_token:
                stop_token.check()
        self.calls.append(("type_text", {"text": text}))

    def press_key(self, key: str) -> None:
        self.calls.append(("press_key", {"key": normalize_key(key)}))

    def press_keys(self, keys: list[str]) -> None:
        self.calls.append(("press_keys", {"keys": [normalize_key(k) for k in keys]}))

    def key_down(self, key: str) -> None:
        self.calls.append(("key_down", {"key": normalize_key(key)}))

    def key_up(self, key: str) -> None:
        self.calls.append(("key_up", {"key": normalize_key(key)}))

    def release_all(self) -> None:
        self.calls.append(("release_all", {}))


from local_control.execution.tools.sendinput_backend import SendInputBackend  # noqa: E402

__all__ = [
    "FakeInputBackend",
    "InputBackend",
    "PyAutoGuiBackend",
    "SendInputBackend",
    "normalize_key",
]
