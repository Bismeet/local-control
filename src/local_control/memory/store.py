"""SQLite-backed persistent memory store for preferences, hints, workflows, and run index."""

import datetime
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

import structlog

from local_control.memory.models import Hint, Workflow

logger = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_MEMORY_DIR = Path.home() / ".local-control"
DEFAULT_DB_PATH = DEFAULT_MEMORY_DIR / "memory.db"


class MemoryStore:
    """Manages SQLite persistent memory with migrations, preferences, hints, and workflows."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is not None:
            self.db_path = Path(db_path)
        elif "LOCAL_CONTROL__MEMORY__DB_PATH" in os.environ:
            self.db_path = Path(os.environ["LOCAL_CONTROL__MEMORY__DB_PATH"])
        else:
            self.db_path = DEFAULT_DB_PATH

        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        """Run all pending SQL migrations in migrations directory."""
        with self._lock:
            cur = self._conn.cursor()
            # Ensure schema_migrations exists first
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

            cur.execute("SELECT version FROM schema_migrations")
            applied_versions = {row[0] for row in cur.fetchall()}

            if not MIGRATIONS_DIR.exists():
                return

            migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
            for mig_file in migration_files:
                match = re.match(r"^(\d+)_", mig_file.name)
                if not match:
                    continue
                version = int(match.group(1))
                if version not in applied_versions:
                    logger.info("Applying memory migration", version=version, file=mig_file.name)
                    sql = mig_file.read_text(encoding="utf-8")
                    cur.executescript(sql)
                    now_str = datetime.datetime.now(datetime.UTC).isoformat()
                    cur.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, now_str),
                    )
                    self._conn.commit()

    # --- Preferences ---

    def set_preference(self, key: str, value: str) -> None:
        """Set or update a user preference."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO preferences (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            self._conn.commit()

    def get_preference(self, key: str) -> str | None:
        """Retrieve preference value by key."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT value FROM preferences WHERE key = ?", (key,))
            row = cur.fetchone()
            return str(row["value"]) if row else None

    def list_preferences(self) -> dict[str, str]:
        """List all stored preferences."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT key, value FROM preferences ORDER BY key ASC")
            rows = cur.fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}

    def delete_preference(self, key: str) -> bool:
        """Delete a preference by key."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM preferences WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    # --- Hints ---

    def add_hint(
        self,
        app: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        source_run_id: str | None = None,
    ) -> int:
        """Add a semantic hint or shortcut."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO hints (app, key, value, confidence, source_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (app, key, value, confidence, source_run_id, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_hints(self, app: str | None = None, key: str | None = None) -> list[Hint]:
        """Fetch hints filtering optionally by app or key."""
        with self._lock:
            cur = self._conn.cursor()
            query = "SELECT id, app, key, value, confidence, source_run_id, created_at FROM hints WHERE 1=1"
            params: list[Any] = []
            if app:
                query += " AND (app = ? OR app = '*')"
                params.append(app)
            if key:
                query += " AND key = ?"
                params.append(key)
            query += " ORDER BY confidence DESC, id DESC"
            cur.execute(query, params)
            rows = cur.fetchall()
            return [
                Hint(
                    id=row["id"],
                    app=row["app"],
                    key=row["key"],
                    value=row["value"],
                    confidence=row["confidence"],
                    source_run_id=row["source_run_id"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def search_hints(self, query: str, app: str | None = None, limit: int = 10) -> list[Hint]:
        """Search hints matching query tokens and optionally an active app name."""
        tokens = [t.lower() for t in re.split(r"\W+", query) if len(t) >= 2]
        with self._lock:
            cur = self._conn.cursor()
            sql = "SELECT id, app, key, value, confidence, source_run_id, created_at FROM hints"
            cur.execute(sql)
            all_hints = [
                Hint(
                    id=r["id"],
                    app=r["app"],
                    key=r["key"],
                    value=r["value"],
                    confidence=r["confidence"],
                    source_run_id=r["source_run_id"],
                    created_at=r["created_at"],
                )
                for r in cur.fetchall()
            ]

        # Score hints by relevance
        scored: list[tuple[float, Hint]] = []
        app_clean = app.lower() if app else ""

        for h in all_hints:
            score = h.confidence
            h_app = h.app.lower()
            h_key = h.key.lower()
            h_val = h.value.lower()

            # App matching boost
            if app_clean and (h_app == app_clean or h_app in app_clean or app_clean in h_app):
                score += 3.0
            elif h_app == "*":
                score += 0.5
            elif app_clean and h_app != "*":
                score -= 1.0

            # Token matching
            matched_tokens = 0
            for tok in tokens:
                if tok in h_app:
                    score += 2.0
                    matched_tokens += 1
                if tok in h_key:
                    score += 1.5
                    matched_tokens += 1
                if tok in h_val:
                    score += 1.0
                    matched_tokens += 1

            if matched_tokens > 0 or (app_clean and h_app == app_clean):
                scored.append((score, h))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def delete_hint(self, hint_id: int) -> bool:
        """Delete a hint by ID."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM hints WHERE id = ?", (hint_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # --- Workflows ---

    def save_workflow(
        self,
        name: str,
        goal_template: str,
        steps_json: str,
        params_json: str,
        description: str = "",
    ) -> int:
        """Create or update a reusable workflow template."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO workflows (name, description, goal_template, steps_json, params_json, success_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    goal_template = excluded.goal_template,
                    steps_json = excluded.steps_json,
                    params_json = excluded.params_json,
                    updated_at = excluded.updated_at
                """,
                (name, description, goal_template, steps_json, params_json, now, now),
            )
            self._conn.commit()
            cur.execute("SELECT id FROM workflows WHERE name = ?", (name,))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_workflow(self, name: str) -> Workflow | None:
        """Retrieve a workflow by unique name."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, name, description, goal_template, steps_json, params_json, success_count, created_at, updated_at
                FROM workflows WHERE name = ?
                """,
                (name,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return Workflow(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                goal_template=row["goal_template"],
                steps_json=row["steps_json"],
                params_json=row["params_json"],
                success_count=row["success_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_workflows(self) -> list[Workflow]:
        """List all stored workflows."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, name, description, goal_template, steps_json, params_json, success_count, created_at, updated_at
                FROM workflows ORDER BY name ASC
                """
            )
            rows = cur.fetchall()
            return [
                Workflow(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"],
                    goal_template=row["goal_template"],
                    steps_json=row["steps_json"],
                    params_json=row["params_json"],
                    success_count=row["success_count"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def delete_workflow(self, name: str) -> bool:
        """Delete a workflow by name."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM workflows WHERE name = ?", (name,))
            self._conn.commit()
            return cur.rowcount > 0

    def increment_workflow_success(self, name: str) -> None:
        """Increment success count for a workflow."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE workflows SET success_count = success_count + 1, updated_at = ? WHERE name = ?",
                (now, name),
            )
            self._conn.commit()

    def record_workflow_run(self, workflow_id: int, run_id: str, status: str) -> int:
        """Record an execution instance of a workflow."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO workflow_runs (workflow_id, run_id, status, executed_at)
                VALUES (?, ?, ?, ?)
                """,
                (workflow_id, run_id, status, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    # --- Runs Index ---

    def index_run(self, run_id: str, goal: str, status: str, step_count: int = 0) -> None:
        """Index or update run metadata in SQLite."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO runs_index (run_id, goal, status, step_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    step_count = excluded.step_count,
                    updated_at = excluded.updated_at
                """,
                (run_id, goal, status, step_count, now, now),
            )
            self._conn.commit()

    def get_indexed_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recently indexed runs."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT run_id, goal, status, step_count, created_at, updated_at
                FROM runs_index ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()
