"""Windows SendInput backend for low-level mouse and keyboard input injection."""

import ctypes
import os
import time
from ctypes import wintypes
from typing import Any

import structlog

from local_control.execution.tools.input_backend import normalize_key
from local_control.safety.kill_switch import StopToken

logger = structlog.get_logger(__name__)

# --- Windows Input Structures & Constants ---

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

# Mouse flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

WHEEL_DELTA = 120

# Virtual Key Codes
VK_CODES: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "clear": 0x0C,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "select": 0x29,
    "print": 0x2A,
    "printscreen": 0x2C,
    "prtscr": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "help": 0x2F,
    "win": 0x5B,
    "super": 0x5B,
    "cmd": 0x5B,
    "apps": 0x5D,
    "sleep": 0x5F,
    "numpad0": 0x60,
    "numpad1": 0x61,
    "numpad2": 0x62,
    "numpad3": 0x63,
    "numpad4": 0x64,
    "numpad5": 0x65,
    "numpad6": 0x66,
    "numpad7": 0x67,
    "numpad8": 0x68,
    "numpad9": 0x69,
    "multiply": 0x6A,
    "add": 0x6B,
    "separator": 0x6C,
    "subtract": 0x6D,
    "decimal": 0x6E,
    "divide": 0x6F,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "numlock": 0x90,
    "scrolllock": 0x91,
}

# Add 0-9 and a-z
for i in range(10):
    VK_CODES[str(i)] = 0x30 + i
for c in "abcdefghijklmnopqrstuvwxyz":
    VK_CODES[c] = 0x41 + (ord(c) - ord("a"))


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


class SendInputBackend:
    """Low-level Windows SendInput implementation of the InputBackend protocol."""

    def __init__(self) -> None:
        self.held_keys: set[int] = set()
        self.held_buttons: set[str] = set()
        self._user32: Any = None
        if os.name == "nt":
            self._user32 = ctypes.windll.user32
            self._user32.SendInput.argtypes = [
                wintypes.UINT,
                ctypes.POINTER(INPUT),
                ctypes.c_int,
            ]
            self._user32.SendInput.restype = wintypes.UINT

    def _get_virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        """Return (left, top, width, height) of virtual desktop spanning all monitors."""
        if not self._user32:
            return 0, 0, 1920, 1080
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        vleft = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vtop = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vwidth = self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vheight = self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vwidth <= 0:
            vwidth = 1920
        if vheight <= 0:
            vheight = 1080
        return vleft, vtop, vwidth, vheight

    def _normalize_coords(self, x: int, y: int) -> tuple[int, int]:
        """Convert physical screen pixels to normalized absolute coordinates (0..65535)."""
        vleft, vtop, vwidth, vheight = self._get_virtual_screen_bounds()
        norm_x = int(((x - vleft) * 65535) / (vwidth - 1)) if vwidth > 1 else 0
        norm_y = int(((y - vtop) * 65535) / (vheight - 1)) if vheight > 1 else 0
        return norm_x, norm_y

    def _send_inputs(self, inputs: list[INPUT]) -> int:
        """Call SendInput for a list of INPUT structures."""
        if not self._user32 or not inputs:
            return 0
        n = len(inputs)
        arr = (INPUT * n)(*inputs)
        sent = self._user32.SendInput(n, arr, ctypes.sizeof(INPUT))
        return int(sent)

    def cursor_position(self) -> tuple[int, int]:
        """Get current cursor position in screen coordinates."""
        if not self._user32:
            return 0, 0
        pt = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def move_to(self, x: int, y: int, duration_s: float = 0.0) -> None:
        """Move cursor to target screen coordinate."""
        if duration_s <= 0.0:
            norm_x, norm_y = self._normalize_coords(x, y)
            inp = INPUT(
                type=INPUT_MOUSE,
                u=_INPUT_UNION(
                    mi=MOUSEINPUT(
                        dx=norm_x,
                        dy=norm_y,
                        mouseData=0,
                        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            self._send_inputs([inp])
            if self._user32:
                self._user32.SetCursorPos(x, y)
        else:
            cur_x, cur_y = self.cursor_position()
            steps = max(1, int(duration_s / 0.015))
            sleep_interval = duration_s / steps
            for i in range(1, steps + 1):
                t = i / steps
                interp_x = round(cur_x + (x - cur_x) * t)
                interp_y = round(cur_y + (y - cur_y) * t)
                norm_x, norm_y = self._normalize_coords(interp_x, interp_y)
                inp = INPUT(
                    type=INPUT_MOUSE,
                    u=_INPUT_UNION(
                        mi=MOUSEINPUT(
                            dx=norm_x,
                            dy=norm_y,
                            mouseData=0,
                            dwFlags=MOUSEEVENTF_MOVE
                            | MOUSEEVENTF_ABSOLUTE
                            | MOUSEEVENTF_VIRTUALDESK,
                            time=0,
                            dwExtraInfo=0,
                        )
                    ),
                )
                self._send_inputs([inp])
                if self._user32:
                    self._user32.SetCursorPos(interp_x, interp_y)
                time.sleep(sleep_interval)

    def mouse_down(self, x: int, y: int, button: str = "left") -> None:
        """Press down a mouse button at specified screen coordinate."""
        self.move_to(x, y)
        norm_x, norm_y = self._normalize_coords(x, y)
        flag = MOUSEEVENTF_LEFTDOWN
        if button == "right":
            flag = MOUSEEVENTF_RIGHTDOWN
        elif button == "middle":
            flag = MOUSEEVENTF_MIDDLEDOWN

        inp = INPUT(
            type=INPUT_MOUSE,
            u=_INPUT_UNION(
                mi=MOUSEINPUT(
                    dx=norm_x,
                    dy=norm_y,
                    mouseData=0,
                    dwFlags=flag | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        self._send_inputs([inp])
        self.held_buttons.add(button)

    def mouse_up(self, x: int, y: int, button: str = "left") -> None:
        """Release a mouse button at specified screen coordinate."""
        self.move_to(x, y)
        norm_x, norm_y = self._normalize_coords(x, y)
        flag = MOUSEEVENTF_LEFTUP
        if button == "right":
            flag = MOUSEEVENTF_RIGHTUP
        elif button == "middle":
            flag = MOUSEEVENTF_MIDDLEUP

        inp = INPUT(
            type=INPUT_MOUSE,
            u=_INPUT_UNION(
                mi=MOUSEINPUT(
                    dx=norm_x,
                    dy=norm_y,
                    mouseData=0,
                    dwFlags=flag | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        self._send_inputs([inp])
        self.held_buttons.discard(button)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        """Send one or more mouse clicks at specified screen coordinate."""
        self.move_to(x, y)
        norm_x, norm_y = self._normalize_coords(x, y)
        down_flag = MOUSEEVENTF_LEFTDOWN
        up_flag = MOUSEEVENTF_LEFTUP
        if button == "right":
            down_flag = MOUSEEVENTF_RIGHTDOWN
            up_flag = MOUSEEVENTF_RIGHTUP
        elif button == "middle":
            down_flag = MOUSEEVENTF_MIDDLEDOWN
            up_flag = MOUSEEVENTF_MIDDLEUP

        for i in range(clicks):
            down = INPUT(
                type=INPUT_MOUSE,
                u=_INPUT_UNION(
                    mi=MOUSEINPUT(
                        dx=norm_x,
                        dy=norm_y,
                        mouseData=0,
                        dwFlags=down_flag | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            up = INPUT(
                type=INPUT_MOUSE,
                u=_INPUT_UNION(
                    mi=MOUSEINPUT(
                        dx=norm_x,
                        dy=norm_y,
                        mouseData=0,
                        dwFlags=up_flag | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            self._send_inputs([down, up])
            if i < clicks - 1:
                time.sleep(0.05)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_s: float = 0.5,
        button: str = "left",
    ) -> None:
        """Drag cursor from start coordinate to end coordinate while holding button."""
        self.move_to(start_x, start_y)
        self.mouse_down(start_x, start_y, button=button)
        time.sleep(0.05)
        self.move_to(end_x, end_y, duration_s=duration_s)
        time.sleep(0.05)
        self.mouse_up(end_x, end_y, button=button)

    def scroll(
        self,
        delta_x: int,
        delta_y: int,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Scroll mouse wheel vertically and/or horizontally."""
        if x is not None and y is not None:
            self.move_to(x, y)

        inputs: list[INPUT] = []
        if delta_y != 0:
            wheel_amount = delta_y * WHEEL_DELTA
            inputs.append(
                INPUT(
                    type=INPUT_MOUSE,
                    u=_INPUT_UNION(
                        mi=MOUSEINPUT(
                            dx=0,
                            dy=0,
                            mouseData=wintypes.DWORD(wheel_amount & 0xFFFFFFFF),
                            dwFlags=MOUSEEVENTF_WHEEL,
                            time=0,
                            dwExtraInfo=0,
                        )
                    ),
                )
            )

        if delta_x != 0:
            hwheel_amount = delta_x * WHEEL_DELTA
            inputs.append(
                INPUT(
                    type=INPUT_MOUSE,
                    u=_INPUT_UNION(
                        mi=MOUSEINPUT(
                            dx=0,
                            dy=0,
                            mouseData=wintypes.DWORD(hwheel_amount & 0xFFFFFFFF),
                            dwFlags=MOUSEEVENTF_HWHEEL,
                            time=0,
                            dwExtraInfo=0,
                        )
                    ),
                )
            )

        if inputs:
            self._send_inputs(inputs)

    def type_text(self, text: str, stop_token: StopToken | None = None) -> None:
        """Type Unicode string using KEYEVENTF_UNICODE."""
        for char in text:
            if stop_token is not None:
                stop_token.check()

            codepoint = ord(char)
            if char == "\n":
                self.press_key("enter")
                continue
            elif char == "\r":
                continue
            elif char == "\t":
                self.press_key("tab")
                continue

            down = INPUT(
                type=INPUT_KEYBOARD,
                u=_INPUT_UNION(
                    ki=KEYBDINPUT(
                        wVk=0,
                        wScan=codepoint,
                        dwFlags=KEYEVENTF_UNICODE,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            up = INPUT(
                type=INPUT_KEYBOARD,
                u=_INPUT_UNION(
                    ki=KEYBDINPUT(
                        wVk=0,
                        wScan=codepoint,
                        dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            self._send_inputs([down, up])
            time.sleep(0.01)

    def key_down(self, key: str) -> None:
        """Hold down a single key by alias or character."""
        k = normalize_key(key).lower()
        vk = VK_CODES.get(k)
        if vk is None:
            if len(k) == 1:
                vk = ord(k.upper())
            else:
                logger.warning("sendinput.unknown_key_down", key=key)
                return

        inp = INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=vk,
                    wScan=0,
                    dwFlags=0,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        self._send_inputs([inp])
        self.held_keys.add(vk)

    def key_up(self, key: str) -> None:
        """Release a single key by alias or character."""
        k = normalize_key(key).lower()
        vk = VK_CODES.get(k)
        if vk is None:
            if len(k) == 1:
                vk = ord(k.upper())
            else:
                logger.warning("sendinput.unknown_key_up", key=key)
                return

        inp = INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=vk,
                    wScan=0,
                    dwFlags=KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        self._send_inputs([inp])
        self.held_keys.discard(vk)

    def press_key(self, key: str) -> None:
        """Press and immediately release a single key."""
        self.key_down(key)
        time.sleep(0.02)
        self.key_up(key)

    def press_keys(self, keys: list[str]) -> None:
        """Press key combination sequence down in order, then release in reverse order."""
        for k in keys:
            self.key_down(k)
            time.sleep(0.01)
        time.sleep(0.03)
        for k in reversed(keys):
            self.key_up(k)
            time.sleep(0.01)

    def release_all(self) -> None:
        """Release all currently held keys and mouse buttons."""
        for vk in list(self.held_keys):
            inp = INPUT(
                type=INPUT_KEYBOARD,
                u=_INPUT_UNION(
                    ki=KEYBDINPUT(
                        wVk=vk,
                        wScan=0,
                        dwFlags=KEYEVENTF_KEYUP,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            self._send_inputs([inp])
        self.held_keys.clear()

        cur_x, cur_y = self.cursor_position()
        norm_x, norm_y = self._normalize_coords(cur_x, cur_y)
        for btn in list(self.held_buttons):
            up_flag = MOUSEEVENTF_LEFTUP
            if btn == "right":
                up_flag = MOUSEEVENTF_RIGHTUP
            elif btn == "middle":
                up_flag = MOUSEEVENTF_MIDDLEUP
            inp = INPUT(
                type=INPUT_MOUSE,
                u=_INPUT_UNION(
                    mi=MOUSEINPUT(
                        dx=norm_x,
                        dy=norm_y,
                        mouseData=0,
                        dwFlags=up_flag | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )
            self._send_inputs([inp])
        self.held_buttons.clear()
