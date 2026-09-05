"""Budget tracking, step limits, timeouts, and warning events."""

from dataclasses import dataclass
from datetime import UTC, datetime

from local_control.config.settings import Settings
from local_control.core.types import TaskState


@dataclass(frozen=True)
class BudgetStatus:
    """Status returned from budget checks."""

    ok: bool
    reason: str | None = None
    warning: bool = False
    warning_message: str | None = None


class Budget:
    """Monitors step count, elapsed duration, failure count, and monetary cost."""

    def __init__(
        self,
        settings: Settings | None = None,
        start_time: datetime | None = None,
    ) -> None:
        self.settings = settings or Settings.load()
        self.start_time = start_time or datetime.now(UTC)
        self._warned_steps = False
        self._warned_time = False

    def reset(self, start_time: datetime | None = None) -> None:
        """Reset budget tracking for a new run."""
        self.start_time = start_time or datetime.now(UTC)
        self._warned_steps = False
        self._warned_time = False

    def check(self, state: TaskState) -> BudgetStatus:
        b_cfg = self.settings.budget
        max_steps = b_cfg.max_steps
        max_time_s = b_cfg.max_time_s
        fail_limit = b_cfg.consecutive_failures_limit

        # 1. Step limit check
        if state.current_step >= max_steps:
            return BudgetStatus(
                ok=False,
                reason=f"Step budget exceeded: {state.current_step} >= {max_steps}",
            )

        # 2. Time limit check
        elapsed_s = (datetime.now(UTC) - self.start_time).total_seconds()
        if elapsed_s >= max_time_s:
            return BudgetStatus(
                ok=False,
                reason=f"Time budget exceeded: {elapsed_s:.1f}s >= {max_time_s}s",
            )

        # 3. Consecutive failures check
        consecutive_failures = 0
        for step in reversed(state.steps):
            if not step.result.success or (
                step.planner_response.assessment.previous_action_outcome == "failure"
            ):
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= fail_limit:
            return BudgetStatus(
                ok=False,
                reason=f"Consecutive failure limit reached: {consecutive_failures} >= {fail_limit}",
            )

        # 4. 80% Warning thresholds
        warning = False
        warning_msg: str | None = None

        if state.current_step >= int(max_steps * 0.8) and not self._warned_steps:
            warning = True
            warning_msg = (
                f"Step budget warning: step {state.current_step}/{max_steps} (80% reached)"
            )
            self._warned_steps = True
        elif elapsed_s >= (max_time_s * 0.8) and not self._warned_time:
            warning = True
            warning_msg = (
                f"Time budget warning: {elapsed_s:.1f}s/{max_time_s}s elapsed (80% reached)"
            )
            self._warned_time = True

        return BudgetStatus(
            ok=True,
            warning=warning,
            warning_message=warning_msg,
        )
