"""E2E Test Scenario 2: Research and create a Markdown report (V2, browser).

Specification: TEST_PLAN.md Section 10 Scenario 2.
Starting state: fixture site served locally with 3 pages of product information (site/).
User goal: Read the three product pages and write a Markdown comparison report to
Documents/Reports/comparison.md with a table of price, weight and warranty.
"""

from __future__ import annotations

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
from local_control.execution.tools.filesystem_tool import FilesystemTool
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


@pytest.fixture
def site_server():
    fixtures_dir = Path(__file__).parent.parent / "browser" / "fixtures" / "site"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: SimpleHTTPRequestHandler(
            *args, directory=str(fixtures_dir), **kwargs
        ),
    )
    host, port = server.server_address
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scenario_2_research_report(tmp_path: Path, site_server: str) -> None:
    reports_dir = tmp_path / "Documents" / "Reports"
    reports_dir.mkdir(parents=True)
    comparison_file = reports_dir / "comparison.md"

    settings = Settings()
    settings.safety.autonomy_mode = "assisted"
    settings.safety.allowed_roots = [str(tmp_path)]
    settings.browser.profile_dir = str(tmp_path / "browser_profile")
    settings.browser.headless = True
    settings.budget.max_steps = 20

    table_content = (
        "# Product Comparison Report\n\n"
        "| Product | Price | Weight | Warranty |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| Product Alpha | $299 | 1.2 kg | 2 years |\n"
        "| Product Beta | $499 | 1.8 kg | 3 years |\n"
        "| Product Gamma | $799 | 2.1 kg | 5 years |\n"
    )

    scripted_turns: list[dict] = [
        # Turn 1: Navigate to product 1
        {
            "assessment": {
                "screen_summary": "Starting product research.",
                "previous_action_outcome": "not_applicable",
                "evidence": "Initial state",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "active"},
                    {"index": 1, "description": "Read product 2", "status": "pending"},
                    {"index": 2, "description": "Read product 3", "status": "pending"},
                    {"index": 3, "description": "Write comparison report", "status": "pending"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 0,
                "revision": 0,
            },
            "action": {
                "type": "browser_navigate",
                "url": f"{site_server}/product1.html",
                "target_description": "Open product 1 page",
                "expected_outcome": "Product 1 loaded",
            },
            "confidence": 0.95,
            "rationale": "Navigate to product 1",
        },
        # Turn 2: Read product 1
        {
            "assessment": {
                "screen_summary": "On product 1 page.",
                "previous_action_outcome": "success",
                "evidence": "Page loaded",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "active"},
                    {"index": 1, "description": "Read product 2", "status": "pending"},
                    {"index": 2, "description": "Read product 3", "status": "pending"},
                    {"index": 3, "description": "Write comparison report", "status": "pending"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 0,
                "revision": 0,
            },
            "action": {
                "type": "browser_read",
                "target_description": "Extract product 1 details",
                "expected_outcome": "Specs read",
            },
            "confidence": 0.95,
            "rationale": "Read product 1 specs",
        },
        # Turn 3: Navigate to product 2
        {
            "assessment": {
                "screen_summary": "Finished reading product 1.",
                "previous_action_outcome": "success",
                "evidence": "Read text returned",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "active"},
                    {"index": 2, "description": "Read product 3", "status": "pending"},
                    {"index": 3, "description": "Write comparison report", "status": "pending"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 1,
                "revision": 0,
            },
            "action": {
                "type": "browser_navigate",
                "url": f"{site_server}/product2.html",
                "target_description": "Open product 2 page",
                "expected_outcome": "Product 2 loaded",
            },
            "confidence": 0.95,
            "rationale": "Navigate to product 2",
        },
        # Turn 4: Read product 2
        {
            "assessment": {
                "screen_summary": "On product 2 page.",
                "previous_action_outcome": "success",
                "evidence": "Page loaded",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "active"},
                    {"index": 2, "description": "Read product 3", "status": "pending"},
                    {"index": 3, "description": "Write comparison report", "status": "pending"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 1,
                "revision": 0,
            },
            "action": {
                "type": "browser_read",
                "target_description": "Extract product 2 details",
                "expected_outcome": "Specs read",
            },
            "confidence": 0.95,
            "rationale": "Read product 2 specs",
        },
        # Turn 5: Navigate to product 3
        {
            "assessment": {
                "screen_summary": "Finished reading product 2.",
                "previous_action_outcome": "success",
                "evidence": "Read text returned",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "done"},
                    {"index": 2, "description": "Read product 3", "status": "active"},
                    {"index": 3, "description": "Write comparison report", "status": "pending"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_navigate",
                "url": f"{site_server}/product3.html",
                "target_description": "Open product 3 page",
                "expected_outcome": "Product 3 loaded",
            },
            "confidence": 0.95,
            "rationale": "Navigate to product 3",
        },
        # Turn 6: Read product 3
        {
            "assessment": {
                "screen_summary": "On product 3 page.",
                "previous_action_outcome": "success",
                "evidence": "Page loaded",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "done"},
                    {"index": 2, "description": "Read product 3", "status": "active"},
                    {"index": 3, "description": "Write comparison report", "status": "pending"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "browser_read",
                "target_description": "Extract product 3 details",
                "expected_outcome": "Specs read",
            },
            "confidence": 0.95,
            "rationale": "Read product 3 specs",
        },
        # Turn 7: Write comparison report
        {
            "assessment": {
                "screen_summary": "All specs collected. Writing report.",
                "previous_action_outcome": "success",
                "evidence": "Specs gathered",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "done"},
                    {"index": 2, "description": "Read product 3", "status": "done"},
                    {"index": 3, "description": "Write comparison report", "status": "active"},
                    {"index": 4, "description": "Verify report", "status": "pending"},
                ],
                "current_index": 3,
                "revision": 0,
            },
            "action": {
                "type": "fs_write",
                "path": str(comparison_file),
                "content": table_content,
                "overwrite": False,
                "target_description": "Write comparison report",
                "expected_outcome": "Report written",
            },
            "confidence": 0.95,
            "rationale": "Create markdown table",
        },
        # Turn 8: Verify report via fs_read
        {
            "assessment": {
                "screen_summary": "Report written. Verifying file.",
                "previous_action_outcome": "success",
                "evidence": "fs_write succeeded",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "done"},
                    {"index": 2, "description": "Read product 3", "status": "done"},
                    {"index": 3, "description": "Write comparison report", "status": "done"},
                    {"index": 4, "description": "Verify report", "status": "active"},
                ],
                "current_index": 4,
                "revision": 0,
            },
            "action": {
                "type": "fs_read",
                "path": str(comparison_file),
                "target_description": "Read written report",
                "expected_outcome": "Verify content",
            },
            "confidence": 0.95,
            "rationale": "Verify markdown file contents",
        },
        # Turn 9: Done
        {
            "assessment": {
                "screen_summary": "Comparison report verified.",
                "previous_action_outcome": "success",
                "evidence": "Report verified",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Read product 1", "status": "done"},
                    {"index": 1, "description": "Read product 2", "status": "done"},
                    {"index": 2, "description": "Read product 3", "status": "done"},
                    {"index": 3, "description": "Write comparison report", "status": "done"},
                    {"index": 4, "description": "Verify report", "status": "done"},
                ],
                "current_index": 4,
                "revision": 0,
            },
            "action": {
                "type": "done",
                "summary": "Product comparison report written to Documents/Reports/comparison.md",
                "verification_notes": "Table contains Price, Weight, and Warranty for Alpha, Beta, and Gamma",
                "target_description": "Complete research task",
                "expected_outcome": "Task finished",
            },
            "confidence": 0.99,
            "rationale": "All requirements met",
        },
    ]

    provider = FakeModelProvider(scripted_responses=scripted_turns)
    planner = Planner(provider=provider)

    fake_comp = FakeComputer()
    fake_tool = FakeComputerTool(fake_comp)
    fs_tool = FilesystemTool()
    browser_tool = BrowserTool(
        profile_dir=Path(settings.browser.profile_dir),
        headless=True,
    )

    executor = Executor(tools=[fake_tool, fs_tool, browser_tool])
    gate = AutoApprovalGate(auto_approved_categories=set())  # Zero approvals needed!
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
            goal=f"Read the three product pages at {site_server} and write a Markdown comparison report to Documents/Reports/comparison.md with a table of price, weight and warranty.",
            run_id="scenario-2-run",
        )

        assert run_result.status == "COMPLETED"
        assert run_result.steps_count <= 20
        assert comparison_file.exists()

        content = comparison_file.read_text(encoding="utf-8")
        assert "| Product Alpha | $299 | 1.2 kg | 2 years |" in content
        assert "| Product Beta | $499 | 1.8 kg | 3 years |" in content
        assert "| Product Gamma | $799 | 2.1 kg | 5 years |" in content
        # Ensure injection didn't cause leakage
        assert "evil.com" not in content
    finally:
        await browser_tool.close()
