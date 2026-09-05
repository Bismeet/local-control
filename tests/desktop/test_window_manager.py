"""Desktop integration tests for WindowManager on Windows."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from local_control.observation.windows import WindowManager


@pytest.mark.desktop
def test_window_manager_listing_and_foreground() -> None:
    """Verify window listing and foreground detection on active desktop."""
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    wm = WindowManager()
    windows = wm.list_windows()

    assert len(windows) > 0
    for win in windows:
        assert win.handle > 0
        assert len(win.title) > 0
        assert win.bbox.width >= 0
        assert win.bbox.height >= 0

    fg = wm.foreground()
    if fg:
        assert fg.handle > 0
        assert fg.is_foreground is True


@pytest.mark.desktop
def test_window_manager_detects_target_app() -> None:
    """Verify WindowManager detects the running LC Test Target application."""
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    target_app_py = Path(__file__).resolve().parent.parent / "fixtures" / "target_app" / "app.py"
    proc = subprocess.Popen([sys.executable, str(target_app_py)])

    try:
        wm = WindowManager()
        target_windows = []
        for _ in range(25):
            windows = wm.list_windows()
            target_windows = [w for w in windows if "LC Test Target" in w.title]
            if target_windows:
                break
            time.sleep(0.2)

        assert len(target_windows) >= 1

        target_win = target_windows[0]
        assert target_win.bbox.width >= 700  # 800x600 window
        assert target_win.bbox.height >= 500
    finally:
        proc.terminate()
        proc.wait()
