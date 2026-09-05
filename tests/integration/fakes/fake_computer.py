"""In-memory simulated computer environment for integration tests without real OS interactions."""

from datetime import UTC, datetime
from typing import Any

from local_control.core.actions import (
    Action,
    ClickAction,
    FocusWindowAction,
    MoveMouseAction,
    Point,
    Rect,
    TypeTextAction,
    WaitAction,
)
from local_control.core.types import (
    ActionResult,
    ImageRef,
    Observation,
    ScreenGeometry,
    WindowInfo,
)
from local_control.execution.executor import Executor
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.observation.observer import Observer


class FakeScreenCapture:
    """Produces synthetic PIL images."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.width = width
        self.height = height

    def capture(self, monitor_index: int = 0) -> Any:
        class DummyRaw:
            def __init__(self, w: int, h: int) -> None:
                self.width = w
                self.height = h
                self.raw_bytes = bytes(w * h * 4)
                self.monitor_index = 0

        return DummyRaw(self.width, self.height)


class FakeComputerTool(Tool):
    """Executes actions by updating the FakeComputer state."""

    def __init__(self, computer: "FakeComputer") -> None:
        self.computer = computer

    @property
    def handles(self) -> frozenset[str]:
        return frozenset(
            {
                "click",
                "move_mouse",
                "type_text",
                "focus_window",
                "wait",
                "done",
                "fail",
            }
        )

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        data: dict[str, Any] = {}

        if isinstance(action, ClickAction):
            self.computer.clicks.append((action.x, action.y))
            self.computer.cursor = Point(x=action.x, y=action.y)
            data = {"clicked": True, "x": action.x, "y": action.y}

        elif isinstance(action, MoveMouseAction):
            self.computer.cursor = Point(x=action.x, y=action.y)
            data = {"moved": True, "x": action.x, "y": action.y}

        elif isinstance(action, TypeTextAction):
            self.computer.typed_texts.append(action.text)
            data = {"typed": action.text}

        elif isinstance(action, FocusWindowAction):
            for win in self.computer.windows:
                is_fg = win.handle == action.handle
                # update foreground
                if is_fg:
                    self.computer.foreground = win
            data = {"focused_handle": action.handle}

        elif isinstance(action, WaitAction):
            data = {"waited": action.seconds}

        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=datetime.now(UTC),
            duration_ms=10,
            data=data,
        )


class FakeComputer:
    """In-memory simulation of desktop windows, cursor, and input actions."""

    def __init__(self) -> None:
        self.screen = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
        self.cursor = Point(x=100, y=100)
        self.windows = [
            WindowInfo(
                handle=1001,
                title="LC Test Target",
                process_name="python.exe",
                pid=5000,
                bbox=Rect(x=100, y=100, width=800, height=600),
                is_foreground=True,
                is_minimized=False,
            )
        ]
        self.foreground = self.windows[0]
        self.clicks: list[tuple[int, int]] = []
        self.typed_texts: list[str] = []

    def create_observer(self) -> Observer:
        """Create an Observer configured to observe this FakeComputer."""
        obs = Observer(screen_capture=FakeScreenCapture())

        # Override observe method to return observation based on fake state
        def fake_observe(
            last_result: ActionResult | None = None,
            step_index: int = 0,
            run_id: str | None = None,
        ) -> Observation:
            return Observation(
                step_index=step_index,
                captured_at=datetime.now(UTC),
                screen=self.screen,
                image=ImageRef(
                    path_original="",
                    path_model="",
                    model_width=960,
                    model_height=540,
                    phash="f" * 16,
                ),
                screen_state="normal",
                foreground=self.foreground,
                windows=self.windows,
                cursor=self.cursor,
                last_result=last_result,
            )

        obs.observe = fake_observe  # type: ignore[method-assign]
        return obs

    def create_executor(self) -> Executor:
        """Create an Executor wired to execute actions on this FakeComputer."""
        tool = FakeComputerTool(self)
        return Executor(tools=[tool])
