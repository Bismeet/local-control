"""Human approval gate abstractions and CLI prompt implementation."""

import asyncio
from typing import Any, Protocol, runtime_checkable

import structlog
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from local_control.core.actions import Action
from local_control.core.types import ApprovalDecision, Verdict

logger = structlog.get_logger(__name__)


@runtime_checkable
class ApprovalGate(Protocol):
    """Protocol for obtaining human approval before executing an action."""

    def request(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        """Request synchronous approval for an action."""
        ...

    async def arequest(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        """Request asynchronous approval for an action."""
        ...

    def ask_user(self, question: str) -> str:
        """Prompt user with a question and return their text response."""
        ...

    async def aask_user(self, question: str) -> str:
        """Asynchronously prompt user with a question and return their response."""
        ...


class CliApprovalGate:
    """Console-based approval gate prompting the user via Rich."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def request(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        """Prompt the user in terminal with full unabridged details (SECURITY_MODEL section 6)."""
        summary_text = verdict.human_summary if verdict else f"Execute {action.type}"
        category_text = verdict.category if verdict else "unclassified"
        reasons_text = "; ".join(verdict.reasons) if verdict and verdict.reasons else "None"
        grantable = verdict.grantable_for_run if verdict else False

        content = [
            f"[bold]Action Summary:[/bold] {summary_text}",
            f"[bold]Category / Tier:[/bold] {category_text} ({verdict.tier if verdict else 'UNKNOWN'})",
            f"[bold]Reasons:[/bold] {reasons_text}",
            f"[bold]Target Screenshot:[/bold] {screenshot_path or 'N/A'}",
            "",
            "[bold]Raw Action Proposal:[/bold]",
            action.model_dump_json(indent=2),
        ]

        options_prompt = "[y] approve once, [n] deny"
        choices = ["y", "n", "s"]
        if grantable:
            options_prompt += ", [a] approve category for this run"
            choices.append("a")
        options_prompt += ", [s] stop"

        self.console.print(
            Panel(
                "\n".join(content),
                title=f"[bold cyan]Action Approval Required: {action.type}[/bold cyan]",
                border_style="yellow",
            )
        )

        try:
            choice = Prompt.ask(
                options_prompt,
                choices=choices,
                default="n",
                console=self.console,
            ).lower()
        except (KeyboardInterrupt, EOFError):
            choice = "n"

        if choice == "y":
            decision = ApprovalDecision(decision="approved")
        elif choice == "a":
            decision = ApprovalDecision(decision="approved_for_run")
        elif choice == "s":
            decision = ApprovalDecision(decision="denied", note="Stopped by user")
        else:
            decision = ApprovalDecision(decision="denied")

        logger.info(
            "approval_gate.decision",
            action_type=action.type,
            decision=decision.decision,
            category=category_text,
        )
        return decision

    async def arequest(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        """Asynchronously prompt for approval offloading blocking input to worker thread."""
        return await asyncio.to_thread(self.request, action, verdict, screenshot_path)

    def ask_user(self, question: str) -> str:
        """Prompt user with a question and return their text response."""
        return Prompt.ask(f"[bold cyan]User Input Required:[/bold cyan] {question}")

    async def aask_user(self, question: str) -> str:
        """Asynchronously prompt user with a question and return their response."""
        return await asyncio.to_thread(self.ask_user, question)


class AutoApprovalGate:
    """Approval gate that automatically approves or rejects (for testing or automation)."""

    def __init__(
        self,
        approve: bool = True,
        approve_for_run: bool = False,
        user_answer: str = "ok",
        auto_approved_categories: set[str] | None = None,
    ) -> None:
        self.approve = approve
        self.approve_for_run = approve_for_run
        self.user_answer = user_answer
        self.auto_approved_categories = auto_approved_categories
        self.history: list[dict[str, Any]] = []

    def request(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        if self.auto_approved_categories is not None:
            if verdict and verdict.category in self.auto_approved_categories:
                dec = ApprovalDecision(decision="approved")
            else:
                dec = ApprovalDecision(decision="denied")
        elif self.approve_for_run and verdict and verdict.grantable_for_run:
            dec = ApprovalDecision(decision="approved_for_run")
        elif self.approve:
            dec = ApprovalDecision(decision="approved")
        else:
            dec = ApprovalDecision(decision="denied")

        self.history.append(
            {
                "action": action,
                "decision": dec,
                "verdict": verdict,
                "category": verdict.category if verdict else None,
            }
        )
        logger.info("auto_approval_gate.decision", action_type=action.type, decision=dec.decision)
        return dec

    async def arequest(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        return self.request(action, verdict, screenshot_path)

    def ask_user(self, question: str) -> str:
        return self.user_answer

    async def aask_user(self, question: str) -> str:
        return self.user_answer
