"""Run directory persistence and replay loader for local-control."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_control.core.errors import RunStoreError
from local_control.core.events import Event
from local_control.core.types import TaskState


class RunStore:
    """Manages the persistence lifecycle and directory structure for runs."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
        elif "LOCAL_CONTROL__LOGGING__RUNS_DIR" in os.environ:
            self.base_dir = Path(os.environ["LOCAL_CONTROL__LOGGING__RUNS_DIR"]).resolve()
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                self.base_dir = Path(local_appdata) / "local-control" / "runs"
            else:
                self.base_dir = Path.home() / ".local-control" / "runs"

    def get_run_dir(self, run_id: str) -> Path:
        """Return the directory path for a specific run ID."""
        return self.base_dir / run_id

    def create_run(
        self,
        run_id: str,
        goal: str,
        mode: str,
        settings_snapshot: dict[str, Any] | None = None,
    ) -> Path:
        """Create the directory layout and initial run.json for a new run."""
        run_dir = self.get_run_dir(run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "screenshots").mkdir(exist_ok=True)
            (run_dir / "steps").mkdir(exist_ok=True)

            run_meta = {
                "run_id": run_id,
                "goal": goal,
                "mode": mode,
                "status": "STARTING",
                "created_at": datetime.now(UTC).isoformat(),
                "settings": settings_snapshot or {},
            }
            run_json_path = run_dir / "run.json"
            with open(run_json_path, "w", encoding="utf-8") as f:
                json.dump(run_meta, f, indent=2)

            # Ensure event and audit log files exist
            (run_dir / "events.jsonl").touch(exist_ok=True)
            (run_dir / "audit.jsonl").touch(exist_ok=True)
            return run_dir
        except OSError as e:
            raise RunStoreError(f"Failed to initialize run directory for {run_id}: {e}") from e

    def append_event(self, run_id: str, event: Event) -> None:
        """Append an Event to events.jsonl."""
        run_dir = self.get_run_dir(run_id)
        events_file = run_dir / "events.jsonl"
        try:
            with open(events_file, "a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        except OSError as e:
            raise RunStoreError(f"Failed to append event to run {run_id}: {e}") from e

    def append_audit(self, run_id: str, entry: dict[str, Any]) -> None:
        """Append an audit record to audit.jsonl."""
        run_dir = self.get_run_dir(run_id)
        audit_file = run_dir / "audit.jsonl"
        try:
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as e:
            raise RunStoreError(f"Failed to append audit record to run {run_id}: {e}") from e

    def write_state(self, run_id: str, state: TaskState) -> None:
        """Atomically rewrite state.json for the run."""
        run_dir = self.get_run_dir(run_id)
        state_file = run_dir / "state.json"
        state_json = state.model_dump_json(indent=2)
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=run_dir,
                delete=False,
                encoding="utf-8",
                suffix=".tmp",
            ) as tmp:
                tmp.write(state_json)
                tmp_path = Path(tmp.name)
            tmp_path.replace(state_file)
        except OSError as e:
            raise RunStoreError(f"Failed to write state for run {run_id}: {e}") from e

    def write_summary(self, run_id: str, content: str) -> None:
        """Write summary.md for the run."""
        run_dir = self.get_run_dir(run_id)
        summary_file = run_dir / "summary.md"
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            raise RunStoreError(f"Failed to write summary for run {run_id}: {e}") from e

    def load_run(self, run_id: str) -> tuple[dict[str, Any], TaskState | None, list[Event]]:
        """Load run metadata, task state, and all events for a run."""
        run_dir = self.get_run_dir(run_id)
        if not run_dir.exists():
            raise RunStoreError(f"Run directory for {run_id} does not exist.")

        run_json_path = run_dir / "run.json"
        if not run_json_path.exists():
            raise RunStoreError(f"run.json missing for run {run_id}.")

        try:
            with open(run_json_path, encoding="utf-8") as f:
                run_meta = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RunStoreError(f"Failed to read run.json for {run_id}: {e}") from e

        task_state: TaskState | None = None
        state_path = run_dir / "state.json"
        if state_path.exists():
            try:
                with open(state_path, encoding="utf-8") as f:
                    task_state = TaskState.model_validate_json(f.read())
            except Exception as e:
                raise RunStoreError(f"Failed to parse state.json for {run_id}: {e}") from e

        events: list[Event] = []
        events_path = run_dir / "events.jsonl"
        if events_path.exists():
            try:
                with open(events_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            events.append(Event.model_validate_json(line))
            except Exception as e:
                raise RunStoreError(f"Failed to parse events.jsonl for {run_id}: {e}") from e

        return run_meta, task_state, events

    def list_runs(self) -> list[dict[str, Any]]:
        """List summaries of all runs in base_dir, sorted by creation date descending."""
        if not self.base_dir.exists():
            return []
        runs: list[dict[str, Any]] = []
        for p in self.base_dir.iterdir():
            if p.is_dir() and (p / "run.json").exists():
                try:
                    with open(p / "run.json", encoding="utf-8") as f:
                        meta = json.load(f)
                    runs.append(meta)
                except Exception:
                    continue
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs
