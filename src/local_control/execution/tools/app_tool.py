"""Application launching and focusing tool implementing the 7-level targeting hierarchy."""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime

import structlog

from local_control.core.actions import Action, AppTarget, OpenApplicationAction
from local_control.core.types import ActionResult, ErrorInfo, WindowInfo
from local_control.execution.app_target import (
    find_app_path_in_registry,
    find_start_menu_shortcut,
    get_known_app_def,
    resolve_app_target,
)
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.observation.windows import WindowManager

logger = structlog.get_logger(__name__)


class AppTool(Tool):
    """Executes robust application launching and focusing with in-flight verification."""

    def __init__(self, window_manager: WindowManager | None = None) -> None:
        self.wm = window_manager or WindowManager()

    @property
    def handles(self) -> frozenset[str]:
        return frozenset({"open_application"})

    def _is_foreground_match(self, target: AppTarget, fg: WindowInfo | None) -> bool:
        """Check if current foreground window matches target application."""
        if not fg:
            return False

        # 1. Process name match
        if target.process_name and fg.process_name and fg.process_name != "unknown":
            t_proc = target.process_name.lower().replace(".exe", "")
            f_proc = fg.process_name.lower().replace(".exe", "")
            if t_proc == f_proc:
                return True

        # 2. Window title regex / substring match
        if target.window_title_pattern and fg.title:
            try:
                if re.search(target.window_title_pattern, fg.title, re.IGNORECASE):
                    return True
            except re.error:
                pass

        # 3. Target name in title
        return bool(target.name and fg.title and target.name.lower() in fg.title.lower())

    def _find_matching_window(self, target: AppTarget, windows: list[WindowInfo]) -> WindowInfo | None:
        """Find any visible or minimized window matching target application."""
        for win in windows:
            if self._is_foreground_match(target, win):
                return win
        return None

    def _focus_hwnd(self, hwnd: int) -> bool:
        """Bring window to foreground using Windows APIs."""
        if sys.platform != "win32":
            return False

        try:
            import win32api
            import win32con
            import win32gui
            import win32process

            if not win32gui.IsWindow(hwnd):
                return False

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            # Unlock SetForegroundWindow permission via Alt key simulation
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

            return True
        except Exception as err:
            logger.debug("app_tool.focus_hwnd_failed", hwnd=hwnd, error=str(err))
            return False

    def _find_taskbar_button(self, target: AppTarget) -> tuple[int, int] | None:
        """Enumerate taskbar items dynamically and locate target element coordinates."""
        if sys.platform != "win32":
            return None

        try:
            import win32gui
            from pywinauto.uia_element_info import UIAElementInfo

            h_taskbar = win32gui.FindWindow("Shell_TrayWnd", None)
            if not h_taskbar:
                return None

            root = UIAElementInfo(h_taskbar)
            norm_name = target.name.lower()

            for child in root.children():
                name = str(getattr(child, "name", "") or "").lower()
                rect = getattr(child, "rectangle", None)
                if rect and norm_name in name:
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    return cx, cy

                # Check grandchild buttons
                for gc in child.children():
                    gc_name = str(getattr(gc, "name", "") or "").lower()
                    gc_rect = getattr(gc, "rectangle", None)
                    if gc_rect and norm_name in gc_name:
                        cx = (gc_rect.left + gc_rect.right) // 2
                        cy = (gc_rect.top + gc_rect.bottom) // 2
                        return cx, cy
        except Exception as e:
            logger.debug("app_tool.taskbar_enum_failed", error=str(e))

        return None

    async def _launch_executable(self, target: AppTarget) -> str | None:
        """Try launching application via protocol URI, known paths, registry, or shortcut."""
        known_def = get_known_app_def(target.name)

        # 1. Try protocol URI (e.g. discord://, spotify:)
        if known_def and known_def.protocol_uri:
            try:
                if sys.platform == "win32":
                    os.startfile(known_def.protocol_uri)  # type: ignore[attr-defined]
                    return f"protocol_uri:{known_def.protocol_uri}"
            except Exception as e:
                logger.debug("app_tool.protocol_launch_failed", uri=known_def.protocol_uri, error=str(e))

        # 2. Known path patterns
        if known_def:
            for pat in known_def.known_path_patterns:
                expanded = os.path.expandvars(pat)
                matches = glob.glob(expanded)
                if matches:
                    exe_path = matches[0]
                    try:
                        args = [exe_path] + known_def.launch_args
                        subprocess.Popen(args, close_fds=True)
                        return f"known_path:{exe_path}"
                    except Exception as e:
                        logger.debug("app_tool.known_path_launch_failed", path=exe_path, error=str(e))

        # 3. Start Menu Shortcut
        shortcut = find_start_menu_shortcut(target.name)
        if shortcut and sys.platform == "win32":
            try:
                os.startfile(shortcut)  # type: ignore[attr-defined]
                return f"start_menu:{shortcut}"
            except Exception as e:
                logger.debug("app_tool.start_menu_launch_failed", path=shortcut, error=str(e))

        # 4. Registry App Paths
        reg_path = find_app_path_in_registry(target.name)
        if reg_path:
            try:
                args = [reg_path]
                if known_def and known_def.launch_args:
                    args.extend(known_def.launch_args)
                elif any(b in target.name.lower() for b in ("chrome", "edge", "brave")):
                    args.append("--new-window")
                subprocess.Popen(args, close_fds=True)
                return f"registry:{reg_path}"
            except Exception as e:
                logger.debug("app_tool.reg_launch_failed", path=reg_path, error=str(e))

        # 5. PATH binary
        proc_name = target.process_name or f"{target.name}.exe"
        which_path = shutil.which(proc_name) or shutil.which(target.name)
        if which_path:
            try:
                args = [which_path]
                if known_def and known_def.launch_args:
                    args.extend(known_def.launch_args)
                elif any(b in target.name.lower() for b in ("chrome", "edge", "brave")):
                    args.append("--new-window")
                subprocess.Popen(args, close_fds=True)
                return f"path:{which_path}"
            except Exception as e:
                logger.debug("app_tool.path_launch_failed", path=which_path, error=str(e))

        # 6. Windows Start search fallback
        if sys.platform == "win32":
            try:
                os.startfile(f"shell:AppsFolder\\{target.name}")  # type: ignore[attr-defined]
                return f"apps_folder:{target.name}"
            except Exception:
                pass

        return None

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        """Execute OpenApplicationAction using the 7-level hierarchy with bounded retry."""
        if not isinstance(action, OpenApplicationAction):
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=datetime.now(UTC),
                duration_ms=0,
                error=ErrorInfo(code="UNSUPPORTED_ACTION", message=f"Expected OpenApplicationAction, got {action.type}"),
            )

        started_at = datetime.now(UTC)
        start_mono = time.monotonic()
        target = action.target

        # Normalize target if needed
        if isinstance(target, str):
            target = resolve_app_target(target)
        elif not target.process_name or not target.window_title_pattern:
            target = resolve_app_target(target.name)

        logger.info(
            "app_tool.resolving_target",
            name=target.name,
            process=target.process_name,
            confidence=target.confidence,
        )

        tried_strategies: list[str] = []

        # =========================================================================
        # Strategy 1: Existing running window / process lookup & focus
        # =========================================================================
        tried_strategies.append("existing_running_window")
        windows = await asyncio.to_thread(self.wm.list_windows)
        match_win = self._find_matching_window(target, windows)

        if match_win:
            logger.info("app_tool.found_existing_window", handle=match_win.handle, title=match_win.title)
            await asyncio.to_thread(self._focus_hwnd, match_win.handle)
            await asyncio.sleep(min(action.settle_ms / 1000.0, 0.5))

            # Verify foreground
            fg = await asyncio.to_thread(self.wm.foreground)
            if self._is_foreground_match(target, fg):
                duration_ms = int((time.monotonic() - start_mono) * 1000)
                return ActionResult(
                    action_type="open_application",
                    success=True,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    data={
                        "postcondition_passed": True,
                        "postcondition_evidence": f"Foreground window '{fg.title if fg else ''}' matches target {target.name}",
                        "target": target.model_dump(),
                        "strategy": "existing_running_window",
                        "foreground_window": fg.model_dump() if fg else None,
                    },
                )

        # =========================================================================
        # Strategy 2: Taskbar UI element identification (dynamic, non-hardcoded)
        # =========================================================================
        tried_strategies.append("taskbar_element_identification")
        taskbar_coords = await asyncio.to_thread(self._find_taskbar_button, target)
        if taskbar_coords:
            tx, ty = taskbar_coords
            logger.info("app_tool.taskbar_button_found", x=tx, y=ty, target=target.name)
            # Click the resolved taskbar button
            try:
                import pyautogui

                await asyncio.to_thread(pyautogui.click, tx, ty)
                await asyncio.sleep(min(action.settle_ms / 1000.0, 1.0))

                fg = await asyncio.to_thread(self.wm.foreground)
                if self._is_foreground_match(target, fg):
                    duration_ms = int((time.monotonic() - start_mono) * 1000)
                    return ActionResult(
                        action_type="open_application",
                        success=True,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        data={
                            "postcondition_passed": True,
                            "postcondition_evidence": f"Foreground window '{fg.title if fg else ''}' matches target {target.name}",
                            "target": target.model_dump(),
                            "strategy": "taskbar_element_identification",
                            "foreground_window": fg.model_dump() if fg else None,
                        },
                    )
            except Exception as e:
                logger.debug("app_tool.taskbar_click_error", error=str(e))

        # =========================================================================
        # Strategy 3: Executable / Protocol / Start Menu Launch
        # =========================================================================
        tried_strategies.append("executable_and_protocol_launch")
        launch_strat = await self._launch_executable(target)
        if launch_strat:
            logger.info("app_tool.app_launched", strategy=launch_strat)

            # Poll for window to appear in foreground up to 4.5 seconds
            poll_start = time.monotonic()
            while time.monotonic() - poll_start < max(4.5, action.settle_ms / 1000.0):
                await asyncio.sleep(0.2)
                fg = await asyncio.to_thread(self.wm.foreground)
                if self._is_foreground_match(target, fg):
                    duration_ms = int((time.monotonic() - start_mono) * 1000)
                    return ActionResult(
                        action_type="open_application",
                        success=True,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        data={
                            "postcondition_passed": True,
                            "postcondition_evidence": f"Foreground window '{fg.title if fg else ''}' matches target {target.name}",
                            "target": target.model_dump(),
                            "strategy": launch_strat,
                            "foreground_window": fg.model_dump() if fg else None,
                        },
                    )
                # Also check visible windows in case window opened in background
                all_wins = await asyncio.to_thread(self.wm.list_windows)
                w_match = self._find_matching_window(target, all_wins)
                if w_match:
                    await asyncio.to_thread(self._focus_hwnd, w_match.handle)
                    await asyncio.sleep(0.2)
                    fg_now = await asyncio.to_thread(self.wm.foreground)
                    if self._is_foreground_match(target, fg_now):
                        duration_ms = int((time.monotonic() - start_mono) * 1000)
                        return ActionResult(
                            action_type="open_application",
                            success=True,
                            started_at=started_at,
                            duration_ms=duration_ms,
                            data={
                                "postcondition_passed": True,
                                "postcondition_evidence": f"Foreground window '{fg_now.title if fg_now else ''}' matches target {target.name}",
                                "target": target.model_dump(),
                                "strategy": f"{launch_strat}+bring_to_front",
                                "foreground_window": fg_now.model_dump() if fg_now else None,
                            },
                        )

        # Final verification check
        final_fg = await asyncio.to_thread(self.wm.foreground)
        if self._is_foreground_match(target, final_fg):
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type="open_application",
                success=True,
                started_at=started_at,
                duration_ms=duration_ms,
                data={
                    "postcondition_passed": True,
                    "postcondition_evidence": f"Foreground window '{final_fg.title if final_fg else ''}' matches target {target.name}",
                    "target": target.model_dump(),
                    "strategy": "post_launch_settle",
                    "foreground_window": final_fg.model_dump() if final_fg else None,
                },
            )

        # If verification fails, return honest failure with tried strategies
        duration_ms = int((time.monotonic() - start_mono) * 1000)
        curr_title = final_fg.title if final_fg else "None"
        curr_proc = final_fg.process_name if final_fg else "None"

        return ActionResult(
            action_type="open_application",
            success=False,
            started_at=started_at,
            duration_ms=duration_ms,
            data={
                "postcondition_passed": False,
                "postcondition_evidence": f"Foreground window is '{curr_title}' ({curr_proc}), expected {target.name}",
                "target": target.model_dump(),
                "tried_strategies": tried_strategies,
            },
            error=ErrorInfo(
                code="APP_LAUNCH_VERIFICATION_FAILED",
                message=(
                    f"Failed to verify application '{target.name}' in foreground after trying "
                    f"strategies: {', '.join(tried_strategies)}. "
                    f"Current foreground: '{curr_title}' ({curr_proc})."
                ),
            ),
        )
