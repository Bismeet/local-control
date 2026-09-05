"""Core contract types and domain models for local-control."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from local_control.core.actions import Action, Point, Rect

# Re-export Point and Rect
__all__ = [
    "Point",
    "Rect",
    "ScreenGeometry",
    "ImageRef",
    "ScreenState",
    "WindowInfo",
    "OcrSpan",
    "UiElement",
    "ErrorInfo",
    "ActionResult",
    "Observation",
    "Assessment",
    "PlanStepStatus",
    "PlanStep",
    "Plan",
    "PlannerResponse",
    "VerdictDecision",
    "PolicyTier",
    "Verdict",
    "ApprovalDecisionKind",
    "ApprovalDecision",
    "VerificationOutcome",
    "VerificationSource",
    "VerificationResult",
    "RecoveryKind",
    "RecoveryDecision",
    "RunStatus",
    "StepRecord",
    "TaskState",
]


class ScreenGeometry(BaseModel):
    """Geometry and display properties of a captured monitor."""

    model_config = ConfigDict(frozen=True)

    width_px: int = Field(..., gt=0)
    height_px: int = Field(..., gt=0)
    scale_factor: float = Field(..., gt=0.0)
    monitor_index: int = 0


class ImageRef(BaseModel):
    """Metadata references for captured screenshot images."""

    model_config = ConfigDict(frozen=True)

    path_original: str
    path_model: str
    model_width: int = Field(..., gt=0)
    model_height: int = Field(..., gt=0)
    phash: str


ScreenState = Literal[
    "normal",
    "black_frame",
    "secure_desktop_or_locked",
    "capture_failed",
]


class WindowInfo(BaseModel):
    """Metadata for a visible top-level window."""

    model_config = ConfigDict(frozen=True)

    handle: int
    title: str
    process_name: str
    pid: int
    bbox: Rect
    is_foreground: bool
    is_minimized: bool
    is_elevated: bool | None = None


class OcrSpan(BaseModel):
    """Detected OCR text span and bounding box."""

    model_config = ConfigDict(frozen=True)

    text: str
    bbox: Rect
    confidence: float = Field(..., ge=0.0, le=1.0)


class UiElement(BaseModel):
    """UI element extracted via accessibility / UIA."""

    model_config = ConfigDict(frozen=True)

    ref: str
    role: str
    name: str
    bbox: Rect
    states: list[str] = Field(default_factory=list)


class ErrorInfo(BaseModel):
    """Structured error details."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    """Outcome of an executed action."""

    model_config = ConfigDict(extra="allow")

    action_type: str
    success: bool
    started_at: datetime
    duration_ms: int = Field(..., ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


class Observation(BaseModel):
    """Complete observation payload passed to planner."""

    step_index: int = Field(..., ge=0)
    captured_at: datetime
    screen: ScreenGeometry
    image: ImageRef
    screen_state: ScreenState = "normal"
    foreground: WindowInfo | None = None
    windows: list[WindowInfo] = Field(default_factory=list)
    cursor: Point
    last_result: ActionResult | None = None
    ocr: list[OcrSpan] | None = None
    ui_elements: list[UiElement] | None = None


class Assessment(BaseModel):
    """Planner's assessment of current screen state vs expected outcome."""

    screen_summary: str = Field(..., description="1-3 sentences describing screen state")
    previous_action_outcome: Literal["success", "failure", "unknown", "not_applicable"]
    evidence: str = Field(..., description="Observable evidence on screen supporting the outcome")


PlanStepStatus = Literal["pending", "active", "done", "failed", "skipped"]


class PlanStep(BaseModel):
    """Single step in an explicit plan."""

    index: int = Field(..., ge=0)
    description: str
    status: PlanStepStatus = "pending"


class Plan(BaseModel):
    """Explicit multi-step execution plan."""

    steps: list[PlanStep] = Field(default_factory=list)
    current_index: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> "Plan":
        if self.steps and self.current_index >= len(self.steps):
            raise ValueError(
                f"current_index {self.current_index} is out of bounds for {len(self.steps)} steps"
            )
        return self


class PlannerResponse(BaseModel):
    """Model output envelope proposing an action and assessment."""

    assessment: Assessment
    plan: Plan | None = None
    action: Action
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., max_length=1000)


VerdictDecision = Literal["allow", "needs_confirmation", "blocked"]
PolicyTier = Literal["SAFE", "CONFIRM", "BLOCKED"]


class Verdict(BaseModel):
    """Outcome of deterministic SafetyValidator evaluation."""

    decision: VerdictDecision
    tier: PolicyTier
    category: str
    reasons: list[str] = Field(default_factory=list)
    human_summary: str
    grantable_for_run: bool = False


class RunPermissions(BaseModel):
    """Ephemeral per-run permissions and category grants."""

    granted_categories: set[str] = Field(default_factory=set)
    granted_roots: set[str] = Field(default_factory=set)
    granted_hosts: set[str] = Field(default_factory=set)


ApprovalDecisionKind = Literal["approved", "denied", "approved_for_run"]


class ApprovalDecision(BaseModel):
    """Decision returned by an ApprovalGate."""

    decision: ApprovalDecisionKind
    note: str | None = None


VerificationOutcome = Literal["success", "failure", "unknown_progress", "not_applicable"]
VerificationSource = Literal["deterministic", "screen_signal", "assessment"]


class VerificationResult(BaseModel):
    """Outcome of action verification."""

    outcome: VerificationOutcome
    source: list[VerificationSource] = Field(default_factory=list)
    evidence: str


RecoveryKind = Literal["continue", "retry_hint", "replan", "ask_user", "abort"]


class RecoveryDecision(BaseModel):
    """Decision from RecoveryPolicy on how to proceed."""

    kind: RecoveryKind
    hint: str | None = None


RunStatus = Literal[
    "STARTING",
    "RUNNING",
    "WAITING_USER",
    "COMPLETED",
    "FAILED_BUDGET",
    "FAILED_PROVIDER",
    "STOPPED_BY_USER",
    "ABORTED_BY_AGENT",
]


class StepRecord(BaseModel):
    """Persistent historical record of a single step."""

    step_index: int
    observation_ref: str
    planner_response: PlannerResponse
    verdict: Verdict
    approval: ApprovalDecision | None = None
    result: ActionResult
    verification: VerificationResult | None = None


class TaskState(BaseModel):
    """Complete mutable task state owned by AgentRunner."""

    run_id: str
    goal: str
    autonomy_mode: str
    status: RunStatus = "STARTING"
    current_step: int = 0
    steps: list[StepRecord] = Field(default_factory=list)
    feedback_queue: list[str] = Field(default_factory=list)
    plan: Plan | None = None
