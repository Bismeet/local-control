"""Model registry and factory for role-based provider resolution."""

import os

from local_control.config.settings import Settings
from local_control.core.errors import ConfigurationError
from local_control.models.fake import FakeModelProvider
from local_control.models.openai_compat import OpenAiCompatProvider
from local_control.models.provider import ModelProvider


def build(role: str, settings: Settings) -> ModelProvider:
    """Instantiate a ModelProvider for the requested agent role based on Settings."""
    role_lower = role.lower()
    if role_lower == "planner":
        cfg = settings.models.planner
    elif role_lower == "verifier":
        cfg = settings.models.verifier or settings.models.planner
    elif role_lower == "summarizer":
        cfg = settings.models.summarizer or settings.models.planner
    else:
        raise ConfigurationError(f"Unknown model role: '{role}'")

    provider_type = cfg.provider.lower()
    if provider_type == "fake":
        return FakeModelProvider(name="fake", model=cfg.model)

    if provider_type == "openai_compat":
        api_key = cfg.api_key or os.environ.get(cfg.api_key_env, "")
        base_url = cfg.base_url or "https://api.openai.com/v1"
        return OpenAiCompatProvider(
            model=cfg.model,
            base_url=base_url,
            api_key=api_key,
            supports_vision=cfg.supports_vision,
            supports_json_schema=cfg.supports_json_schema,
        )

    raise ConfigurationError(f"Unsupported model provider: '{cfg.provider}' for role '{role}'")
