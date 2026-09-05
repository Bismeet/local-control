"""Integration tests for Control Center approval and inquiry roundtrip workflow."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from local_control.control_center.gate import ControlCenterApprovalGate
from local_control.control_center.server import create_app
from local_control.core.actions import ClickAction
from local_control.core.events import EventBus
from local_control.core.types import Verdict


@pytest.mark.asyncio
async def test_approval_roundtrip_approve():
    """Approval submitted via REST endpoint must resolve arequest with 'approved'."""
    bus = EventBus()
    gate = ControlCenterApprovalGate(event_bus=bus)
    app = create_app(event_bus=bus, gate=gate, token="tok-approval")
    client = TestClient(app)

    verdict = Verdict(
        tier="CONFIRM",
        category="external_navigation",
        decision="needs_confirmation",
        human_summary="Navigate to external domain",
    )
    action = ClickAction(
        x=100,
        y=100,
        target_description="Nav link",
        expected_outcome="Navigate",
    )

    # Background task requesting approval
    approval_task = asyncio.create_task(gate.arequest(action, verdict))
    await asyncio.sleep(0.05)

    assert len(gate._pending_approvals) == 1
    req_id = list(gate._pending_approvals.keys())[0]

    # Client posts approval
    resp = client.post(
        "/api/runs/test-run-1/approve?token=tok-approval",
        json={"decision": "approved", "request_id": req_id, "note": "Looks safe"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    decision = await approval_task
    assert decision.decision == "approved"
    assert decision.note == "Looks safe"


@pytest.mark.asyncio
async def test_approval_roundtrip_deny():
    """Denial submitted via REST endpoint must resolve arequest with 'denied'."""
    bus = EventBus()
    gate = ControlCenterApprovalGate(event_bus=bus)
    app = create_app(event_bus=bus, gate=gate, token="tok-approval-deny")
    client = TestClient(app)

    verdict = Verdict(
        tier="CONFIRM",
        category="file_delete",
        decision="needs_confirmation",
        human_summary="Delete files",
    )
    action = ClickAction(
        x=50,
        y=50,
        target_description="Delete button",
        expected_outcome="Deleted",
    )

    approval_task = asyncio.create_task(gate.arequest(action, verdict))
    await asyncio.sleep(0.05)

    assert len(gate._pending_approvals) == 1
    req_id = list(gate._pending_approvals.keys())[0]

    resp = client.post(
        "/api/runs/test-run-2/approve?token=tok-approval-deny",
        json={"decision": "denied", "request_id": req_id, "note": "Do not delete"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    decision = await approval_task
    assert decision.decision == "denied"
    assert decision.note == "Do not delete"


@pytest.mark.asyncio
async def test_question_answer_roundtrip():
    """User answer submitted via REST endpoint must resolve aask_user."""
    bus = EventBus()
    gate = ControlCenterApprovalGate(event_bus=bus)
    app = create_app(event_bus=bus, gate=gate, token="tok-qa")
    client = TestClient(app)

    question_task = asyncio.create_task(gate.aask_user("Which directory should I inspect?"))
    await asyncio.sleep(0.05)

    assert len(gate._pending_answers) == 1
    req_id = list(gate._pending_answers.keys())[0]

    resp = client.post(
        "/api/runs/test-run-3/answer?token=tok-qa",
        json={"answer": "C:/Reports", "request_id": req_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    answer = await question_task
    assert answer == "C:/Reports"
