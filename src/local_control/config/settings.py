"""Configuration settings models, loading hierarchy, and validation for local-control."""

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from local_control.core.errors import ConfigurationError


class ModelRoleSettings(BaseModel):
    """Configuration for an individual model role (planner, verifier, summarizer)."""

    model_config = ConfigDict(extra="allow")

    provider: str = "openai_compat"
    model: str = "gpt-4o"
    base_url: str | None = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    api_key: str = ""
    supports_vision: bool = True
    supports_json_schema: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class ModelsSettings(BaseModel):
    """Model role configuration."""

    planner: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    verifier: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    summarizer: ModelRoleSettings = Field(default_factory=ModelRoleSettings)


class ObservationSettings(BaseModel):
    """Screen and visual observation settings."""

    model_max_width: int = Field(default=1280, ge=640)
    ocr_always: bool = False
    set_of_marks: bool = False
    max_windows: int = Field(default=15, ge=1)
    monitor_index: int = 0


class SafetySettings(BaseModel):
    """Safety policy and permission settings."""

    autonomy_mode: Literal["step", "assisted", "trusted"] = "assisted"
    allowed_roots: list[str] = Field(default_factory=list)
    max_destructive_per_run: int = Field(default=50, ge=1, le=500)
    confirm_new_hosts: bool = False
    confirm_browser_type: bool = False
    allowed_unc_hosts: list[str] = Field(default_factory=list)
    seen_hosts: set[str] = Field(default_factory=set)

    @field_validator("max_destructive_per_run")
    @classmethod
    def validate_max_destructive(cls, v: int) -> int:
        if v > 500:
            raise ValueError("max_destructive_per_run cannot exceed 500 (hard cap).")
        return v


class BudgetSettings(BaseModel):
    """Execution budgets for cost, steps, and duration."""

    max_steps: int = Field(default=30, ge=1)
    max_time_s: int = Field(default=600, ge=1)
    max_cost_usd: float = Field(default=2.0, ge=0.0)
    consecutive_failures_limit: int = Field(default=3, ge=1)


class ExecutionSettings(BaseModel):
    """Tool execution parameters and timeouts."""

    action_timeout_s: int = Field(default=30, ge=1)
    shell_timeout_s: int = Field(default=120, ge=1)
    input_backend: Literal["sendinput", "pyautogui"] = "sendinput"


class TerminalSettings(BaseModel):
    """Terminal / shell execution configuration."""

    shell: str = ""
    strip_env: list[str] = Field(
        default_factory=lambda: [
            "*_API_KEY",
            "*_TOKEN",
            "*_SECRET",
            "*PASSWORD*",
        ]
    )


class BrowserSettings(BaseModel):
    """Browser automation configuration."""

    headless: bool = False
    channel: str = ""
    profile_dir: str = ""
    download_dir: str = ""
    snapshot_max_nodes: int = 400


class ControlCenterSettings(BaseModel):
    """Control Center local web UI settings."""

    host: str = "127.0.0.1"
    port: int = 8000


class LoggingSettings(BaseModel):
    """Logging and run persistence settings."""

    level: str = "INFO"
    runs_dir: str = ""


class MemorySettings(BaseModel):
    """Long-term memory settings."""

    db_path: str = ""


class VerificationSettings(BaseModel):
    """Verification and recovery configuration."""

    phash_threshold: int = Field(default=6, ge=0)
    max_retries_per_step: int = Field(default=2, ge=1)
    stuck_threshold: int = Field(default=3, ge=1)


class Settings(BaseSettings):
    """Root configuration object loaded with precedence."""

    model_config = SettingsConfigDict(
        env_prefix="LOCAL_CONTROL__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    models: ModelsSettings = Field(default_factory=ModelsSettings)
    observation: ObservationSettings = Field(default_factory=ObservationSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    verify: VerificationSettings = Field(default_factory=VerificationSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    terminal: TerminalSettings = Field(default_factory=TerminalSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    control_center: ControlCenterSettings = Field(default_factory=ControlCenterSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)

    def masked_dict(self) -> dict[str, Any]:
        """Return a dictionary of effective settings with sensitive values masked."""
        data = self.model_dump(mode="json")

        # Mask api key values or sensitive tokens if present
        def _mask_sensitive(d: dict[str, Any]) -> None:
            for k, v in d.items():
                if isinstance(v, dict):
                    _mask_sensitive(v)
                elif (
                    isinstance(v, str)
                    and any(s in k.lower() for s in ["key", "token", "secret", "password"])
                    and not k.endswith("_env")
                ):
                    d[k] = "***MASKED***" if v else ""

        _mask_sensitive(data)
        return data

    @classmethod
    def load(
        cls,
        config_path: Path | str | None = None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> "Settings":
        """Load settings following precedence:

        defaults < default_config.toml < user config < env vars < cli overrides
        """
        data: dict[str, Any] = {}

        # 1. default_config.toml
        pkg_root = Path(__file__).resolve().parent.parent.parent.parent
        default_toml = pkg_root / "config" / "default_config.toml"
        if default_toml.exists():
            try:
                with open(default_toml, "rb") as f:
                    data = _deep_update(data, tomllib.load(f))
            except Exception as e:
                raise ConfigurationError(f"Failed to read default_config.toml: {e}") from e

        # 2. User config path or %LOCALAPPDATA%/local-control/config.toml
        user_config_path: Path | None = None
        if config_path:
            user_config_path = Path(config_path)
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                candidate = Path(local_appdata) / "local-control" / "config.toml"
                if candidate.exists():
                    user_config_path = candidate

        if user_config_path and user_config_path.exists():
            try:
                with open(user_config_path, "rb") as f:
                    data = _deep_update(data, tomllib.load(f))
            except Exception as e:
                raise ConfigurationError(
                    f"Failed to read user config at {user_config_path}: {e}"
                ) from e

        # 3. Environment overrides (overrides TOML files)
        env_overrides = _parse_env_overrides()
        data = _deep_update(data, env_overrides)

        # 4. Instantiate Settings with Pydantic validation & coercion
        settings = cls(**data)

        # 5. CLI overrides
        if cli_overrides:
            updated_dict = _deep_update(settings.model_dump(), cli_overrides)
            settings = cls(**updated_dict)

        return settings


def _parse_env_overrides() -> dict[str, Any]:
    """Parse environment variables matching LOCAL_CONTROL__<SECTION>__<KEY> into a nested dict."""
    import json

    env_data: dict[str, Any] = {}
    prefix = "LOCAL_CONTROL__"
    for env_key, env_val in os.environ.items():
        if env_key.startswith(prefix):
            parts = env_key[len(prefix) :].lower().split("__")
            curr = env_data
            for part in parts[:-1]:
                curr = curr.setdefault(part, {})
            try:
                parsed_val = json.loads(env_val)
            except Exception:
                parsed_val = env_val
            curr[parts[-1]] = parsed_val
    return env_data


def _deep_update(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively update a dictionary."""
    result = deepcopy(base)
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_update(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
