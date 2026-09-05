"""Sanitizer for memory and workflow recording.

Strips secrets, passwords, tokens, API keys, and parameterizes concrete user paths.
Guarantees memory stores no secrets or machine-specific absolute user paths.
"""

import os
import re
from typing import Any

# Secret patterns
SECRET_PATTERNS = [
    # Common API keys & tokens
    re.compile(
        r"(?:sk|ghp|gho|ghu|ghs|ghr|pat|xoxb|xoxp|xapp|secret_token|token|secret)[-_][A-Za-z0-9_\-]{16,}",
        re.IGNORECASE,
    ),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(
        r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", re.IGNORECASE
    ),
    # Common password assignments
    re.compile(
        r'(?i)(?:password|passwd|pwd|secret|api_key|token|auth)\s*[:=]\s*["\']?([^"\'\s,;]+)["\']?'
    ),
    re.compile(r"(?i)(?:-p|-password|--password)\s+([^\s]+)"),
]

# Sensitive keys in action dictionaries
SENSITIVE_PARAM_KEYS = {"password", "passwd", "secret", "token", "api_key", "credentials"}


class Sanitizer:
    """Sanitizes goals, actions, and text payloads for workflow persistence."""

    def __init__(self, user_home: str | None = None) -> None:
        self.user_home = user_home or os.path.expanduser("~")
        self.user_home_normalized = os.path.normpath(self.user_home).replace("\\", "/")

    def contains_secrets(self, text: str) -> bool:
        """Check if a string appears to contain secrets or API keys."""
        return any(pattern.search(text) is not None for pattern in SECRET_PATTERNS)

    def sanitize_secrets(self, text: str) -> str:
        """Redact secrets from text string."""
        result = text
        # Redact known token formats
        result = re.sub(
            r"(?:sk|ghp|gho|ghu|ghs|ghr|pat|xoxb|xoxp|xapp|secret_token|token|secret)[-_][A-Za-z0-9_\-]{16,}",
            "[REDACTED_KEY]",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
            "Bearer [REDACTED_TOKEN]",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
            "[REDACTED_JWT]",
            result,
        )

        # Redact password assignments
        def _redact_pw_assign(match: re.Match[str]) -> str:
            full = match.group(0)
            val = match.group(1)
            return full.replace(val, "[REDACTED]")

        result = re.sub(
            r'(?i)(?:password|passwd|pwd|secret|api_key|token|auth)\s*[:=]\s*["\']?([^"\'\s,;]+)["\']?',
            _redact_pw_assign,
            result,
        )
        result = re.sub(
            r"(?i)(?:-p|-password|--password)\s+([^\s]+)",
            lambda m: m.group(0).replace(m.group(1), "[REDACTED]"),
            result,
        )
        return result

    def parameterize_paths(
        self,
        text: str,
        params: dict[str, str],
    ) -> str:
        """Replace concrete user paths with template variables like {{downloads_dir}}."""
        result = text

        # Check for user Downloads directory
        downloads = os.path.join(self.user_home, "Downloads")
        for p in [downloads, downloads.replace("\\", "/")]:
            if p.lower() in result.lower():
                param_name = "downloads_dir"
                params[param_name] = p
                pattern = re.compile(re.escape(p), re.IGNORECASE)
                result = pattern.sub(f"{{{{{param_name}}}}}", result)

        # Check for user Documents directory
        documents = os.path.join(self.user_home, "Documents")
        for p in [documents, documents.replace("\\", "/")]:
            if p.lower() in result.lower():
                param_name = "documents_dir"
                params[param_name] = p
                pattern = re.compile(re.escape(p), re.IGNORECASE)
                result = pattern.sub(f"{{{{{param_name}}}}}", result)

        # Check for user home directory
        for p in [self.user_home, self.user_home_normalized]:
            if p.lower() in result.lower():
                param_name = "user_home"
                params[param_name] = p
                pattern = re.compile(re.escape(p), re.IGNORECASE)
                result = pattern.sub(f"{{{{{param_name}}}}}", result)

        # Check for general Windows drive absolute paths like C:\foo\bar
        win_paths = re.findall(r"[A-Za-z]:\\[^\"'\s\n\r<>|]+", result)
        for wp in win_paths:
            if "{{" in wp:
                continue
            leaf = os.path.basename(os.path.normpath(wp)).replace(".", "_").lower()
            param_name = f"{leaf}_path" if leaf else "file_path"
            if param_name in params and params[param_name] != wp:
                idx = 1
                while f"{param_name}_{idx}" in params and params[f"{param_name}_{idx}"] != wp:
                    idx += 1
                param_name = f"{param_name}_{idx}"
            params[param_name] = wp
            result = result.replace(wp, f"{{{{{param_name}}}}}")

        return result

    def sanitize_action(
        self,
        action: dict[str, Any],
        params: dict[str, str],
    ) -> dict[str, Any]:
        """Sanitize an action dict: remove secrets and parameterize paths."""
        sanitized: dict[str, Any] = {}
        for k, v in action.items():
            if k.lower() in SENSITIVE_PARAM_KEYS:
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, str):
                cleaned = self.sanitize_secrets(v)
                sanitized[k] = self.parameterize_paths(cleaned, params)
            elif isinstance(v, dict):
                sanitized[k] = self.sanitize_action(v, params)
            elif isinstance(v, list):
                sanitized[k] = [
                    self.sanitize_action(item, params)
                    if isinstance(item, dict)
                    else (
                        self.parameterize_paths(self.sanitize_secrets(item), params)
                        if isinstance(item, str)
                        else item
                    )
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized
