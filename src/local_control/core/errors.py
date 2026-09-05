"""Exception hierarchy for local-control."""

from typing import Any


class LocalControlError(Exception):
    """Base exception for all local-control errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(LocalControlError):
    """Raised when configuration is invalid or missing."""


class SafetyError(LocalControlError):
    """Base exception for safety and permission violations."""


class BlockedActionError(SafetyError):
    """Raised when an action violates a hard-blocked safety rule."""

    def __init__(self, message: str, rule_id: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details)
        self.rule_id = rule_id


class ExecutionError(LocalControlError):
    """Base exception for tool execution errors."""


class ActionTimeoutError(ExecutionError):
    """Raised when an action execution exceeds its allowed timeout."""


class ToolNotFoundError(ExecutionError):
    """Raised when no tool is registered to handle a specific action type."""


class PlannerError(LocalControlError):
    """Base exception for planner failures."""


class PlannerParsingError(PlannerError):
    """Raised when model response cannot be parsed into a valid PlannerResponse."""


class CoordinateMappingError(LocalControlError):
    """Raised when coordinate mapping or validation fails."""


class RunStoreError(LocalControlError):
    """Raised when reading or writing run artifacts fails."""


class AuditError(LocalControlError):
    """Raised when an audit record cannot be written synchronously."""


class ProviderError(LocalControlError):
    """Raised when an LLM provider request fails."""
