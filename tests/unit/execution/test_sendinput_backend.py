"""Unit tests for Windows SendInputBackend."""

import os
from unittest.mock import MagicMock, patch

from local_control.execution.tools.sendinput_backend import (
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    VK_CODES,
    SendInputBackend,
)
from local_control.safety.kill_switch import StopToken


def test_sendinput_backend_initialization() -> None:
    backend = SendInputBackend()
    assert isinstance(backend.held_keys, set)
    assert isinstance(backend.held_buttons, set)
    if os.name == "nt":
        assert backend._user32 is not None


def test_virtual_screen_bounds_and_coord_normalization() -> None:
    backend = SendInputBackend()
    # Mock _get_virtual_screen_bounds
    with patch.object(backend, "_get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080)):
        # (0, 0) should normalize to (0, 0)
        nx, ny = backend._normalize_coords(0, 0)
        assert nx == 0
        assert ny == 0

        # (1919, 1079) should normalize to (65535, 65535)
        nx, ny = backend._normalize_coords(1919, 1079)
        assert nx == 65535
        assert ny == 65535

        # (960, 540) should normalize close to mid-point 32768
        nx, ny = backend._normalize_coords(960, 540)
        assert 32000 < nx < 33500
        assert 32000 < ny < 33500


def test_virtual_screen_with_negative_offset() -> None:
    backend = SendInputBackend()
    # Secondary monitor to the left: virtual desktop starts at x=-1920
    with patch.object(backend, "_get_virtual_screen_bounds", return_value=(-1920, 0, 3840, 1080)):
        nx, ny = backend._normalize_coords(-1920, 0)
        assert nx == 0
        assert ny == 0

        nx, ny = backend._normalize_coords(0, 0)
        assert 32000 < nx < 33500


def test_move_to_dispatches_mouse_input() -> None:
    backend = SendInputBackend()
    mock_send = MagicMock(return_value=1)
    backend._send_inputs = mock_send

    backend.move_to(500, 400, duration_s=0.0)

    assert mock_send.call_count == 1
    inputs = mock_send.call_args[0][0]
    assert len(inputs) == 1
    assert inputs[0].type == INPUT_MOUSE
    assert (
        inputs[0].u.mi.dwFlags == MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    )


def test_click_sends_down_and_up() -> None:
    backend = SendInputBackend()
    mock_send = MagicMock(return_value=2)
    backend._send_inputs = mock_send

    backend.click(300, 200, button="left", clicks=1)

    assert mock_send.call_count == 2
    inputs = mock_send.call_args[0][0]
    assert len(inputs) == 2  # down and up
    assert (
        inputs[0].u.mi.dwFlags
        == MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    )
    assert (
        inputs[1].u.mi.dwFlags
        == MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    )


def test_scroll_dispatches_wheel_input() -> None:
    backend = SendInputBackend()
    mock_send = MagicMock(return_value=1)
    backend._send_inputs = mock_send

    backend.scroll(delta_x=0, delta_y=3)

    assert mock_send.call_count == 1
    inputs = mock_send.call_args[0][0]
    assert len(inputs) == 1
    assert inputs[0].u.mi.dwFlags == MOUSEEVENTF_WHEEL
    assert inputs[0].u.mi.mouseData == 3 * 120


def test_type_text_emits_unicode_inputs() -> None:
    backend = SendInputBackend()
    mock_send = MagicMock(return_value=2)
    backend._send_inputs = mock_send

    backend.type_text("Hi", stop_token=StopToken())

    # 'H' and 'i' each produce a down and up input pair
    assert mock_send.call_count == 2
    h_inputs = mock_send.call_args_list[0][0][0]
    assert len(h_inputs) == 2
    assert h_inputs[0].type == INPUT_KEYBOARD
    assert h_inputs[0].u.ki.wScan == ord("H")
    assert h_inputs[0].u.ki.dwFlags == KEYEVENTF_UNICODE
    assert h_inputs[1].u.ki.dwFlags == KEYEVENTF_UNICODE | KEYEVENTF_KEYUP


def test_press_keys_down_and_up_in_reverse_order() -> None:
    backend = SendInputBackend()
    recorded_ops: list[str] = []

    def mock_key_down(k: str) -> None:
        recorded_ops.append(f"down_{k}")

    def mock_key_up(k: str) -> None:
        recorded_ops.append(f"up_{k}")

    backend.key_down = mock_key_down  # type: ignore
    backend.key_up = mock_key_up  # type: ignore

    backend.press_keys(["ctrl", "shift", "s"])

    assert recorded_ops == [
        "down_ctrl",
        "down_shift",
        "down_s",
        "up_s",
        "up_shift",
        "up_ctrl",
    ]


def test_release_all_clears_tracked_keys_and_buttons() -> None:
    backend = SendInputBackend()
    backend.held_keys = {VK_CODES["ctrl"], VK_CODES["shift"]}
    backend.held_buttons = {"left"}
    mock_send = MagicMock(return_value=1)
    backend._send_inputs = mock_send

    backend.release_all()

    assert len(backend.held_keys) == 0
    assert len(backend.held_buttons) == 0
    assert mock_send.call_count >= 2
