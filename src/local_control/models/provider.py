"""Model provider protocol, request and response envelopes, and token usage."""

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Usage(BaseModel):
    """Token consumption and estimated cost."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class TextPart(BaseModel):
    """Plain text segment in a multimodal message."""

    model_config = ConfigDict(frozen=True)

    text: str
    type: Literal["text"] = "text"


class ImagePart(BaseModel):
    """PNG image payload in a multimodal message."""

    model_config = ConfigDict(frozen=True)

    png_bytes: bytes
    detail: str = "auto"
    type: Literal["image"] = "image"


MessagePart = TextPart | ImagePart


class Message(BaseModel):
    """Message in a conversational completion exchange."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    parts: list[MessagePart] = Field(default_factory=list)


class ModelRequest(BaseModel):
    """Structured request payload passed to a ModelProvider."""

    system: str = ""
    messages: list[Message] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    temperature: float = 0.2
    max_tokens: int = 1500
    timeout_s: float = 60.0


class ModelResponse(BaseModel):
    """Normalized response payload returned by a ModelProvider."""

    text: str
    parsed: dict[str, Any] | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    provider: str
    model: str
    raw_id: str | None = None


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol for LLM and vision model access."""

    name: str
    model: str
    supports_vision: bool
    supports_json_schema: bool

    async def complete(self, req: ModelRequest) -> ModelResponse:
        """Send a completion request and return normalized response."""
        ...
