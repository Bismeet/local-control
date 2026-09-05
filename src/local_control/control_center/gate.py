"""Control Center human-in-the-loop approval gate."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from local_control.core.actions import Action
from local_control.core.events import Event, EventBus
from local_control.core.types import ApprovalDecision, Verdict

logger = structlog.get_logger(__name__)


class ControlCenterApprovalGate:
    """Approval gate bridging agent runs with Control Center WebSocket / REST consumers."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self.event_bus = event_bus
        self._pending_approvals: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._pending_approval_payloads: dict[str, dict[str, Any]] = {}
        self._pending_answers: dict[str, asyncio.Future[str]] = {}
        self._pending_answer_payloads: dict[str, dict[str, Any]] = {}
        self._active_approval_req_id: str | None = None
        self._active_answer_req_id: str | None = None

    @property
    def has_pending_approval(self) -> bool:
        return bool(self._pending_approvals)

    @property
    def has_pending_answer(self) -> bool:
        return bool(self._pending_answers)

    def request(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        """Synchronous wrapper for arequest."""
        return asyncio.run(self.arequest(action, verdict, screenshot_path))

    async def arequest(
        self,
        action: Action,
        verdict: Verdict | None = None,
        screenshot_path: str | None = None,
    ) -> ApprovalDecision:
        """Wait asynchronously for human approval via Control Center API."""
        req_id = f"appr-{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()

        payload: dict[str, Any] = {
            "request_id": req_id,
            "action": action.model_dump(mode="json"),
            "verdict": verdict.model_dump(mode="json") if verdict else None,
            "category": verdict.category if verdict else "unknown",
            "human_summary": verdict.human_summary if verdict else action.type,
            "grantable_for_run": verdict.grantable_for_run if verdict else False,
            "screenshot_path": screenshot_path,
        }

        self._pending_approvals[req_id] = fut
        self._pending_approval_payloads[req_id] = payload
        self._active_approval_req_id = req_id

        logger.info(
            "control_center_gate.approval_requested",
            request_id=req_id,
            action_type=action.type,
            category=payload["category"],
        )

        if self.event_bus:
            await self.event_bus.publish(
                Event(
                    run_id="current",
                    type="approval_requested",
                    payload=payload,
                )
            )

        try:
            decision = await fut
            logger.info(
                "control_center_gate.approval_resolved",
                request_id=req_id,
                decision=decision.decision,
            )
            return decision
        finally:
            self._pending_approvals.pop(req_id, None)
            self._pending_approval_payloads.pop(req_id, None)
            if self._active_approval_req_id == req_id:
                self._active_approval_req_id = None

    def resolve_approval(
        self,
        decision: ApprovalDecision,
        request_id: str | None = None,
    ) -> bool:
        """Resolve a pending approval request."""
        target_id = request_id or self._active_approval_req_id
        if not target_id or target_id not in self._pending_approvals:
            # If any approval is pending, resolve the first one
            if self._pending_approvals:
                target_id = next(iter(self._pending_approvals))
            else:
                logger.warning(
                    "control_center_gate.no_pending_approval_found", request_id=request_id
                )
                return False

        fut = self._pending_approvals[target_id]
        if not fut.done():
            fut.set_result(decision)
            return True
        return False

    def ask_user(self, question: str) -> str:
        """Synchronous wrapper for aask_user."""
        return asyncio.run(self.aask_user(question))

    async def aask_user(self, question: str) -> str:
        """Prompt user with question and wait asynchronously for answer."""
        req_id = f"ask-{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()

        payload: dict[str, Any] = {
            "request_id": req_id,
            "question": question,
        }

        self._pending_answers[req_id] = fut
        self._pending_answer_payloads[req_id] = payload
        self._active_answer_req_id = req_id

        logger.info(
            "control_center_gate.user_input_requested", request_id=req_id, question=question
        )

        if self.event_bus:
            await self.event_bus.publish(
                Event(
                    run_id="current",
                    type="user_input_requested",
                    payload=payload,
                )
            )

        try:
            answer = await fut
            logger.info("control_center_gate.user_input_resolved", request_id=req_id, answer=answer)
            return answer
        finally:
            self._pending_answers.pop(req_id, None)
            self._pending_answer_payloads.pop(req_id, None)
            if self._active_answer_req_id == req_id:
                self._active_answer_req_id = None

    def resolve_answer(
        self,
        answer: str,
        request_id: str | None = None,
    ) -> bool:
        """Resolve a pending user question request."""
        target_id = request_id or self._active_answer_req_id
        if not target_id or target_id not in self._pending_answers:
            if self._pending_answers:
                target_id = next(iter(self._pending_answers))
            else:
                logger.warning("control_center_gate.no_pending_answer_found", request_id=request_id)
                return False

        fut = self._pending_answers[target_id]
        if not fut.done():
            fut.set_result(answer)
            return True
        return False

    def get_pending_approval(self) -> dict[str, Any] | None:
        """Return the active pending approval payload, if any."""
        if (
            self._active_approval_req_id
            and self._active_approval_req_id in self._pending_approval_payloads
        ):
            return self._pending_approval_payloads[self._active_approval_req_id]
        if self._pending_approval_payloads:
            return next(iter(self._pending_approval_payloads.values()))
        return None

    def get_pending_answer(self) -> dict[str, Any] | None:
        """Return the active pending user question payload, if any."""
        if (
            self._active_answer_req_id
            and self._active_answer_req_id in self._pending_answer_payloads
        ):
            return self._pending_answer_payloads[self._active_answer_req_id]
        if self._pending_answer_payloads:
            return next(iter(self._pending_answer_payloads.values()))
        return None

    def abort_all(self, reason: str = "Execution stopped") -> None:
        """Immediately abort and reject all pending approval and question requests."""
        for fut in list(self._pending_approvals.values()):
            if not fut.done():
                fut.set_result(ApprovalDecision(decision="denied", note=reason))
        for fut in list(self._pending_answers.values()):
            if not fut.done():
                fut.set_result(reason)
        self._pending_approvals.clear()
        self._pending_approval_payloads.clear()
        self._pending_answers.clear()
        self._pending_answer_payloads.clear()
        self._active_approval_req_id = None
        self._active_answer_req_id = None
