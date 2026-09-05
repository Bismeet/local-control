"""Unit tests for Settings loading, validation, and secret masking."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from local_control.config.settings import SafetySettings, Settings


@pytest.mark.unit
def test_default_settings_load() -> None:
    """Verify that default settings load cleanly with expected values."""
    settings = Settings.load()
    assert settings.safety.autonomy_mode == "assisted"
    assert settings.observation.model_max_width == 1280
    assert settings.budget.max_steps == 30
    assert settings.budget.max_cost_usd == 2.0
    assert settings.control_center.port == 8000


@pytest.mark.unit
def test_secret_masking() -> None:
    """Verify sensitive fields are masked when calling masked_dict()."""
    settings = Settings()
    # Mock a sensitive key
    settings.models.planner.extra["secret_token"] = "super-secret-value-12345"
    masked = settings.masked_dict()

    assert masked["models"]["planner"]["extra"]["secret_token"] == "***MASKED***"
    # Ensure api_key_env itself is not masked since it's just the env var name
    assert masked["models"]["planner"]["api_key_env"] == "OPENAI_API_KEY"


@pytest.mark.unit
def test_safety_hard_cap_validation() -> None:
    """Verify max_destructive_per_run cannot exceed 500."""
    with pytest.raises(ValidationError):
        SafetySettings(max_destructive_per_run=501)

    valid = SafetySettings(max_destructive_per_run=500)
    assert valid.max_destructive_per_run == 500


@pytest.mark.unit
def test_invalid_autonomy_mode_rejected() -> None:
    """Verify unknown autonomy mode is rejected."""
    with pytest.raises(ValidationError):
        SafetySettings(autonomy_mode="unrestricted")  # type: ignore[arg-type]


@pytest.mark.unit
def test_file_and_env_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify precedence: default < file < env < cli."""
    # 1. Custom config file
    config_file = tmp_path / "custom_config.toml"
    config_file.write_text(
        """
[safety]
autonomy_mode = "step"
max_destructive_per_run = 20

[budget]
max_steps = 15
""",
        encoding="utf-8",
    )

    # Load from file
    settings = Settings.load(config_path=config_file)
    assert settings.safety.autonomy_mode == "step"
    assert settings.safety.max_destructive_per_run == 20
    assert settings.budget.max_steps == 15

    # 2. Env variable override
    monkeypatch.setenv("LOCAL_CONTROL__BUDGET__MAX_STEPS", "50")
    monkeypatch.setenv("LOCAL_CONTROL__SAFETY__AUTONOMY_MODE", "trusted")
    settings_with_env = Settings.load(config_path=config_file)
    assert settings_with_env.safety.autonomy_mode == "trusted"
    assert settings_with_env.budget.max_steps == 50
    assert settings_with_env.safety.max_destructive_per_run == 20

    # 3. CLI override
    cli_overrides = {"safety": {"max_destructive_per_run": 10}}
    settings_with_cli = Settings.load(config_path=config_file, cli_overrides=cli_overrides)
    assert settings_with_cli.safety.max_destructive_per_run == 10
