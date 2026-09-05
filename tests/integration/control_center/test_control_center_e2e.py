"""Integration test: driving an end-to-end agent scenario via Control Center REST and WebSocket APIs."""

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from local_control.agent.budget import Budget
from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.control_center.gate import ControlCenterApprovalGate
from local_control.control_center.server import create_app
from local_control.core.events import EventBus
from local_control.core.run_store import RunStore
from local_control.models.fake import FakeModelProvider
from tests.integration.fakes.fake_computer import FakeComputer


@pytest.mark.asyncio
async def test_control_center_scenario_e2e():
    """Drive a full agent scenario end to end via Control Center without CLI."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        runs_dir = tmp_path / "runs"
        settings = Settings()
        settings.logging.runs_dir = str(runs_dir)
        run_store = RunStore(base_dir=runs_dir)
        bus = EventBus()
        gate = ControlCenterApprovalGate(event_bus=bus)
        computer = FakeComputer()
        observer = computer.create_observer()
        executor = computer.create_executor()

        scripted_steps = [
            # Step 1: Click action
            {
                "assessment": {
                    "screen_summary": "Desktop ready",
                    "previous_action_outcome": "not_applicable",
                    "evidence": "Start",
                },
                "action": {
                    "type": "click",
                    "x": 100,
                    "y": 100,
                    "target_description": "Initial target",
                    "expected_outcome": "Target clicked",
                },
                "confidence": 0.9,
                "rationale": "First action",
            },
            # Step 2: Done action
            {
                "assessment": {
                    "screen_summary": "Target clicked",
                    "previous_action_outcome": "success",
                    "evidence": "Observed change",
                },
                "action": {
                    "type": "done",
                    "summary": "Scenario completed successfully from Control Center",
                    "verification_notes": "Verified desktop state",
                    "target_description": "Goal completion",
                    "expected_outcome": "Run finished",
                },
                "confidence": 0.95,
                "rationale": "Finish",
            },
        ]

        provider = FakeModelProvider(scripted_responses=scripted_steps)
        planner = Planner(provider=provider)
        runner = AgentRunner(
            planner=planner,
            executor=executor,
            observer=observer,
            approval_gate=gate,
            settings=settings,
            event_bus=bus,
            run_store=run_store,
            budget=Budget(settings=settings),
        )

        token = "test-e2e-token"
        app = create_app(
            runner=runner,
            run_store=run_store,
            event_bus=bus,
            gate=gate,
            token=token,
            settings=settings,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Check initial status
            status_resp = await client.get(f"/api/status?token={token}")
            assert status_resp.status_code == 200
            assert status_resp.json()["status"] == "ok"
            assert status_resp.json()["active_run"] is None

            # 2. Start a new run via REST API
            start_resp = await client.post(
                f"/api/runs?token={token}",
                json={
                    "goal": "Verify Control Center autonomous execution",
                    "run_id": "cc-run-e2e-1",
                },
            )
            assert start_resp.status_code == 200
            assert start_resp.json()["status"] == "started"
            assert start_resp.json()["run_id"] == "cc-run-e2e-1"

            # 3. Wait for the run task to complete
            if app.state.active_run_task:
                await app.state.active_run_task

            # 4. Status should now be idle
            status_after = await client.get(f"/api/status?token={token}")
            assert status_after.status_code == 200
            assert status_after.json()["active_run"] is None

            # 5. List runs via REST
            runs_resp = await client.get(f"/api/runs?token={token}")
            assert runs_resp.status_code == 200
            runs_list = runs_resp.json()
            assert len(runs_list) >= 1
            assert any(r["run_id"] == "cc-run-e2e-1" for r in runs_list)

            # 6. Get run details
            detail_resp = await client.get(f"/api/runs/cc-run-e2e-1?token={token}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["meta"]["run_id"] == "cc-run-e2e-1"
            assert detail["summary"] is not None
            assert "Scenario completed successfully" in detail["summary"]

            # 7. Get run replay timeline
            replay_resp = await client.get(f"/api/runs/cc-run-e2e-1/replay?token={token}")
            assert replay_resp.status_code == 200
            replay = replay_resp.json()
            assert replay["run_id"] == "cc-run-e2e-1"
            assert len(replay["steps"]) >= 1
