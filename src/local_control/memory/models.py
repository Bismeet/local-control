"""Data models for local-control memory layer."""

import json
import re
from typing import Any, cast

from pydantic import BaseModel, Field


class Preference(BaseModel):
    """User preference setting stored in memory."""

    key: str
    value: str
    updated_at: str


class Hint(BaseModel):
    """Learned semantic tip, shortcut, or app-specific hint."""

    id: int | None = None
    app: str
    key: str
    value: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_run_id: str | None = None
    created_at: str

    def format_prompt_line(self) -> str:
        """Format hint as a concise string for planner prompt."""
        if self.app and self.app != "*":
            return f"- [{self.app}] {self.key}: {self.value}"
        return f"- {self.key}: {self.value}"


class Workflow(BaseModel):
    """Parameterized workflow template recorded from successful runs."""

    id: int | None = None
    name: str
    description: str = ""
    goal_template: str
    steps_json: str
    params_json: str
    success_count: int = 1
    created_at: str
    updated_at: str

    def get_steps(self) -> list[dict[str, Any]]:
        """Parse raw steps json into list of action dicts."""
        try:
            val = json.loads(self.steps_json)
            return cast(list[dict[str, Any]], val) if isinstance(val, list) else []
        except Exception:
            return []

    def get_params(self) -> dict[str, Any]:
        """Parse raw params json into dict."""
        try:
            val = json.loads(self.params_json)
            return cast(dict[str, Any], val) if isinstance(val, dict) else {}
        except Exception:
            return {}

    def render(self, params: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
        """Render goal template and steps with supplied parameter values."""
        merged_params = dict(self.get_params())
        if params:
            merged_params.update(params)

        rendered_goal = self.goal_template
        for k, v in merged_params.items():
            pattern = re.compile(r"\{\{\s*" + re.escape(k) + r"\s*\}\}")
            val_str = str(v)

            def _repl_goal(_match: re.Match[str], s: str = val_str) -> str:
                return s

            rendered_goal = pattern.sub(_repl_goal, rendered_goal)

        steps = self.get_steps()
        rendered_steps: list[dict[str, Any]] = []

        def _substitute_obj(obj: Any) -> Any:
            if isinstance(obj, str):
                res = obj
                for k, v in merged_params.items():
                    p = re.compile(r"\{\{\s*" + re.escape(k) + r"\s*\}\}")
                    val_s = str(v)

                    def _repl_obj(_match: re.Match[str], s: str = val_s) -> str:
                        return s

                    res = p.sub(_repl_obj, res)
                return res
            elif isinstance(obj, dict):
                return {k: _substitute_obj(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_substitute_obj(item) for item in obj]
            return obj

        for step in steps:
            rendered_steps.append(_substitute_obj(step))

        return rendered_goal, rendered_steps
