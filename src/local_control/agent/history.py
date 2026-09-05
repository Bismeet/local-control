"""History condensation and token budgeting for agent prompt formatting."""

from local_control.core.types import StepRecord


def estimate_tokens(text: str) -> int:
    """Rough heuristic estimating token count (~4 characters per token)."""
    return max(1, len(text) // 4)


class HistoryCondenser:
    """Condenses past step records into bounded text representations for prompts."""

    def __init__(self, full_steps_count: int = 6, max_history_tokens: int = 1500) -> None:
        self.full_steps_count = full_steps_count
        self.max_history_tokens = max_history_tokens

    def condense(self, steps: list[StepRecord]) -> list[str]:
        """Format past steps into a bounded list of lines for prompt injection."""
        if not steps:
            return []

        lines: list[str] = ["# Execution History"]

        split_idx = max(0, len(steps) - self.full_steps_count)
        older_steps = steps[:split_idx]
        recent_steps = steps[split_idx:]

        # 1. Condensed summary for older steps
        if older_steps:
            lines.append("## Earlier Steps (Summary)")
            for s in older_steps:
                outcome = "SUCCESS" if s.result.success else "FAILED"
                act_type = s.planner_response.action.type
                lines.append(f"- Step {s.step_index}: proposed `{act_type}` -> {outcome}")
            lines.append("")

        # 2. Detailed record for recent steps
        lines.append("## Recent Steps (Detailed)")
        for s in recent_steps:
            res_str = "SUCCESS" if s.result.success else "FAILURE"
            act = s.planner_response.action
            assessment = s.planner_response.assessment
            lines.append(f"### Step {s.step_index}")
            lines.append(f"- Action: `{act.type}` (target: {act.target_description})")
            lines.append(f"- Outcome: {res_str} ({s.result.duration_ms}ms)")
            if not s.result.success and s.result.error:
                lines.append(f"- Error: [{s.result.error.code}] {s.result.error.message}")
            lines.append(
                f"- Assessment: outcome={assessment.previous_action_outcome}, evidence='{assessment.evidence}'"
            )

        # Enforce max token limit by truncating earlier lines if necessary
        total_text = "\n".join(lines)
        if estimate_tokens(total_text) > self.max_history_tokens:
            # Drop older step lines to keep within budget
            filtered_lines: list[str] = ["# Execution History (Truncated)"]
            for s in recent_steps[-3:]:
                act = s.planner_response.action
                res_str = "SUCCESS" if s.result.success else "FAILURE"
                filtered_lines.append(f"- Step {s.step_index}: `{act.type}` -> {res_str}")
            return filtered_lines

        return lines
