"""Human approval gate abstractions and CLI prompt implementation."""

import asyncio
from typing import Protocol, runtime_checkable

import structlog
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from local_control.core.actions import Action

logger = structlog.get_logger(__name__)


@runtime_checkable
class ApprovalGate(Protocol):
    """Protocol for obtaining human approval before executing an action."""

    def request(self, action: Action, prompt: str = "") -> bool:
        """Request synchronous approval for an action."""
        ...

    async def arequest(self, action: Action, prompt: str = "") -> bool:
        """Request asynchronous approval for an action."""
        ...


class CliApprovalGate:
    """Console-based approval gate prompting the user via Rich."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def request(self, action: Action, prompt: str = "") -> bool:
        """Prompt the user in terminal to approve or reject the action."""
        summary = action.model_dump_json(indent=2)
        prompt_text = prompt or f"Execute action [bold yellow]{action.type}[/bold yellow]?"

        self.console.print(
            Panel(
                summary,
                title=f"[bold cyan]Action Approval Required: {action.type}[/bold cyan]",
                border_style="yellow",
            )
        )

        try:
            approved = Confirm.ask(prompt_text, default=False, console=self.console)
        except (KeyboardInterrupt, EOFError):
            approved = False

        logger.info("approval_gate.decision", action_type=action.type, approved=approved)
        return approved

    async def arequest(self, action: Action, prompt: str = "") -> bool:
        """Asynchronously prompt for approval offloading blocking input to a worker thread."""
        return await asyncio.to_thread(self.request, action, prompt)


class AutoApprovalGate:
    """Approval gate that automatically approves or rejects (for testing or automation)."""

    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.history: list[tuple[Action, bool]] = []

    def request(self, action: Action, prompt: str = "") -> bool:
        self.history.append((action, self.approve))
        logger.info("auto_approval_gate.decision", action_type=action.type, approved=self.approve)
        return self.approve

    async def arequest(self, action: Action, prompt: str = "") -> bool:
        return self.request(action, prompt)
