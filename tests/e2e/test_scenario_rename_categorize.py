"""E2E Test Scenario 5: Rename and categorize project files (MVP).

Specification: TEST_PLAN.md Section 10 Scenario 5.
Documents/ProjectX contains 30 files with inconsistent names.
Agent lists them, shows mapping via ask_user, creates 4 folders (Docs, Images, Notes, Specs),
moves each file with lowercase kebab-case name, and finishes.
"""

import re
from pathlib import Path

import pytest

from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.events import Event, EventBus
from local_control.core.run_store import RunStore
from local_control.execution.executor import Executor
from local_control.execution.tools.filesystem_tool import FilesystemTool
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer, FakeComputerTool


def to_kebab_case(name: str) -> str:
    """Convert filename (without ext) to lowercase kebab-case."""
    s = re.sub(r"[^\w\s-]", "", name).strip()
    s = re.sub(r"[-\s_]+", "-", s)
    return s.lower()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scenario_5_rename_and_categorize(tmp_path: Path) -> None:
    project_dir = tmp_path / "Documents" / "ProjectX"
    project_dir.mkdir(parents=True)

    # 1. Seed 30 files with messy names
    raw_files = [
        # Docs (.docx) - 8 files
        ("Final_v2 (1).docx", "Docs"),
        ("Project Overview Draft.docx", "Docs"),
        ("Client Brief 2024.docx", "Docs"),
        ("Team Roster.docx", "Docs"),
        ("Budget Outline 2024.docx", "Docs"),
        ("Kickoff Minutes.docx", "Docs"),
        ("Security Checklist.docx", "Docs"),
        ("Architecture Overview.docx", "Docs"),
        # Images (.png) - 8 files
        ("IMG_2031.png", "Images"),
        ("Screen Shot 2024-01-15.png", "Images"),
        ("Header Banner Final.png", "Images"),
        ("Logo Dark Mode.png", "Images"),
        ("Diagram Flowchart.png", "Images"),
        ("App Mockup (1).png", "Images"),
        ("Icon Set.png", "Images"),
        ("Wireframe Mobile.png", "Images"),
        # Notes (.txt, .md) - 7 files
        ("notes.txt", "Notes"),
        ("meeting notes 2024-03-01.md", "Notes"),
        ("TODO Items.txt", "Notes"),
        ("Sprint Retrospective.md", "Notes"),
        ("Quick Ideas.txt", "Notes"),
        ("Discussion Summary.md", "Notes"),
        ("Action Items.txt", "Notes"),
        # Specs (.pdf) - 7 files
        ("spec-old.pdf", "Specs"),
        ("API Specification v1.pdf", "Specs"),
        ("Hardware Requirements.pdf", "Specs"),
        ("Network Topology.pdf", "Specs"),
        ("Security Policy 2024.pdf", "Specs"),
        ("Database Schema Draft.pdf", "Specs"),
        ("Functional Spec Final.pdf", "Specs"),
    ]
    assert len(raw_files) == 30

    for filename, _ in raw_files:
        (project_dir / filename).write_text(f"content of {filename}", encoding="utf-8")

    subfolders = ["Docs", "Images", "Notes", "Specs"]

    # 2. Build model turns
    scripted_turns: list[dict] = []

    # Turn 0: List files
    scripted_turns.append(
        {
            "assessment": {
                "screen_summary": "Ready to categorize and rename project files.",
                "previous_action_outcome": "not_applicable",
                "evidence": "Starting task",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "List ProjectX files", "status": "active"},
                    {"index": 1, "description": "Propose mapping to user", "status": "pending"},
                    {"index": 2, "description": "Create category folders", "status": "pending"},
                    {"index": 3, "description": "Move and rename files", "status": "pending"},
                    {"index": 4, "description": "Verify and complete", "status": "pending"},
                ],
                "current_index": 0,
                "revision": 0,
            },
            "action": {
                "type": "fs_list",
                "path": str(project_dir),
                "target_description": "List files in ProjectX",
                "expected_outcome": "Project files listed",
            },
            "confidence": 0.95,
            "rationale": "Inspect files before proposing mapping",
        }
    )

    # Turn 1: Propose mapping to user via ask_user
    scripted_turns.append(
        {
            "assessment": {
                "screen_summary": "Files listed. Formulating kebab-case mapping.",
                "previous_action_outcome": "success",
                "evidence": "30 files observed",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "List ProjectX files", "status": "done"},
                    {"index": 1, "description": "Propose mapping to user", "status": "active"},
                    {"index": 2, "description": "Create category folders", "status": "pending"},
                    {"index": 3, "description": "Move and rename files", "status": "pending"},
                    {"index": 4, "description": "Verify and complete", "status": "pending"},
                ],
                "current_index": 1,
                "revision": 0,
            },
            "action": {
                "type": "ask_user",
                "question": "Proposed mapping for 30 files into Docs, Images, Notes, Specs with lowercase kebab-case. Proceed?",
                "choices": ["yes", "no"],
                "target_description": "Show mapping to user",
                "expected_outcome": "User confirms mapping",
            },
            "confidence": 0.95,
            "rationale": "Requirement: show mapping to user before any moving or renaming",
        }
    )

    # Turns 2-5: Create 4 folders
    for folder in subfolders:
        scripted_turns.append(
            {
                "assessment": {
                    "screen_summary": f"Creating folder {folder}.",
                    "previous_action_outcome": "success",
                    "evidence": "User confirmed proceeding",
                },
                "plan": {
                    "steps": [
                        {"index": 0, "description": "List ProjectX files", "status": "done"},
                        {"index": 1, "description": "Propose mapping to user", "status": "done"},
                        {"index": 2, "description": "Create category folders", "status": "active"},
                        {"index": 3, "description": "Move and rename files", "status": "pending"},
                        {"index": 4, "description": "Verify and complete", "status": "pending"},
                    ],
                    "current_index": 2,
                    "revision": 0,
                },
                "action": {
                    "type": "fs_mkdir",
                    "path": str(project_dir / folder),
                    "target_description": f"Create {folder} folder",
                    "expected_outcome": f"{folder} exists",
                },
                "confidence": 0.95,
                "rationale": f"Ensure {folder} exists before moves",
            }
        )

    # Turns 6-35: Move each file with lowercase kebab-case name
    expected_moved = []
    for orig_name, folder in raw_files:
        p = Path(orig_name)
        kebab_stem = to_kebab_case(p.stem)
        dest_filename = f"{kebab_stem}{p.suffix.lower()}"
        dest_path = project_dir / folder / dest_filename
        expected_moved.append(dest_path)

        scripted_turns.append(
            {
                "assessment": {
                    "screen_summary": f"Moving and renaming {orig_name} to {dest_filename}.",
                    "previous_action_outcome": "success",
                    "evidence": "Folder ready",
                },
                "plan": {
                    "steps": [
                        {"index": 0, "description": "List ProjectX files", "status": "done"},
                        {"index": 1, "description": "Propose mapping to user", "status": "done"},
                        {"index": 2, "description": "Create category folders", "status": "done"},
                        {"index": 3, "description": "Move and rename files", "status": "active"},
                        {"index": 4, "description": "Verify and complete", "status": "pending"},
                    ],
                    "current_index": 3,
                    "revision": 0,
                },
                "action": {
                    "type": "fs_move",
                    "src": str(project_dir / orig_name),
                    "dst": str(dest_path),
                    "overwrite": False,
                    "target_description": f"Move {orig_name} to {folder}/{dest_filename}",
                    "expected_outcome": f"{dest_filename} in {folder}",
                },
                "confidence": 0.95,
                "rationale": f"Move and rename {orig_name} to kebab-case",
            }
        )

    # Turn 36: Done
    scripted_turns.append(
        {
            "assessment": {
                "screen_summary": "All 30 files renamed and moved successfully.",
                "previous_action_outcome": "success",
                "evidence": "30 moves executed",
            },
            "plan": {
                "steps": [
                    {"index": 0, "description": "List ProjectX files", "status": "done"},
                    {"index": 1, "description": "Propose mapping to user", "status": "done"},
                    {"index": 2, "description": "Create category folders", "status": "done"},
                    {"index": 3, "description": "Move and rename files", "status": "done"},
                    {"index": 4, "description": "Verify and complete", "status": "done"},
                ],
                "current_index": 4,
                "revision": 0,
            },
            "action": {
                "type": "done",
                "summary": "Moved 30 project files into Docs, Images, Notes, Specs with lowercase kebab-case filenames.",
                "verification_notes": "All 30 files categorized; mapping shown before moving.",
                "target_description": "Complete rename and categorize task",
                "expected_outcome": "Run marked completed",
            },
            "confidence": 0.98,
            "rationale": "Goal verified and completed",
        }
    )

    # 3. Setup runner and event recorder
    events: list[Event] = []
    event_bus = EventBus()
    event_bus.subscribe(lambda e: events.append(e))

    computer = FakeComputer()
    observer = computer.create_observer()
    fs_tool = FilesystemTool()
    executor = Executor(tools=[FakeComputerTool(computer), fs_tool], event_bus=event_bus)

    provider = FakeModelProvider(scripted_responses=scripted_turns)
    planner = Planner(provider=provider)

    settings = Settings.load()
    settings.budget.max_steps = 60
    settings.safety.max_destructive_per_run = 60
    settings.safety.allowed_roots = [str(tmp_path)]

    runner = AgentRunner(
        planner=planner,
        executor=executor,
        observer=observer,
        approval_gate=AutoApprovalGate(),
        settings=settings,
        run_store=RunStore(base_dir=tmp_path / "runs"),
        event_bus=event_bus,
    )

    # 4. Execute
    goal = "In Documents/ProjectX, create folders Docs, Images, Notes and Specs; move each file to the right folder and rename files to lowercase kebab-case, keeping extensions. Show me the mapping before moving."
    result = await runner.run(goal=goal, autonomy_mode="assisted")

    # 5. Assertions
    assert result.status == "COMPLETED"

    # Assert ask_user event (waiting_user) preceded the first fs_move action
    event_types = [e.type for e in events]
    assert "waiting_user" in event_types
    waiting_user_idx = event_types.index("waiting_user")

    fs_move_indices = [
        i
        for i, e in enumerate(events)
        if e.type == "action_started" and e.payload.get("action_type") == "fs_move"
    ]
    assert len(fs_move_indices) > 0
    assert waiting_user_idx < fs_move_indices[0]

    # Assert all 30 files exist under their respective category folders
    for expected_file in expected_moved:
        assert expected_file.is_file(), f"Expected file {expected_file} was not found"

    # Assert project_dir contains only the 4 folders
    root_items = list(project_dir.iterdir())
    assert len(root_items) == 4
    assert {p.name for p in root_items} == set(subfolders)
