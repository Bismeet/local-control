"""InputTool adapter executing keyboard and mouse actions via an InputBackend."""

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from local_control.core.actions import (
    Action,
    ClickAction,
    DragAction,
    MoveMouseAction,
    Point,
    PressKeysAction,
    ScrollAction,
    TypeTextAction,
)
from local_control.core.types import ActionResult, ErrorInfo
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.execution.tools.input_backend import InputBackend, PyAutoGuiBackend
from local_control.safety.kill_switch import StopRequestedError

logger = structlog.get_logger(__name__)


class InputTool(Tool):
    """Executes mouse and keyboard actions with coordinate mapping and failsafe checks."""

    def __init__(
        self,
        backend: InputBackend | None = None,
        backend_name: str | None = None,
    ) -> None:
        import os

        if backend is not None:
            self.backend = backend
        elif backend_name == "sendinput" or (backend_name is None and os.name == "nt"):
            try:
                from local_control.execution.tools.sendinput_backend import SendInputBackend

                self.backend = SendInputBackend()
            except Exception:
                self.backend = PyAutoGuiBackend()
        else:
            self.backend = PyAutoGuiBackend()

    @property
    def handles(self) -> frozenset[str]:
        return frozenset(
            {
                "click",
                "move_mouse",
                "drag",
                "scroll",
                "type_text",
                "press_keys",
            }
        )

    def _map_point(self, point: Point, ctx: ExecutionContext) -> Point:
        """Map Point to physical screen coordinates if mapper is available."""
        if ctx.mapper:
            return ctx.mapper.to_screen(point)
        return point

    def _resolve_point(self, action: Action, ctx: ExecutionContext) -> tuple[Point, Point]:
        """Resolve model image-space Point and physical screen Point honoring ref."""
        ref = getattr(action, "ref", None)
        if ref and ctx.ui_elements:
            matched = next(
                (el for el in ctx.ui_elements if el.ref.lower() == str(ref).lower()),
                None,
            )
            if matched:
                cx = matched.bbox.x + matched.bbox.width // 2
                cy = matched.bbox.y + matched.bbox.height // 2
                model_pt = Point(x=cx, y=cy)
                return model_pt, self._map_point(model_pt, ctx)

        model_pt = Point(x=getattr(action, "x", 0), y=getattr(action, "y", 0))
        return model_pt, self._map_point(model_pt, ctx)

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        try:
            # Check StopToken before starting any input action
            ctx.stop.check()

            data: dict[str, Any] = {}

            if isinstance(action, ClickAction):
                model_pt, screen_pt = self._resolve_point(action, ctx)
                data = {
                    "image_x": model_pt.x,
                    "image_y": model_pt.y,
                    "screen_x": screen_pt.x,
                    "screen_y": screen_pt.y,
                    "ref": getattr(action, "ref", None),
                    "button": action.button,
                    "clicks": action.clicks,
                }
                await asyncio.to_thread(
                    self.backend.click,
                    screen_pt.x,
                    screen_pt.y,
                    button=action.button,
                    clicks=action.clicks,
                )
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, MoveMouseAction):
                model_pt, screen_pt = self._resolve_point(action, ctx)
                data = {
                    "image_x": model_pt.x,
                    "image_y": model_pt.y,
                    "screen_x": screen_pt.x,
                    "screen_y": screen_pt.y,
                    "ref": getattr(action, "ref", None),
                }
                await asyncio.to_thread(self.backend.move_to, screen_pt.x, screen_pt.y)
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, DragAction):
                from_pt = self._map_point(action.from_point, ctx)
                to_pt = self._map_point(action.to_point, ctx)
                data = {
                    "from_screen": {"x": from_pt.x, "y": from_pt.y},
                    "to_screen": {"x": to_pt.x, "y": to_pt.y},
                    "button": action.button,
                    "duration_ms": action.duration_ms,
                }
                await asyncio.to_thread(
                    self.backend.drag,
                    from_pt.x,
                    from_pt.y,
                    to_pt.x,
                    to_pt.y,
                    duration_s=action.duration_ms / 1000.0,
                    button=action.button,
                )
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, ScrollAction):
                screen_pt = self._map_point(Point(x=action.x, y=action.y), ctx)
                data = {
                    "screen_x": screen_pt.x,
                    "screen_y": screen_pt.y,
                    "dx": action.dx,
                    "dy": action.dy,
                }
                await asyncio.to_thread(
                    self.backend.scroll,
                    action.dx,
                    action.dy,
                    screen_pt.x,
                    screen_pt.y,
                )
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, TypeTextAction):
                data = {"chars_count": len(action.text)}
                await asyncio.to_thread(
                    self.backend.type_text,
                    action.text,
                    stop_token=ctx.stop,
                )
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            elif isinstance(action, PressKeysAction):
                data = {"keys": action.keys}
                await asyncio.to_thread(self.backend.press_keys, action.keys)
                if action.settle_ms > 0:
                    await asyncio.sleep(action.settle_ms / 1000.0)

            else:
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="UNSUPPORTED_ACTION",
                        message=f"InputTool does not support action {action.type}",
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

        except StopRequestedError as e:
            # Emergency release of any held buttons or keys
            with contextlib.suppress(Exception):
                self.backend.release_all()
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="STOPPED_BY_USER", message=str(e)),
            )
        except Exception as e:
            with contextlib.suppress(Exception):
                self.backend.release_all()
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            logger.error("input_tool.execution_failed", error=str(e), action_type=action.type)
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="EXECUTION_ERROR", message=str(e)),
            )
