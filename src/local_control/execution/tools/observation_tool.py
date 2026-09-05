"""Observation tools adapter for zoom_region and ocr_region actions."""

import time
from datetime import UTC, datetime

import structlog

from local_control.core.actions import Action, OcrRegionAction, ZoomRegionAction
from local_control.core.types import ActionResult, ErrorInfo
from local_control.execution.tools.base import ExecutionContext, Tool

logger = structlog.get_logger(__name__)


class ObservationTool(Tool):
    """Executes observation enhancement actions like zoom_region and ocr_region."""

    @property
    def handles(self) -> frozenset[str]:
        return frozenset({"zoom_region", "ocr_region"})

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        ctx.stop.check()

        if isinstance(action, ZoomRegionAction):
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type="zoom_region",
                success=True,
                started_at=started_at,
                duration_ms=duration_ms,
                data={
                    "rect": action.rect.model_dump(),
                    "postcondition_passed": True,
                },
            )

        if isinstance(action, OcrRegionAction):
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type="ocr_region",
                success=True,
                started_at=started_at,
                duration_ms=duration_ms,
                data={
                    "rect": action.rect.model_dump(),
                    "postcondition_passed": True,
                },
            )

        return ActionResult(
            action_type=action.type,
            success=False,
            started_at=started_at,
            duration_ms=0,
            error=ErrorInfo(
                code="UNSUPPORTED_ACTION",
                message=f"Action '{action.type}' is not supported by ObservationTool",
            ),
        )
