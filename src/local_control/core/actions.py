"""Typed action vocabulary for local-control."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Point(BaseModel):
    """2D coordinate point."""

    model_config = ConfigDict(frozen=True)

    x: int
    y: int


class Rect(BaseModel):
    """Bounding rectangle."""

    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    width: int
    height: int


class ActionBase(BaseModel):
    """Base envelope for all proposed actions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    target_description: str = Field(
        ...,
        description="Human-readable description of target and intent, shown in approvals",
    )
    expected_outcome: str = Field(
        ...,
        description="Observable result expected on screen or system state after execution",
    )
    settle_ms: int = Field(
        default=0,
        ge=0,
        description="Milliseconds to wait after action execution before observing",
    )


# --- GUI Actions ---


class ClickAction(ActionBase):
    """Send mouse click at image-space coordinates or UI element ref."""

    type: Literal["click"] = "click"
    x: int | None = None
    y: int | None = None
    ref: str | None = None
    button: Literal["left", "right", "middle"] = "left"
    clicks: Literal[1, 2] = 1
    settle_ms: int = 500

    @model_validator(mode="after")
    def validate_target(self) -> "ClickAction":
        if (self.x is None or self.y is None) and not self.ref:
            raise ValueError("ClickAction requires either (x, y) coordinates or ref.")
        return self


class MoveMouseAction(ActionBase):
    """Move cursor to image-space coordinates or UI element ref."""

    type: Literal["move_mouse"] = "move_mouse"
    x: int | None = None
    y: int | None = None
    ref: str | None = None
    settle_ms: int = 100

    @model_validator(mode="after")
    def validate_target(self) -> "MoveMouseAction":
        if (self.x is None or self.y is None) and not self.ref:
            raise ValueError("MoveMouseAction requires either (x, y) coordinates or ref.")
        return self


class DragAction(ActionBase):
    """Drag mouse from start point to destination point."""

    type: Literal["drag"] = "drag"
    from_point: Point = Field(..., alias="from")
    to_point: Point = Field(..., alias="to")
    button: Literal["left", "right", "middle"] = "left"
    duration_ms: int = Field(default=500, ge=0)
    settle_ms: int = 500


class ScrollAction(ActionBase):
    """Scroll mouse wheel at specified coordinates."""

    type: Literal["scroll"] = "scroll"
    x: int
    y: int
    dx: int = 0
    dy: int = Field(..., description="Scroll notches (positive up, negative down)")
    settle_ms: int = 400


class TypeTextAction(ActionBase):
    """Type Unicode text into the active focused element."""

    type: Literal["type_text"] = "type_text"
    text: str = Field(..., max_length=4000)
    settle_ms: int = 300


class PressKeysAction(ActionBase):
    """Press keyboard hotkey combination."""

    type: Literal["press_keys"] = "press_keys"
    keys: list[str] = Field(..., min_length=1)
    settle_ms: int = 400


# --- Window Actions ---


class FocusWindowAction(ActionBase):
    """Bring a window to foreground by window handle."""

    type: Literal["focus_window"] = "focus_window"
    handle: int
    settle_ms: int = 400


class ListWindowsAction(ActionBase):
    """List all visible top-level windows."""

    type: Literal["list_windows"] = "list_windows"
    settle_ms: int = 0


class CloseWindowAction(ActionBase):
    """Close window by window handle."""

    type: Literal["close_window"] = "close_window"
    handle: int
    settle_ms: int = 600


class AppTarget(BaseModel):
    """Semantic target specification for launching or focusing an application."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    type: Literal["application"] = "application"
    name: str = Field(..., description="Common or formal name of the application, e.g. 'Discord'")
    process_name: str | None = Field(
        default=None, alias="processName", description="Process executable name, e.g. 'Discord.exe'"
    )
    window_title_pattern: str | None = Field(
        default=None, alias="windowTitlePattern", description="Regex or pattern for window title"
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Resolution confidence score")
    strategy: str | None = Field(
        default=None, description="Resolution strategy used, e.g. 'running_window', 'executable'"
    )


class OpenApplicationAction(ActionBase):
    """Open or focus an application by semantic target identity."""

    type: Literal["open_application"] = "open_application"
    target: AppTarget
    settle_ms: int = 1000

    @model_validator(mode="before")
    @classmethod
    def normalize_target(cls, data: Any) -> Any:
        if isinstance(data, dict):
            tgt = data.get("target")
            if isinstance(tgt, str):
                data["target"] = {"name": tgt}
            name = ""
            if isinstance(tgt, dict):
                name = tgt.get("name", "application")
            elif isinstance(tgt, str):
                name = tgt
            else:
                name = "application"
            if not data.get("target_description"):
                data["target_description"] = f"Open {name}"
            if not data.get("expected_outcome"):
                data["expected_outcome"] = f"{name} is the foreground window"
        return data


# --- Observation & Wait Actions ---


class WaitAction(ActionBase):
    """Wait for a specified duration."""

    type: Literal["wait"] = "wait"
    seconds: float = Field(..., ge=0.0, le=30.0)
    settle_ms: int = 0


class ZoomRegionAction(ActionBase):
    """Capture full-resolution crop of specified region."""

    type: Literal["zoom_region"] = "zoom_region"
    rect: Rect
    settle_ms: int = 0


class OcrRegionAction(ActionBase):
    """Run OCR over specified region."""

    type: Literal["ocr_region"] = "ocr_region"
    rect: Rect
    settle_ms: int = 0


class ReadUiTreeAction(ActionBase):
    """Read UIA element tree of window."""

    type: Literal["read_ui_tree"] = "read_ui_tree"
    handle: int
    settle_ms: int = 0


# --- Agent Control Actions ---


class AskUserAction(ActionBase):
    """Pause execution and ask user for clarification."""

    type: Literal["ask_user"] = "ask_user"
    question: str
    choices: list[str] | None = None
    settle_ms: int = 0


class DoneAction(ActionBase):
    """Signal that the goal is complete and verified."""

    type: Literal["done"] = "done"
    summary: str
    verification_notes: str
    settle_ms: int = 0


class FailAction(ActionBase):
    """Signal that the goal cannot be completed."""

    type: Literal["fail"] = "fail"
    reason: str
    settle_ms: int = 0


# --- Filesystem Actions ---


class FsListAction(ActionBase):
    """List directory entries."""

    type: Literal["fs_list"] = "fs_list"
    path: str
    recursive: bool = False
    max_entries: int = 500
    settle_ms: int = 0


class FsReadAction(ActionBase):
    """Read text file contents."""

    type: Literal["fs_read"] = "fs_read"
    path: str
    max_bytes: int = 65536
    encoding: str = "utf-8"
    settle_ms: int = 0


class FsStatAction(ActionBase):
    """Get metadata for file or directory."""

    type: Literal["fs_stat"] = "fs_stat"
    path: str
    settle_ms: int = 0


class FsMkdirAction(ActionBase):
    """Create directory."""

    type: Literal["fs_mkdir"] = "fs_mkdir"
    path: str
    settle_ms: int = 0


class FsWriteAction(ActionBase):
    """Write text file content."""

    type: Literal["fs_write"] = "fs_write"
    path: str
    content: str
    overwrite: bool = False
    settle_ms: int = 0


class FsCopyAction(ActionBase):
    """Copy file or directory."""

    type: Literal["fs_copy"] = "fs_copy"
    src: str
    dst: str
    overwrite: bool = False
    settle_ms: int = 0


class FsMoveAction(ActionBase):
    """Move file or directory."""

    type: Literal["fs_move"] = "fs_move"
    src: str
    dst: str
    overwrite: bool = False
    settle_ms: int = 0


class FsDeleteAction(ActionBase):
    """Send file or directory to Recycle Bin."""

    type: Literal["fs_delete"] = "fs_delete"
    path: str
    settle_ms: int = 0


# --- Terminal Actions ---


class ShellRunAction(ActionBase):
    """Run non-interactive PowerShell command."""

    type: Literal["shell_run"] = "shell_run"
    command: str
    cwd: str | None = None
    timeout_s: int = 60
    settle_ms: int = 0


# --- Browser Actions ---


class BrowserNavigateAction(ActionBase):
    """Navigate browser to URL."""

    type: Literal["browser_navigate"] = "browser_navigate"
    url: str
    settle_ms: int = 0


class BrowserClickAction(ActionBase):
    """Click element by snapshot ref or CSS selector."""

    type: Literal["browser_click"] = "browser_click"
    ref: str | None = None
    selector: str | None = None
    settle_ms: int = 500


class BrowserTypeAction(ActionBase):
    """Type into browser element."""

    type: Literal["browser_type"] = "browser_type"
    ref: str | None = None
    selector: str | None = None
    text: str
    submit: bool = False
    settle_ms: int = 300


class BrowserReadAction(ActionBase):
    """Read visible text from page or element."""

    type: Literal["browser_read"] = "browser_read"
    selector: str | None = None
    max_chars: int = 20000
    settle_ms: int = 0


class BrowserSnapshotAction(ActionBase):
    """Capture accessibility snapshot with element refs."""

    type: Literal["browser_snapshot"] = "browser_snapshot"
    settle_ms: int = 0


class BrowserBackAction(ActionBase):
    """Navigate back in browser history."""

    type: Literal["browser_back"] = "browser_back"
    settle_ms: int = 0


class BrowserTabsAction(ActionBase):
    """Manage browser tabs."""

    type: Literal["browser_tabs"] = "browser_tabs"
    op: Literal["list", "switch", "new", "close"]
    index: int | None = None
    settle_ms: int = 0


class BrowserDownloadAction(ActionBase):
    """Trigger file download via element interaction."""

    type: Literal["browser_download"] = "browser_download"
    dest_dir: str
    ref: str | None = None
    selector: str | None = None
    settle_ms: int = 0


# Complete Action vocabulary discriminated union
Action = Annotated[
    ClickAction
    | MoveMouseAction
    | DragAction
    | ScrollAction
    | TypeTextAction
    | PressKeysAction
    | FocusWindowAction
    | ListWindowsAction
    | CloseWindowAction
    | OpenApplicationAction
    | WaitAction
    | ZoomRegionAction
    | OcrRegionAction
    | ReadUiTreeAction
    | AskUserAction
    | DoneAction
    | FailAction
    | FsListAction
    | FsReadAction
    | FsStatAction
    | FsMkdirAction
    | FsWriteAction
    | FsCopyAction
    | FsMoveAction
    | FsDeleteAction
    | ShellRunAction
    | BrowserNavigateAction
    | BrowserClickAction
    | BrowserTypeAction
    | BrowserReadAction
    | BrowserSnapshotAction
    | BrowserBackAction
    | BrowserTabsAction
    | BrowserDownloadAction,
    Field(discriminator="type"),
]

ActionAdapter: TypeAdapter[Action] = TypeAdapter(Action)
