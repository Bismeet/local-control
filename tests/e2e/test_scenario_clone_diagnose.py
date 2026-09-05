"""E2E Test Scenario 3: Clone a repository and diagnose a failure (MVP).

Specification: TEST_PLAN.md Section 10 Scenario 3.
Repo contains a failing test caused by a misspelled module import.
Agent clones repo, runs tests, reads failing module, and reports diagnosis without modifying files.
"""

from pathlib import Path

import pytest

from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.events import EventBus
from local_control.core.run_store import RunStore
from local_control.execution.executor import Executor
from local_control.execution.tools.filesystem_tool import FilesystemTool
from local_control.execution.tools.terminal_tool import TerminalTool
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scenario_3_clone_and_diagnose(tmp_path: Path) -> None:
    # 1. Setup simulated broken repository
    repo_src = tmp_path / "broken-repo"
    repo_src.mkdir()
    (repo_src / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo_src / "test_calculator.py").write_text(
        "import calclator\n\ndef test_add():\n    assert calclator.add(1, 2) == 3\n",
        encoding="utf-8",
    )

    work_dir = tmp_path / "Documents" / "work"
    work_dir.mkdir(parents=True)
    target_clone = work_dir / "broken-repo"

    # 2. Build scripted actions for model
    scripted_turns = [
        # Turn 0: Copy/clone repo into work dir
        {
            "assessment": {
                "screen_summary": "Ready to clone broken repo into work folder.",
                "previous_action_outcome": "not_applicable",
                "evidence": "Starting clone diagnose task",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Clone repository", "status": "active"},
                    {"index": 1, "description": "Run tests", "status": "pending"},
                    {"index": 2, "description": "Diagnose failure", "status": "pending"},
                    {"index": 3, "description": "Complete run", "status": "pending"},
                ],
                "current_index": 0,
                "revision": 0,
            },
            "action": {
                "type": "fs_copy",
                "src": str(repo_src),
                "dst": str(target_clone),
                "overwrite": False,
                "target_description": "Clone repo to work folder",
                "expected_outcome": "Repo cloned",
            },
            "confidence": 0.95,
            "rationale": "Fetch repository files into work area",
        },
        # Turn 1: List cloned repo
        {
            "assessment": {
                "screen_summary": "Repository cloned. Listing contents.",
                "previous_action_outcome": "success",
                "evidence": "Clone directory created",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Clone repository", "status": "done"},
                    {"index": 1, "description": "Inspect repository files", "status": "active"},
                    {"index": 2, "description": "Run tests", "status": "pending"},
                    {"index": 3, "description": "Diagnose failure", "status": "pending"},
                ],
                "current_index": 1,
                "revision": 0,
            },
            "action": {
                "type": "fs_list",
                "path": str(target_clone),
                "target_description": "List cloned files",
                "expected_outcome": "Files listed",
            },
            "confidence": 0.95,
            "rationale": "Check structure of cloned project",
        },
        # Turn 2: Run tests via shell_run
        {
            "assessment": {
                "screen_summary": "Running project tests.",
                "previous_action_outcome": "success",
                "evidence": "Files listed: calculator.py, test_calculator.py",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Clone repository", "status": "done"},
                    {"index": 1, "description": "Inspect repository files", "status": "done"},
                    {"index": 2, "description": "Run tests", "status": "active"},
                    {"index": 3, "description": "Diagnose failure", "status": "pending"},
                ],
                "current_index": 2,
                "revision": 0,
            },
            "action": {
                "type": "shell_run",
                "command": "python -m pytest test_calculator.py",
                "cwd": str(target_clone),
                "target_description": "Execute pytest on test_calculator.py",
                "expected_outcome": "Test results captured",
            },
            "confidence": 0.90,
            "rationale": "Run test suite to observe test error",
        },
        # Turn 3: Read test file to investigate the failure
        {
            "assessment": {
                "screen_summary": "Test execution produced ModuleNotFoundError: No module named 'calclator'.",
                "previous_action_outcome": "failure",
                "evidence": "Pytest failed with import error on calclator",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Clone repository", "status": "done"},
                    {"index": 1, "description": "Inspect repository files", "status": "done"},
                    {"index": 2, "description": "Run tests", "status": "done"},
                    {"index": 3, "description": "Read failing test source", "status": "active"},
                ],
                "current_index": 3,
                "revision": 0,
            },
            "action": {
                "type": "fs_read",
                "path": str(target_clone / "test_calculator.py"),
                "target_description": "Read test file to verify import typo",
                "expected_outcome": "File contents showing import calclator",
            },
            "confidence": 0.95,
            "rationale": "Verify typo in import statement",
        },
        # Turn 4: Complete run with diagnosis
        {
            "assessment": {
                "screen_summary": "Confirmed test failure cause: misspelled import 'calclator'.",
                "previous_action_outcome": "success",
                "evidence": "test_calculator.py imports calclator instead of calculator",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "Clone repository", "status": "done"},
                    {"index": 1, "description": "Inspect repository files", "status": "done"},
                    {"index": 2, "description": "Run tests", "status": "done"},
                    {"index": 3, "description": "Read failing test source", "status": "done"},
                    {"index": 4, "description": "Report diagnosis", "status": "done"},
                ],
                "current_index": 4,
                "revision": 0,
            },
            "action": {
                "type": "done",
                "summary": "Tests fail due to misspelled module import: 'calclator' should be 'calculator'.",
                "verification_notes": "Diagnostic confirmed: ModuleNotFoundError for 'calclator'. No files modified.",
                "target_description": "Finish diagnosis task",
                "expected_outcome": "Run marked completed",
            },
            "confidence": 0.98,
            "rationale": "Task complete: diagnosis identified without modifying code",
        },
    ]

    # 3. Setup Runner
    computer = FakeComputer()
    observer = computer.create_observer()
    fs_tool = FilesystemTool()
    term_tool = TerminalTool()
    executor = Executor(tools=[FakeComputerTool(computer), fs_tool, term_tool])

    provider = FakeModelProvider(scripted_responses=scripted_turns)
    planner = Planner(provider=provider)

    settings = Settings.load()
    settings.budget.max_steps = 20
    settings.safety.allowed_roots = [str(tmp_path)]

    runner = AgentRunner(
        planner=planner,
        executor=executor,
        observer=observer,
        approval_gate=AutoApprovalGate(),
        settings=settings,
        run_store=RunStore(base_dir=tmp_path / "runs"),
        event_bus=EventBus(),
    )

    # 4. Execute
    goal = "Clone broken-repo into Documents/work, run its tests, and tell me why they fail. Do not fix anything."
    result = await runner.run(goal=goal, autonomy_mode="assisted")

    # 5. Assertions
    assert result.status == "COMPLETED"
    assert "calclator" in result.summary.lower()
    # Ensure no files modified in clone
    test_content = (target_clone / "test_calculator.py").read_text(encoding="utf-8")
    assert "import calclator" in test_content
