"""Action execution engine and tool dispatcher."""

import asyncio
import time
from datetime import UTC, datetime

import structlog

from local_control.core.actions import Action
from local_control.core.events import Event, EventBus
from local_control.core.types import ActionResult, ErrorInfo
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.safety.kill_switch import StopRequestedError

logger = structlog.get_logger(__name__)


class Executor:
    """Dispatches validated actions to tool adapters, enforcing timeouts and safety stops."""

    def __init__(
        self,
        tools: list[Tool] | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.registry: dict[str, Tool] = {}
        self.event_bus = event_bus

        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        """Register a tool for the action types it handles."""
        for action_type in tool.handles:
            self.registry[action_type] = tool
        logger.debug("executor.tool_registered", handles=list(tool.handles))

    def get_tool(self, action_type: str) -> Tool | None:
        """Retrieve registered tool for an action type."""
        return self.registry.get(action_type)

    async def execute(
        self,
        action: Action,
        ctx: ExecutionContext,
        step_index: int | None = None,
    ) -> ActionResult:
        """Execute a single validated action through its matching tool adapter."""
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        # 1. Early StopToken check
        if ctx.stop.is_set():
            reason = ctx.stop.reason() or "user"
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=0,
                error=ErrorInfo(
                    code="STOPPED_BY_USER",
                    message=f"Execution stopped: {reason}",
                ),
            )

        # 2. Tool lookup
        tool = self.get_tool(action.type)
        if not tool:
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=0,
                error=ErrorInfo(
                    code="NO_TOOL_FOUND",
                    message=f"No tool registered for action type '{action.type}'",
                ),
            )

        # 3. Determine timeout
        timeout_s = 30.0
        if ctx.settings and hasattr(ctx.settings, "execution"):
            timeout_s = getattr(ctx.settings.execution, "action_timeout_s", 30.0)
        if action.type == "shell_run":
            timeout_s = max(timeout_s, 120.0)

        # 4. Emit ActionStarted event
        if self.event_bus:
            await self.event_bus.publish(
                Event(
                    run_id=ctx.run_id,
                    step_index=step_index,
                    type="action_started",
                    payload={"action_type": action.type, "action": action.model_dump()},
                )
            )

        # 5. Execute with timeout
        result: ActionResult
        try:
            result = await asyncio.wait_for(tool.execute(action, ctx), timeout=timeout_s)
        except TimeoutError:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            result = ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(
                    code="timeout",
                    message=f"Action '{action.type}' timed out after {timeout_s:.1f}s",
                ),
            )
        except StopRequestedError as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            result = ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="STOPPED_BY_USER", message=str(e)),
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            logger.error("executor.execution_exception", error=str(e), action_type=action.type)
            result = ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="EXECUTION_ERROR", message=str(e)),
            )

        # 6. Emit ActionFinished event
        if self.event_bus:
            await self.event_bus.publish(
                Event(
                    run_id=ctx.run_id,
                    step_index=step_index,
                    type="action_finished",
                    payload={"action_type": action.type, "result": result.model_dump()},
                )
            )

        return result
