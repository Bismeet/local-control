"""Window management tool adapter."""

import asyncio
import sys
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from local_control.core.actions import (
    Action,
    CloseWindowAction,
    FocusWindowAction,
    ListWindowsAction,
)
from local_control.core.types import ActionResult, ErrorInfo, Observation
from local_control.execution.tools.base import ExecutionContext, Tool

logger = structlog.get_logger(__name__)


class WindowTool(Tool):
    """Executes window focus, close, and listing operations."""

    @property
    def handles(self) -> frozenset[str]:
        return frozenset(
            {
                "focus_window",
                "close_window",
                "list_windows",
            }
        )

    def _focus_hwnd_win32(self, hwnd: int) -> None:
        """Reliably bring a window to the foreground on Windows."""
        if sys.platform != "win32":
            return

        import win32api
        import win32con
        import win32gui
        import win32process

        from local_control.observation.windows import ensure_interactive_desktop

        ensure_interactive_desktop()

        if not win32gui.IsWindow(hwnd):
            raise ValueError(f"Invalid window handle: {hwnd}")

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # Send momentary Alt key event to unlock SetForegroundWindow permission
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

        cur_fg = win32gui.GetForegroundWindow()
        if cur_fg != hwnd and cur_fg != 0:
            fg_thread = win32process.GetWindowThreadProcessId(cur_fg)[0]
            cur_thread = win32api.GetCurrentThreadId()
            attached = False
            try:
                if fg_thread != cur_thread:
                    win32process.AttachThreadInput(cur_thread, fg_thread, True)
                    attached = True
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
            finally:
                if attached:
                    win32process.AttachThreadInput(cur_thread, fg_thread, False)
        else:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)

    def _close_hwnd_win32(self, hwnd: int) -> None:
        """Send WM_CLOSE to window on Windows."""
        if sys.platform != "win32":
            return

        import win32con
        import win32gui

        if not win32gui.IsWindow(hwnd):
            raise ValueError(f"Invalid window handle: {hwnd}")

        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        try:
            ctx.stop.check()
            data: dict[str, Any] = {}

            if isinstance(action, FocusWindowAction):
                await asyncio.to_thread(self._focus_hwnd_win32, action.handle)
                data = {"handle": action.handle}
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, CloseWindowAction):
                await asyncio.to_thread(self._close_hwnd_win32, action.handle)
                data = {"handle": action.handle}
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, ListWindowsAction):
                from local_control.observation.windows import WindowManager

                wm = WindowManager()
                windows = await asyncio.to_thread(wm.list_windows)
                data = {
                    "count": len(windows),
                    "windows": [w.model_dump() for w in windows],
                }

            else:
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="UNSUPPORTED_ACTION",
                        message=f"WindowTool does not support action {action.type}",
                    ),
                )

            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type=action.type,
                success=True,
                started_at=started_at,
                duration_ms=duration_ms,
                data=data,
            )

        except Exception as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            logger.error("window_tool.execution_failed", error=str(e), action_type=action.type)
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="WINDOW_OPERATION_FAILED", message=str(e)),
            )

    async def postcondition(
        self, action: Action, result: ActionResult, obs_after: Observation
    ) -> Any | None:
        """Verify window focus postconditions."""
        if isinstance(action, FocusWindowAction) and result.success:
            fg = obs_after.foreground
            passed = fg is not None and fg.handle == action.handle
            return {
                "check": "foreground_window_matches",
                "passed": passed,
                "expected_handle": action.handle,
                "actual_handle": fg.handle if fg else None,
            }
        return None
