"""Desktop tests verifying WindowTool operations against LC Test Target on Windows."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from local_control.config.settings import Settings
from local_control.core.actions import (
    CloseWindowAction,
    FocusWindowAction,
    ListWindowsAction,
)
from local_control.execution.executor import Executor
from local_control.execution.tools.base import ExecutionContext
from local_control.execution.tools.window_tool import WindowTool
from local_control.observation.observer import Observer
from local_control.observation.screen import init_dpi_awareness
from local_control.observation.windows import WindowManager
from local_control.safety.kill_switch import StopToken


@pytest.mark.desktop
@pytest.mark.asyncio
async def test_window_tool_focus_and_close() -> None:
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    init_dpi_awareness()
    target_app_py = Path(__file__).resolve().parent.parent / "fixtures" / "target_app" / "app.py"

    proc = subprocess.Popen([sys.executable, str(target_app_py)])
    try:
        wm = WindowManager()
        target_win = None
        for _ in range(25):
            windows = wm.list_windows()
            target_win = next((w for w in windows if "LC Test Target" in w.title), None)
            if target_win is not None:
                break
            time.sleep(0.2)
        assert target_win is not None, "Target app window not found"

        hwnd = target_win.handle
        tool = WindowTool()
        executor = Executor(tools=[tool])
        ctx = ExecutionContext(
            run_id="test-win",
            stop=StopToken(),
            settings=Settings.load(),
        )

        # 1. Focus window
        focus_act = FocusWindowAction(
            handle=hwnd,
            target_description="LC Test Target",
            expected_outcome="Target app is foreground",
            settle_ms=200,
        )
        res = await executor.execute(focus_act, ctx)
        assert res.success

        # Verify postcondition with observer
        observer = Observer()
        obs = observer.observe()
        post_check = await tool.postcondition(focus_act, res, obs)
        assert post_check is not None
        assert post_check["passed"] is True
        assert post_check["actual_handle"] == hwnd

        # 2. List windows
        list_act = ListWindowsAction(
            target_description="all windows",
            expected_outcome="windows listed",
            settle_ms=0,
        )
        list_res = await executor.execute(list_act, ctx)
        assert list_res.success
        assert list_res.data["count"] >= 1

        # 3. Close window
        close_act = CloseWindowAction(
            handle=hwnd,
            target_description="close target",
            expected_outcome="target window closed",
            settle_ms=400,
        )
        close_res = await executor.execute(close_act, ctx)
        assert close_res.success

        time.sleep(0.5)
        windows_after = wm.list_windows()
        assert not any(w.handle == hwnd for w in windows_after)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            pass
