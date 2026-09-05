"""Agent planner: prompt construction, model interaction, structured output parsing and retry."""

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

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
    ) -> None:
        self.provider = provider
        prompt_file = system_prompt_path or DEFAULT_SYSTEM_PROMPT_PATH
        if prompt_file.exists():
            self.system_prompt = prompt_file.read_text(encoding="utf-8")
        else:
            self.system_prompt = "You are local-control. Propose typed actions as JSON."

    def build_request(
        self,
        state: TaskState,
        obs: Observation,
        error_feedback: str | None = None,
        png_bytes: bytes | None = None,
    ) -> ModelRequest:
        """Construct the prompt messages, system instructions, and schema for the model."""
        prompt_lines: list[str] = [
            f"# Goal\n{state.goal}\n",
            f"# Autonomy Mode\n{state.autonomy_mode}\n",
        ]

        # 1. Condensed history
        if state.steps:
            prompt_lines.append("# Execution History (Recent Steps)")
            for step in state.steps[-6:]:
                res_summary = "SUCCESS" if step.result.success else "FAILURE"
                prompt_lines.append(
                    f"- Step {step.step_index}: proposed {step.planner_response.action.type} "
                    f"-> {res_summary} ({step.result.duration_ms}ms)"
                )
            prompt_lines.append("")

        # 2. Feedback queue items
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
            )

            response = await self.provider.complete(req)

            try:
                plan_resp = self.parse_response(response.text, response.parsed)
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
