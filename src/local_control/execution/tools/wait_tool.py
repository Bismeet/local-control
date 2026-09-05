"""Wait tool adapter with responsive stop checking."""

import asyncio
import time
from datetime import UTC, datetime

import structlog

from local_control.core.actions import Action, WaitAction
from local_control.core.types import ActionResult, ErrorInfo
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.safety.kill_switch import StopRequestedError

logger = structlog.get_logger(__name__)


class WaitTool(Tool):
    """Executes wait actions with fine-grained cancellation checking."""

    @property
    def handles(self) -> frozenset[str]:
        return frozenset({"wait"})

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        if not isinstance(action, WaitAction):
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=0,
                error=ErrorInfo(
                    code="UNSUPPORTED_ACTION", message=f"Expected WaitAction, got {action.type}"
                ),
            )

        total_seconds = action.seconds
        slice_seconds = 0.05
        elapsed = 0.0

        try:
            while elapsed < total_seconds:
                ctx.stop.check()
                remaining = total_seconds - elapsed
                sleep_time = min(slice_seconds, remaining)
                await asyncio.sleep(sleep_time)
                elapsed += sleep_time

            ctx.stop.check()
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type="wait",
                success=True,
                started_at=started_at,
                duration_ms=duration_ms,
                data={"waited_seconds": elapsed},
            )

        except StopRequestedError as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type="wait",
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="STOPPED_BY_USER", message=str(e)),
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type="wait",
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="EXECUTION_ERROR", message=str(e)),
            )
