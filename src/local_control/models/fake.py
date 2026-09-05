"""Fake model provider for deterministic testing and simulated runs."""

import json
import time
from collections.abc import Callable
from typing import Any

from local_control.core.errors import ProviderError
from local_control.core.types import PlannerResponse
from local_control.models.provider import ModelProvider, ModelRequest, ModelResponse, Usage


class FakeModelProvider(ModelProvider):
    """In-memory scripted model provider recording all requests."""

    def __init__(
        self,
        scripted_responses: list[dict[str, Any] | PlannerResponse | str] | None = None,
        generator: Callable[[ModelRequest], dict[str, Any] | PlannerResponse | str] | None = None,
        name: str = "fake",
        model: str = "fake-vision-v1",
    ) -> None:
        self.name = name
        self.model = model
        self.supports_vision = True
        self.supports_json_schema = True
        self.requests: list[ModelRequest] = []
        self._scripted: list[dict[str, Any] | PlannerResponse | str] = list(
            scripted_responses or []
        )
        self._generator = generator

    async def complete(self, req: ModelRequest) -> ModelResponse:
        start_mono = time.monotonic()
        self.requests.append(req)

        item: dict[str, Any] | PlannerResponse | str
        if self._generator:
            item = self._generator(req)
        elif self._scripted:
            item = self._scripted.pop(0)
        else:
            raise ProviderError("FakeModelProvider ran out of scripted responses")

        parsed: dict[str, Any] | None = None
        text: str

        if isinstance(item, PlannerResponse):
            parsed = item.model_dump()
            text = json.dumps(parsed)
        elif isinstance(item, dict):
            parsed = item
            text = json.dumps(item)
        elif isinstance(item, str):
            text = item
            try:
                parsed = json.loads(item)
            except Exception:
                parsed = None
        else:
            raise ProviderError(f"Unsupported scripted item type: {type(item)}")

        latency_ms = int((time.monotonic() - start_mono) * 1000)
        return ModelResponse(
            text=text,
            parsed=parsed,
            usage=Usage(input_tokens=100, output_tokens=50, cost_usd=0.001),
            latency_ms=latency_ms,
            provider=self.name,
            model=self.model,
            raw_id=f"fake-{len(self.requests)}",
        )
