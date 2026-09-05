"""Synchronous append-only audit logging for local-control."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_control.core.errors import AuditError


class AuditLogger:
    """Synchronous append-only audit logger.

    Writes to run-specific audit.jsonl and global audit/global.jsonl.
    Per SECURITY_MODEL section 8, audit writes are synchronous and any failure
    raises AuditError to abort the operation.
    """

    def __init__(
        self,
        run_id: str,
        run_dir: Path | str | None = None,
        global_dir: Path | str | None = None,
    ) -> None:
        self.run_id = run_id
        self.run_dir = Path(run_dir).resolve() if run_dir else None

        if global_dir:
            self.global_dir = Path(global_dir).resolve()
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                self.global_dir = Path(local_appdata) / "local-control" / "audit"
            else:
                self.global_dir = Path.home() / ".local-control" / "audit"

    def record(self, event_type: str, data: dict[str, Any]) -> None:
        """Synchronously write an audit record to both run and global audit logs."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "type": event_type,
            "data": data,
        }
        serialized = json.dumps(entry, default=str) + "\n"

        # 1. Write to run audit log
        if self.run_dir:
            run_audit_file = self.run_dir / "audit.jsonl"
            try:
                self.run_dir.mkdir(parents=True, exist_ok=True)
                with open(run_audit_file, "a", encoding="utf-8") as f:
                    f.write(serialized)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                raise AuditError(f"Failed writing to run audit log at {run_audit_file}: {e}") from e

        # 2. Write to global audit log
        global_audit_file = self.global_dir / "global.jsonl"
        try:
            self.global_dir.mkdir(parents=True, exist_ok=True)
            with open(global_audit_file, "a", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise AuditError(
                f"Failed writing to global audit log at {global_audit_file}: {e}"
            ) from e
