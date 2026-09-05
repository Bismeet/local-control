"""E2E Test Scenario 1: Organize Downloads (MVP).

Specification: TEST_PLAN.md Section 10 Scenario 1.
Seed 25 files (8 PDFs, 6 images, 5 archives, 4 installers, 2 text files).
Agent organizes them into subfolders: PDFs, Images, Archives, Installers, Other.
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
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scenario_1_organize_downloads(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "Downloads"
    downloads_dir.mkdir()

    # 1. Seed 25 files
    pdf_files = [f"doc_{i}.pdf" for i in range(1, 9)]
    img_files = [f"img_{i}.jpg" for i in range(1, 4)] + [f"screen_{i}.png" for i in range(1, 4)]
    archive_files = [f"backup_{i}.zip" for i in range(1, 6)]
    installer_files = [f"app_{i}.exe" for i in range(1, 3)] + [f"pkg_{i}.msi" for i in range(1, 3)]
    other_files = ["notes.txt", "readme.txt"]

    all_seeded = pdf_files + img_files + archive_files + installer_files + other_files
    assert len(all_seeded) == 25

    for name in all_seeded:
        (downloads_dir / name).write_text(f"content of {name}", encoding="utf-8")

    subfolders = ["PDFs", "Images", "Archives", "Installers", "Other"]

    # 2. Build scripted actions for model
    scripted_turns: list[dict] = []

    # Step 1: List directory
    scripted_turns.append(
        {
            "assessment": {
                "screen_summary": "Ready to organize downloads folder.",
                "previous_action_outcome": "not_applicable",
                "evidence": "Starting task",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "List downloads folder", "status": "active"},
                    {"index": 1, "description": "Create subfolders", "status": "pending"},
                    {"index": 2, "description": "Move files by category", "status": "pending"},
                    {"index": 3, "description": "Verify and finish", "status": "pending"},
                ],
                "current_index": 0,
                "revision": 0,
            },
            "action": {
                "type": "fs_list",
                "path": str(downloads_dir),
                "recursive": False,
                "target_description": "List files in Downloads directory",
                "expected_outcome": "Files listed",
            },
            "confidence": 0.95,
            "rationale": "Inspect current downloads contents",
        }
    )

    # Step 2: Create subfolders
    for _idx, folder in enumerate(subfolders):
        scripted_turns.append(
            {
                "assessment": {
                    "screen_summary": "Creating subfolder.",
                    "previous_action_outcome": "success",
                    "evidence": "Directory listed or previous folder created",
                },
                "plan": {
                    "steps": [
                        {"index": 0, "description": "List downloads folder", "status": "done"},
                        {"index": 1, "description": "Create subfolders", "status": "active"},
                        {"index": 2, "description": "Move files by category", "status": "pending"},
                        {"index": 3, "description": "Verify and finish", "status": "pending"},
                    ],
                    "current_index": 1,
                    "revision": 0,
                },
                "action": {
                    "type": "fs_mkdir",
                    "path": str(downloads_dir / folder),
                    "target_description": f"Create {folder} folder",
                    "expected_outcome": f"{folder} directory exists",
                },
                "confidence": 0.95,
                "rationale": f"Create {folder} category subfolder",
            }
        )

    # Step 3: Move files to their respective subfolders
    mapping = []
    for f in pdf_files:
        mapping.append((f, "PDFs"))
    for f in img_files:
        mapping.append((f, "Images"))
    for f in archive_files:
        mapping.append((f, "Archives"))
    for f in installer_files:
        mapping.append((f, "Installers"))
    for f in other_files:
        mapping.append((f, "Other"))

    for src_name, dest_sub in mapping:
        scripted_turns.append(
            {
                "assessment": {
                    "screen_summary": "Moving files to target subfolders.",
                    "previous_action_outcome": "success",
                    "evidence": "Previous operation succeeded",
                },
                "plan": {
                    "steps": [
                        {"index": 0, "description": "List downloads folder", "status": "done"},
                        {"index": 1, "description": "Create subfolders", "status": "done"},
                        {"index": 2, "description": "Move files by category", "status": "active"},
                        {"index": 3, "description": "Verify and finish", "status": "pending"},
                    ],
                    "current_index": 2,
                    "revision": 0,
                },
                "action": {
                    "type": "fs_move",
                    "src": str(downloads_dir / src_name),
                    "dst": str(downloads_dir / dest_sub),
                    "overwrite": False,
                    "target_description": f"Move {src_name} to {dest_sub}",
                    "expected_outcome": f"{src_name} in {dest_sub}",
                },
                "confidence": 0.95,
                "rationale": f"Categorize {src_name} into {dest_sub}",
            }
        )

    # Step 4: Done action
    scripted_turns.append(
        {
            "assessment": {
                "screen_summary": "All 25 files categorized successfully.",
                "previous_action_outcome": "success",
                "evidence": "All move operations completed",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "List downloads folder", "status": "done"},
                    {"index": 1, "description": "Create subfolders", "status": "done"},
                    {"index": 2, "description": "Move files by category", "status": "done"},
                    {"index": 3, "description": "Verify and finish", "status": "done"},
                ],
                "current_index": 3,
                "revision": 0,
            },
            "action": {
                "type": "done",
                "summary": "Organized 25 downloads files into PDFs, Images, Archives, Installers, and Other.",
                "verification_notes": "All 25 files verified in their respective folders.",
                "target_description": "Signal completion",
                "expected_outcome": "Run marked completed",
            },
            "confidence": 0.98,
            "rationale": "Goal fully verified and achieved",
        }
    )

    # 3. Assemble runner environment
    computer = FakeComputer()
    observer = computer.create_observer()
    fs_tool = FilesystemTool()
    executor = Executor(tools=[FakeComputerTool(computer), fs_tool])

    provider = FakeModelProvider(scripted_responses=scripted_turns)
    planner = Planner(provider=provider)

    settings = Settings.load()
    settings.budget.max_steps = 50
    settings.safety.max_destructive_per_run = 60
    settings.safety.allowed_roots = [str(tmp_path)]

    run_store = RunStore(base_dir=tmp_path / "runs")
    event_bus = EventBus()

    runner = AgentRunner(
        planner=planner,
        executor=executor,
        observer=observer,
        approval_gate=AutoApprovalGate(),
        settings=settings,
        run_store=run_store,
        event_bus=event_bus,
    )

    # 4. Execute run
    goal = "Organize my Downloads folder into subfolders by file type: PDFs, Images, Archives, Installers, Other."
    result = await runner.run(goal=goal, autonomy_mode="assisted")

    # 5. Verify outcome
    assert result.steps_count == 31  # 1 list + 5 mkdir + 25 move = 31 steps
    assert result.steps_count <= 45  # within TEST_PLAN budget

    # Check files in subfolders
    for f in pdf_files:
        assert (downloads_dir / "PDFs" / f).is_file()
    for f in img_files:
        assert (downloads_dir / "Images" / f).is_file()
    for f in archive_files:
        assert (downloads_dir / "Archives" / f).is_file()
    for f in installer_files:
        assert (downloads_dir / "Installers" / f).is_file()
    for f in other_files:
        assert (downloads_dir / "Other" / f).is_file()

    # Verify no unorganized files in downloads root
    root_entries = list(downloads_dir.iterdir())
    assert len(root_entries) == 5
    assert {p.name for p in root_entries} == set(subfolders)
