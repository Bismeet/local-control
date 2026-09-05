"""Agent planner: prompt construction, model interaction, structured output parsing and retry."""

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from local_control.agent.history import HistoryCondenser
from local_control.core.errors import PlannerError
from local_control.core.types import Observation, PlannerResponse, TaskState
from local_control.models.provider import (
    ImagePart,
    Message,
    ModelProvider,
    ModelRequest,
    TextPart,
)

logger = structlog.get_logger(__name__)

DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_planner.md"


def strip_markdown_fences(text: str) -> str:
    """Extract raw JSON from markdown fenced code blocks if present."""
    trimmed = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", trimmed)
    if match:
        return match.group(1).strip()
    return trimmed


class Planner:
    """Orchestrates vision model prompts, validates typed actions, and handles parse retries."""

    def __init__(
        self,
        provider: ModelProvider,
        system_prompt_path: Path | None = None,
        history_condenser: HistoryCondenser | None = None,
    ) -> None:
        self.provider = provider
        prompt_file = system_prompt_path or DEFAULT_SYSTEM_PROMPT_PATH
        if prompt_file.exists():
            self.system_prompt = prompt_file.read_text(encoding="utf-8")
        else:
            self.system_prompt = "You are local-control. Propose typed actions as JSON."
        self.history_condenser = history_condenser or HistoryCondenser()

    def build_request(
        self,
        state: TaskState,
        obs: Observation,
        error_feedback: str | None = None,
        png_bytes: bytes | None = None,
        replan_reason: str | None = None,
    ) -> ModelRequest:
        """Construct the prompt messages, system instructions, and schema for the model."""
        prompt_lines: list[str] = [
            f"# Goal\n{state.goal}\n",
            f"# Autonomy Mode\n{state.autonomy_mode}\n",
        ]

        # 1. Replan notice if triggered
        if replan_reason:
            prompt_lines.append(f"# REPLAN REQUIRED: {replan_reason}")
            prompt_lines.append(
                "You MUST return an updated `plan` in your response with `revision` incremented by 1, "
                "and reflect required changes to the plan steps.\n"
            )

        # 2. Current Plan (if any)
        if state.plan:
            prompt_lines.append("# Current Plan")
            prompt_lines.append(
                f"- Revision: {state.plan.revision}, Active Step: {state.plan.current_index}"
            )
            for step in state.plan.steps:
                prompt_lines.append(f"  [{step.index}] ({step.status}) {step.description}")
            prompt_lines.append("")

        # 3. Condensed history
        history_lines = self.history_condenser.condense(state.steps)
        if history_lines:
            prompt_lines.extend(history_lines)
            prompt_lines.append("")

        # 4. Feedback queue items
        if state.feedback_queue or error_feedback:
            prompt_lines.append("# Feedback & Runtime Notices")
            for fb in state.feedback_queue:
                prompt_lines.append(f"- NOTICE: {fb}")
            if error_feedback:
                prompt_lines.append(f"- RETRY ERROR: {error_feedback}")
            prompt_lines.append("")

        # 3. Current observation summary
        prompt_lines.append("# Current Desktop State")
        prompt_lines.append(
            f"- Screen: {obs.screen.width_px}x{obs.screen.height_px} (Scale: {obs.screen.scale_factor})"
        )
        prompt_lines.append(f"- Model Image Size: {obs.image.model_width}x{obs.image.model_height}")
        prompt_lines.append(f"- Cursor Position: ({obs.cursor.x}, {obs.cursor.y})")
        prompt_lines.append(f"- Screen State: {obs.screen_state}")

        if obs.foreground:
            prompt_lines.append(
                f"- Foreground Window: '{obs.foreground.title}' (PID {obs.foreground.pid}, handle {obs.foreground.handle})"
            )

        if obs.windows:
            prompt_lines.append(f"- Visible Windows ({len(obs.windows)} total):")
            for w in obs.windows[:10]:
                prompt_lines.append(
                    f"  * '{w.title}' (process: {w.process_name}, handle: {w.handle})"
                )

        if obs.last_result:
            lr = obs.last_result
            status = (
                "SUCCESS" if lr.success else f"FAILED ({lr.error.code if lr.error else 'error'})"
            )
            prompt_lines.append(f"- Last Action Result: {lr.action_type} -> {status}")

        if obs.browser:
            prompt_lines.append("# Browser State")
            prompt_lines.append(f"- URL: {obs.browser.url}")
            prompt_lines.append(f"- Title: '{obs.browser.title}'")
            if obs.browser.tabs:
                prompt_lines.append(f"- Tabs ({len(obs.browser.tabs)} total):")
                for tab in obs.browser.tabs:
                    act_mark = " (ACTIVE)" if tab.active else ""
                    prompt_lines.append(f"  * [{tab.index}] '{tab.title}' <{tab.url}>{act_mark}")
            if obs.browser.snapshot:
                prompt_lines.append(f"- Accessibility Snapshot:\n{obs.browser.snapshot}")

        prompt_lines.append(
            "\nPropose the next typed action in valid JSON conforming to PlannerResponse."
        )

        text_content = "\n".join(prompt_lines)
        user_parts: list[TextPart | ImagePart] = [TextPart(text=text_content)]

        # Attach image part if bytes provided or image path exists
        img_data = png_bytes
        if not img_data and obs.image.path_model and Path(obs.image.path_model).exists():
            try:
                img_data = Path(obs.image.path_model).read_bytes()
            except Exception:
                img_data = None

        if img_data:
            user_parts.append(ImagePart(png_bytes=img_data))

        return ModelRequest(
            system=self.system_prompt,
            messages=[Message(role="user", parts=user_parts)],
            response_schema=PlannerResponse.model_json_schema(),
            temperature=0.2,
            max_tokens=1500,
        )

    def parse_response(self, text: str, parsed: dict[str, Any] | None) -> PlannerResponse:
        """Parse raw response text or parsed dictionary into validated PlannerResponse."""
        data = parsed
        if data is None:
            clean = strip_markdown_fences(text)
            data = json.loads(clean)

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data)}")

        return PlannerResponse.model_validate(data)

    async def propose(
        self,
        state: TaskState,
        obs: Observation,
        png_bytes: bytes | None = None,
        replan_reason: str | None = None,
    ) -> PlannerResponse:
        """Call model and return validated PlannerResponse with up to 2 retries."""
        max_attempts = 3
        error_feedback: str | None = None

        for attempt in range(1, max_attempts + 1):
            req = self.build_request(
                state=state,
                obs=obs,
                error_feedback=error_feedback,
                png_bytes=png_bytes,
                replan_reason=replan_reason,
            )

            response = await self.provider.complete(req)

            try:
                plan_resp = self.parse_response(response.text, response.parsed)

                # If replan was requested and an existing plan exists, ensure revision incremented
                if replan_reason is not None and state.plan is not None:
                    if plan_resp.plan is None:
                        raise ValueError("Replan requested but no `plan` was provided in response.")
                    if plan_resp.plan.revision <= state.plan.revision:
                        raise ValueError(
                            f"Replan requested with revision > {state.plan.revision}, "
                            f"but response had revision {plan_resp.plan.revision}."
                        )

                logger.info(
                    "planner.proposal_parsed",
                    action_type=plan_resp.action.type,
                    confidence=plan_resp.confidence,
                    attempt=attempt,
                )
                return plan_resp
            except (json.JSONDecodeError, ValidationError, ValueError) as err:
                logger.warning(
                    "planner.parse_failed",
                    attempt=attempt,
                    error=str(err),
                )
                error_feedback = (
                    f"Your previous response was invalid ({err}). "
                    "You must output ONLY a valid JSON object matching the PlannerResponse schema."
                )

        raise PlannerError(
            f"Planner failed to produce valid PlannerResponse after {max_attempts} attempts: {error_feedback}"
        )
