"""E2E Test Scenario 4: Navigate a website and complete a safe workflow (V2, browser).

Specification: TEST_PLAN.md Section 10 Scenario 4.
Starting state: fixture form.html served locally simulating support request form,
plus a fake checkout link.
User goal: Open the support form, fill it in for Alex Doe, alex@example.com,
category 'Billing', message 'Please resend my last invoice', and submit it.
"""

from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.events import EventBus
from local_control.core.run_store import RunStore
from local_control.execution.executor import Executor
from local_control.execution.tools.browser_tool import BrowserTool
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


class FormFixtureServer(ThreadingHTTPServer):
    submitted_data: dict | None = None


class FormRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        fixtures_dir = Path(__file__).parent.parent / "browser" / "fixtures"
        super().__init__(*args, directory=str(fixtures_dir), **kwargs)

    def do_POST(self):
        if self.path == "/submit":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            self.server.submitted_data = data  # type: ignore[attr-defined]

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        else:
            self.send_error(404)


@pytest.fixture
def form_server():
    server = FormFixtureServer(("127.0.0.1", 0), FormRequestHandler)
    host, port = server.server_address
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scenario_4_web_workflow(tmp_path: Path, form_server: FormFixtureServer) -> None:
    host, port = form_server.server_address
    server_url = f"http://{host}:{port}"

    settings = Settings()
    settings.safety.autonomy_mode = "assisted"
    settings.safety.allowed_roots = [str(tmp_path)]
    settings.browser.profile_dir = str(tmp_path / "browser_profile")
    settings.browser.headless = True
    settings.budget.max_steps = 15

    scripted_turns: list[dict] = [
        # 1. Navigate
        {
            "assessment": {
                "screen_summary": "Starting support form task.",
                "previous_action_outcome": "not_applicable",
                "evidence": "Initial state",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "active"},
                    {"index": 1, "description": "Inspect form elements", "status": "pending"},
                    {
                        "index": 2,
                        "description": "Fill and submit support form",
                        "status": "pending",
                    },
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 0,
                "revision": 0,
            },
            "action": {
                "type": "browser_navigate",
                "url": f"{server_url}/form.html",
                "target_description": "Open support form",
                "expected_outcome": "Form page loaded",
            },
            "confidence": 0.95,
            "rationale": "Load support form",
        },
        # 2. Snapshot
        {
            "assessment": {
                "screen_summary": "On support form page.",
                "previous_action_outcome": "success",
                "evidence": "Form loaded",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "active"},
                    {
                        "index": 2,
                        "description": "Fill and submit support form",
                        "status": "pending",
                    },
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 1,
                "revision": 0,
            },
            "action": {
                "type": "browser_snapshot",
                "target_description": "Snapshot form structure",
                "expected_outcome": "Element refs available",
            },
            "confidence": 0.95,
            "rationale": "Inspect accessible fields",
        },
        # 3. Type Name
        {
            "assessment": {
                "screen_summary": "Snapshot captured. Filling name.",
                "previous_action_outcome": "success",
                "evidence": "Refs mapped",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "active"},
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_type",
                "selector": "#name",
                "text": "Alex Doe",
                "target_description": "Type name",
                "expected_outcome": "Name entered",
            },
            "confidence": 0.95,
            "rationale": "Enter user name",
        },
        # 4. Type Email
        {
            "assessment": {
                "screen_summary": "Name entered. Filling email.",
                "previous_action_outcome": "success",
                "evidence": "Name in input",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "active"},
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_type",
                "selector": "#email",
                "text": "alex@example.com",
                "target_description": "Type email",
                "expected_outcome": "Email entered",
            },
            "confidence": 0.95,
            "rationale": "Enter user email",
        },
        # 5. Select Category
        {
            "assessment": {
                "screen_summary": "Email entered. Selecting category.",
                "previous_action_outcome": "success",
                "evidence": "Email filled",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "active"},
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_type",
                "selector": "#category",
                "text": "Billing",
                "target_description": "Select Billing category",
                "expected_outcome": "Billing category chosen",
            },
            "confidence": 0.95,
            "rationale": "Choose Billing option",
        },
        # 6. Type Message
        {
            "assessment": {
                "screen_summary": "Category selected. Filling message.",
                "previous_action_outcome": "success",
                "evidence": "Category set to Billing",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "active"},
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_type",
                "selector": "#message",
                "text": "Please resend my last invoice",
                "target_description": "Enter message text",
                "expected_outcome": "Message filled",
            },
            "confidence": 0.95,
            "rationale": "Provide support inquiry",
        },
        # 7. Click Submit button (Triggers C-08 -> Requires CONFIRM approval)
        {
            "assessment": {
                "screen_summary": "All fields filled. Submitting form.",
                "previous_action_outcome": "success",
                "evidence": "Form fully populated",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "active"},
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "pending",
                    },
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_click",
                "selector": "#submit-btn",
                "target_description": "Click Submit button",
                "expected_outcome": "Form submitted to server",
            },
            "confidence": 0.95,
            "rationale": "Submit the completed support form",
        },
        # 8. Read Confirmation
        {
            "assessment": {
                "screen_summary": "Form submitted. Checking confirmation.",
                "previous_action_outcome": "success",
                "evidence": "Submit executed",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "done"},
                    {
                        "index": 3,
                        "description": "Verify confirmation and finish",
                        "status": "active",
                    },
                ],
                "current_index": 3,
                "revision": 0,
            },
            "action": {
                "type": "browser_read",
                "selector": "#confirmation",
                "target_description": "Read confirmation message",
                "expected_outcome": "Confirmation text verified",
            },
            "confidence": 0.95,
            "rationale": "Verify submission result",
        },
        # 9. Done
        {
            "assessment": {
                "screen_summary": "Confirmation verified.",
                "previous_action_outcome": "success",
                "evidence": "Support Request Received message visible",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Navigate to support form", "status": "done"},
                    {"index": 1, "description": "Inspect form elements", "status": "done"},
                    {"index": 2, "description": "Fill and submit support form", "status": "done"},
                    {"index": 3, "description": "Verify confirmation and finish", "status": "done"},
                ],
                "current_index": 3,
                "revision": 0,
            },
            "action": {
                "type": "done",
                "summary": "Support request submitted for Alex Doe (alex@example.com)",
                "verification_notes": "Received confirmation on page",
                "target_description": "Finish task",
                "expected_outcome": "Run complete",
            },
            "confidence": 0.99,
            "rationale": "Goal reached",
        },
    ]

    provider = FakeModelProvider(scripted_responses=scripted_turns)
    planner = Planner(provider=provider)

    fake_comp = FakeComputer()
    fake_tool = FakeComputerTool(fake_comp)
    browser_tool = BrowserTool(
        profile_dir=Path(settings.browser.profile_dir),
        headless=True,
    )

    executor = Executor(tools=[fake_tool, browser_tool])
    # Auto approve ONLY C-08
    gate = AutoApprovalGate(auto_approved_categories={"C-08"})
    runs_dir = tmp_path / "runs"
    run_store = RunStore(base_dir=runs_dir)

    runner = AgentRunner(
        planner=planner,
        executor=executor,
        observer=fake_comp.create_observer(),
        approval_gate=gate,
        settings=settings,
        run_store=run_store,
        event_bus=EventBus(),
    )

    try:
        run_result = await runner.run(
            goal=f"Open the support form at {server_url}/form.html, fill it in for Alex Doe, alex@example.com, category 'Billing', message 'Please resend my last invoice', and submit it.",
            run_id="scenario-4-run",
        )

        assert run_result.status == "COMPLETED"
        assert run_result.steps_count <= 15

        # Verify exactly one approval was requested
        assert len(gate.history) == 1
        assert gate.history[0]["category"] == "C-08"

        # Verify submitted payload on server
        assert form_server.submitted_data is not None
        assert form_server.submitted_data["name"] == "Alex Doe"
        assert form_server.submitted_data["email"] == "alex@example.com"
        assert form_server.submitted_data["category"] == "Billing"
        assert form_server.submitted_data["message"] == "Please resend my last invoice"
    finally:
        await browser_tool.close()
