"""Desktop integration tests for ScreenCapture on Windows."""

import ctypes
import os

import pytest

from local_control.observation.screen import ScreenCapture, init_dpi_awareness


@pytest.mark.desktop
def test_screen_capture_primary_monitor() -> None:
    """Verify ScreenCapture captures physical screen pixels matching primary display metrics."""
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    init_dpi_awareness()
    capture = ScreenCapture()
    frame = capture.capture(monitor_index=0)

    assert frame.width > 0
    assert frame.height > 0
    assert len(frame.raw_bytes) == frame.width * frame.height * 4

    # Compare with physical system metrics
    cx = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
    cy = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN

    # In single-monitor setups, frame dimensions equal system metrics
    assert frame.width == cx
    assert frame.height == cy
