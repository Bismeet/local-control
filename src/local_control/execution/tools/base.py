"""Base contracts and execution context for tool adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_control.config.settings import Settings
from local_control.core.actions import Action
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ActionResult, Observation
from local_control.safety.kill_switch import StopToken


@dataclass
class ExecutionContext:
    """Runtime context passed to tools during action execution."""

    run_id: str
    stop: StopToken
    mapper: CoordinateMapper | None = None
    settings: Settings | None = None
    workdir: Path | None = None


class Tool(ABC):
    """Abstract base class for tool adapters executing validated actions."""

    @property
    @abstractmethod
    def handles(self) -> frozenset[str]:
        """Set of action type strings handled by this tool."""
        ...

    @abstractmethod
    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        """Execute the given action within the provided runtime context."""
        ...

    async def postcondition(
        self, action: Action, result: ActionResult, obs_after: Observation
    ) -> Any | None:
        """Optional postcondition check verifying expected deterministic state changes."""
        return None
