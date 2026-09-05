"""OpenAI-compatible HTTP provider adapter using httpx."""

import asyncio
import base64
import json
import time
from typing import Any

import httpx
import structlog

from local_control.core.errors import ProviderError
from local_control.models.provider import (
    ImagePart,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    TextPart,
    Usage,
)

logger = structlog.get_logger(__name__)


class OpenAiCompatProvider(ModelProvider):
    """Provider adapter for OpenAI, Azure, OpenRouter, Ollama, vLLM, and compatible APIs."""

    def __init__(
        self,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        supports_vision: bool = True,
        supports_json_schema: bool = True,
        name: str = "openai_compat",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.supports_vision = supports_vision
        self.supports_json_schema = supports_json_schema
        self.name = name
        self._client = client

    def _build_payload(self, req: ModelRequest) -> dict[str, Any]:
        """Construct the chat/completions JSON payload."""
        messages: list[dict[str, Any]] = []

        if req.system:
            messages.append({"role": "system", "content": req.system})

        for msg in req.messages:
            if len(msg.parts) == 1 and isinstance(msg.parts[0], TextPart):
                messages.append({"role": msg.role, "content": msg.parts[0].text})
            else:
                content_items: list[dict[str, Any]] = []
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        content_items.append({"type": "text", "text": part.text})
                    elif isinstance(part, ImagePart) and self.supports_vision:
                        b64 = base64.b64encode(part.png_bytes).decode("ascii")
                        content_items.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                    "detail": part.detail,
                                },
                            }
                        )
                if len(content_items) == 1 and content_items[0].get("type") == "text":
                    messages.append({"role": msg.role, "content": content_items[0]["text"]})
                else:
                    messages.append({"role": msg.role, "content": content_items})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }

        if req.response_schema:
            if self.supports_json_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "PlannerResponse",
                        "schema": req.response_schema,
                        "strict": True,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}

        return payload

    async def complete(self, req: ModelRequest) -> ModelResponse:
        payload = self._build_payload(req)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        client = self._client or httpx.AsyncClient(timeout=req.timeout_s)
        should_close = self._client is None

        max_attempts = 3
        backoff_s = 0.5
        last_error: Exception | None = None
        start_mono = time.monotonic()

        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        logger.warning(
                            "openai_compat.retryable_status",
                            status=resp.status_code,
                            attempt=attempt,
                        )
                        await asyncio.sleep(backoff_s)
                        backoff_s *= 2
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                    latency_ms = int((time.monotonic() - start_mono) * 1000)
                    choices = data.get("choices", [])
                    if not choices:
                        raise ProviderError("OpenAI response returned no choices")

                    raw_text = choices[0].get("message", {}).get("content", "") or ""
                    parsed: dict[str, Any] | None = None
                    try:
                        parsed = json.loads(raw_text)
                    except Exception:
                        parsed = None

                    raw_usage = data.get("usage", {})
                    usage = Usage(
                        input_tokens=raw_usage.get("prompt_tokens", 0),
                        output_tokens=raw_usage.get("completion_tokens", 0),
                    )

                    return ModelResponse(
                        text=raw_text,
                        parsed=parsed,
                        usage=usage,
                        latency_ms=latency_ms,
                        provider=self.name,
                        model=self.model,
                        raw_id=data.get("id"),
                    )

                except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
                    last_error = e
                    if attempt < max_attempts:
                        await asyncio.sleep(backoff_s)
                        backoff_s *= 2
                    else:
                        break

            raise ProviderError(
                f"OpenAI provider request failed after {max_attempts} attempts: {last_error}"
            )

        finally:
            if should_close:
                await client.aclose()
