"""Unit tests for Action vocabulary and schema validation."""

import pytest
from pydantic import ValidationError

from local_control.core.actions import (
    Action,
    ActionAdapter,
    AskUserAction,
    BrowserBackAction,
    BrowserClickAction,
    BrowserDownloadAction,
    BrowserNavigateAction,
    BrowserReadAction,
    BrowserSnapshotAction,
    BrowserTabsAction,
    BrowserTypeAction,
    ClickAction,
    CloseWindowAction,
    DoneAction,
    DragAction,
    FailAction,
    FocusWindowAction,
    FsCopyAction,
    FsDeleteAction,
    FsListAction,
    FsMkdirAction,
    FsMoveAction,
    FsReadAction,
    FsStatAction,
    FsWriteAction,
    ListWindowsAction,
    MoveMouseAction,
    OcrRegionAction,
    Point,
    PressKeysAction,
    ReadUiTreeAction,
    Rect,
    ScrollAction,
    ShellRunAction,
    TypeTextAction,
    WaitAction,
    ZoomRegionAction,
)

SAMPLE_ACTIONS: list[Action] = [
    ClickAction(
        target_description="Click Submit button",
        expected_outcome="Form submitted",
        x=100,
        y=200,
        button="left",
        clicks=1,
    ),
    MoveMouseAction(
        target_description="Hover over menu",
        expected_outcome="Menu opens",
        x=150,
        y=50,
    ),
    DragAction(
        target_description="Drag slider to 50%",
        expected_outcome="Volume increases",
        from_point=Point(x=10, y=10),
        to_point=Point(x=100, y=10),
    ),
    ScrollAction(
        target_description="Scroll down document",
        expected_outcome="Next page visible",
        x=500,
        y=500,
        dy=-3,
    ),
    TypeTextAction(
        target_description="Type user greeting",
        expected_outcome="Greeting appears in text input",
        text="Hello world!",
    ),
    PressKeysAction(
        target_description="Select all text",
        expected_outcome="All text highlighted",
        keys=["ctrl", "a"],
    ),
    FocusWindowAction(
        target_description="Focus Notepad window",
        expected_outcome="Notepad in foreground",
        handle=123456,
    ),
    ListWindowsAction(
        target_description="List active windows",
        expected_outcome="Window list updated",
    ),
    CloseWindowAction(
        target_description="Close test window",
        expected_outcome="Window closed",
        handle=123456,
    ),
    WaitAction(
        target_description="Wait for animation",
        expected_outcome="Animation completes",
        seconds=2.5,
    ),
    ZoomRegionAction(
        target_description="Zoom on tiny icon",
        expected_outcome="High resolution icon visible",
        rect=Rect(x=10, y=10, width=50, height=50),
    ),
    OcrRegionAction(
        target_description="OCR error dialog",
        expected_outcome="Error text extracted",
        rect=Rect(x=100, y=100, width=300, height=200),
    ),
    ReadUiTreeAction(
        target_description="Inspect dialog controls",
        expected_outcome="UIA tree captured",
        handle=123456,
    ),
    AskUserAction(
        target_description="Ask user for preferred folder",
        expected_outcome="User provides directory name",
        question="Where would you like to save the files?",
        choices=["Folder A", "Folder B"],
    ),
    DoneAction(
        target_description="Goal finished",
        expected_outcome="Task verified complete",
        summary="All files organized into subfolders.",
        verification_notes="Verified 25 files in destination folders.",
    ),
    FailAction(
        target_description="Goal impossible",
        expected_outcome="Agent stops with error",
        reason="Required application is not installed.",
    ),
    FsListAction(
        target_description="List Downloads folder",
        expected_outcome="Directory entries listed",
        path="C:\\Users\\test\\Downloads",
    ),
    FsReadAction(
        target_description="Read config file",
        expected_outcome="File contents loaded",
        path="C:\\Users\\test\\config.txt",
    ),
    FsStatAction(
        target_description="Check if file exists",
        expected_outcome="File metadata returned",
        path="C:\\Users\\test\\file.dat",
    ),
    FsMkdirAction(
        target_description="Create PDF subfolder",
        expected_outcome="Directory created",
        path="C:\\Users\\test\\Downloads\\PDFs",
    ),
    FsWriteAction(
        target_description="Write report",
        expected_outcome="Report file created",
        path="C:\\Users\\test\\report.md",
        content="# Report\nAll good.",
    ),
    FsCopyAction(
        target_description="Backup config",
        expected_outcome="Backup file created",
        src="C:\\Users\\test\\config.txt",
        dst="C:\\Users\\test\\config.bak",
    ),
    FsMoveAction(
        target_description="Move file to PDFs",
        expected_outcome="File moved",
        src="C:\\Users\\test\\doc.pdf",
        dst="C:\\Users\\test\\PDFs\\doc.pdf",
    ),
    FsDeleteAction(
        target_description="Send temp file to Recycle Bin",
        expected_outcome="File moved to Recycle Bin",
        path="C:\\Users\\test\\temp.tmp",
    ),
    ShellRunAction(
        target_description="Run git status",
        expected_outcome="Working tree status reported",
        command="git status",
    ),
    BrowserNavigateAction(
        target_description="Open documentation page",
        expected_outcome="Documentation loaded",
        url="https://example.com/docs",
    ),
    BrowserClickAction(
        target_description="Click Next link",
        expected_outcome="Next page loaded",
        ref="b12",
    ),
    BrowserTypeAction(
        target_description="Enter search query",
        expected_outcome="Search results displayed",
        selector="input#search",
        text="Windows automation",
        submit=True,
    ),
    BrowserReadAction(
        target_description="Read article content",
        expected_outcome="Article text extracted",
        selector="article",
    ),
    BrowserSnapshotAction(
        target_description="Take accessibility snapshot",
        expected_outcome="DOM tree with refs returned",
    ),
    BrowserBackAction(
        target_description="Go back to previous page",
        expected_outcome="Previous page displayed",
    ),
    BrowserTabsAction(
        target_description="Switch to second tab",
        expected_outcome="Tab 1 focused",
        op="switch",
        index=1,
    ),
    BrowserDownloadAction(
        target_description="Download manual",
        expected_outcome="Manual downloaded to target dir",
        ref="b44",
        dest_dir="C:\\Users\\test\\Downloads",
    ),
]


@pytest.mark.unit
def test_all_33_action_types_covered() -> None:
    """Ensure our sample list covers all 33 distinct action types."""
    action_types = {action.type for action in SAMPLE_ACTIONS}
    assert len(action_types) == 33
    assert len(SAMPLE_ACTIONS) == 33


@pytest.mark.unit
@pytest.mark.parametrize("action", SAMPLE_ACTIONS, ids=lambda a: a.type)
def test_action_roundtrip_serialization(action: Action) -> None:
    """Verify that every action model serializes and roundtrips without data loss."""
    json_data = ActionAdapter.dump_json(action)
    reloaded = ActionAdapter.validate_json(json_data)
    assert reloaded == action
    assert reloaded.type == action.type


@pytest.mark.unit
def test_action_json_schema_export() -> None:
    """Verify Action JSON schema can be exported for model structured outputs."""
    schema = ActionAdapter.json_schema()
    assert "discriminator" in str(schema)
    assert "oneOf" in schema or "$defs" in schema


@pytest.mark.unit
def test_reject_unknown_action_type() -> None:
    """Verify that an unknown action type raises a ValidationError."""
    payload = {
        "type": "unregistered_magic_action",
        "target_description": "Do magic",
        "expected_outcome": "Magic done",
    }
    with pytest.raises(ValidationError):
        ActionAdapter.validate_python(payload)


@pytest.mark.unit
def test_reject_missing_required_fields() -> None:
    """Verify missing required coordinates for click raises ValidationError."""
    payload = {
        "type": "click",
        "target_description": "Click without coordinates",
        "expected_outcome": "Should fail",
    }
    with pytest.raises(ValidationError):
        ActionAdapter.validate_python(payload)


@pytest.mark.unit
def test_reject_type_text_exceeding_length_cap() -> None:
    """Verify type_text with text > 4000 characters is rejected."""
    long_text = "a" * 4001
    with pytest.raises(ValidationError):
        TypeTextAction(
            target_description="Too long text",
            expected_outcome="Rejected",
            text=long_text,
        )


@pytest.mark.unit
def test_reject_wait_exceeding_30_seconds() -> None:
    """Verify wait > 30.0 seconds is rejected."""
    with pytest.raises(ValidationError):
        WaitAction(
            target_description="Sleep too long",
            expected_outcome="Rejected",
            seconds=30.1,
        )


@pytest.mark.unit
def test_drag_aliased_fields() -> None:
    """Verify DragAction supports both 'from'/'to' aliases and python names."""
    payload = {
        "type": "drag",
        "target_description": "Drag item",
        "expected_outcome": "Item moved",
        "from": {"x": 10, "y": 20},
        "to": {"x": 30, "y": 40},
    }
    action = ActionAdapter.validate_python(payload)
    assert isinstance(action, DragAction)
    assert action.from_point.x == 10
    assert action.to_point.y == 40
