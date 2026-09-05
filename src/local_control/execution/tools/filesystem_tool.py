"""Filesystem tool implementation for reliable, safety-gated file operations."""

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import send2trash
import structlog

from local_control.core.actions import (
    Action,
    FsCopyAction,
    FsDeleteAction,
    FsListAction,
    FsMkdirAction,
    FsMoveAction,
    FsReadAction,
    FsStatAction,
    FsWriteAction,
)
from local_control.core.types import ActionResult, ErrorInfo, Observation
from local_control.execution.tools.base import ExecutionContext, Tool

logger = structlog.get_logger(__name__)

MAX_READ_SIZE = 5 * 1024 * 1024  # 5 MB cap
MAX_LIST_ENTRIES = 1000

ToolResultTuple = tuple[bool, str | None, dict[str, Any] | None, ErrorInfo | None]


def normalize_path(path_str: str, base_dir: Path | None = None) -> Path:
    """Normalize and resolve path, expanding user variables and handling relative paths."""
    expanded = os.path.expandvars(os.path.expanduser(path_str))
    p = Path(expanded)
    if not p.is_absolute() and base_dir:
        p = base_dir / p
    return p.resolve()


def is_binary_file(path: Path) -> bool:
    """Detect if file contains null bytes in initial chunk."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" in chunk
    except Exception:
        return False


class FilesystemTool(Tool):
    """Tool adapter for filesystem operations (list, read, stat, mkdir, write, copy, move, delete)."""

    @property
    def handles(self) -> frozenset[str]:
        return frozenset(
            {
                "fs_list",
                "fs_read",
                "fs_stat",
                "fs_mkdir",
                "fs_write",
                "fs_copy",
                "fs_move",
                "fs_delete",
            }
        )

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        try:
            success: bool
            output: str | None
            data: dict[str, Any] | None
            error: ErrorInfo | None

            if isinstance(action, FsListAction):
                success, output, data, error = await self._fs_list(action, ctx)
            elif isinstance(action, FsReadAction):
                success, output, data, error = await self._fs_read(action, ctx)
            elif isinstance(action, FsStatAction):
                success, output, data, error = await self._fs_stat(action, ctx)
            elif isinstance(action, FsMkdirAction):
                success, output, data, error = await self._fs_mkdir(action, ctx)
            elif isinstance(action, FsWriteAction):
                success, output, data, error = await self._fs_write(action, ctx)
            elif isinstance(action, FsCopyAction):
                success, output, data, error = await self._fs_copy(action, ctx)
            elif isinstance(action, FsMoveAction):
                success, output, data, error = await self._fs_move(action, ctx)
            elif isinstance(action, FsDeleteAction):
                success, output, data, error = await self._fs_delete(action, ctx)
            else:
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=0,
                    error=ErrorInfo(
                        code="unsupported_action",
                        message=f"FilesystemTool cannot handle '{action.type}'",
                    ),
                )

            duration_ms = int((time.monotonic() - start_mono) * 1000)
            return ActionResult(
                action_type=action.type,
                success=success,
                started_at=started_at,
                duration_ms=duration_ms,
                output=output,
                data=data or {},
                error=error,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            logger.error("filesystem_tool.error", action_type=action.type, error=str(e))
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="filesystem_error", message=str(e)),
            )

    async def _fs_list(self, action: FsListAction, ctx: ExecutionContext) -> ToolResultTuple:
        p = normalize_path(action.path, ctx.workdir)
        if not p.exists():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_found", message=f"Path '{action.path}' does not exist"),
            )
        if not p.is_dir():
            return (
                False,
                None,
                None,
                ErrorInfo(
                    code="not_a_directory", message=f"Path '{action.path}' is not a directory"
                ),
            )

        max_entries = min(action.max_entries or 500, MAX_LIST_ENTRIES)
        entries: list[dict[str, Any]] = []
        truncated = False
        iterator = p.rglob("*") if action.recursive else p.iterdir()

        count = 0
        for item in sorted(iterator, key=lambda x: str(x)):
            count += 1
            if len(entries) < max_entries:
                try:
                    st = item.stat()
                    entries.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "is_dir": item.is_dir(),
                            "size": st.st_size if item.is_file() else None,
                            "modified": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
                        }
                    )
                except Exception:
                    entries.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "is_dir": item.is_dir(),
                            "size": None,
                            "modified": None,
                        }
                    )
            else:
                truncated = True

        lines = [f"Directory listing for '{p}' ({len(entries)} of {count} entries shown):"]
        for e in entries:
            kind = "DIR " if e["is_dir"] else "FILE"
            size_str = f" {e['size']} B" if e["size"] is not None else ""
            lines.append(f"  [{kind}] {e['name']}{size_str}")
        if truncated:
            lines.append(f"  ... [truncated: {count - len(entries)} more entries]")

        output_text = "\n".join(lines)
        return (
            True,
            output_text,
            {"entries": entries, "truncated": truncated, "total_count": count},
            None,
        )

    async def _fs_read(self, action: FsReadAction, ctx: ExecutionContext) -> ToolResultTuple:
        p = normalize_path(action.path, ctx.workdir)
        if not p.exists():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_found", message=f"File '{action.path}' does not exist"),
            )
        if not p.is_file():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_a_file", message=f"Path '{action.path}' is not a file"),
            )

        if is_binary_file(p):
            return (
                False,
                None,
                None,
                ErrorInfo(
                    code="binary_file",
                    message=f"File '{action.path}' contains binary data and cannot be read as text",
                ),
            )

        file_size = p.stat().st_size
        if file_size > MAX_READ_SIZE:
            return (
                False,
                None,
                None,
                ErrorInfo(
                    code="file_too_large",
                    message=f"File '{action.path}' size ({file_size} bytes) exceeds maximum allowable limit of {MAX_READ_SIZE} bytes",
                ),
            )

        max_bytes = action.max_bytes or 65536
        encoding = action.encoding or "utf-8"

        with open(p, "rb") as f:
            raw = f.read(max_bytes)

        text = raw.decode(encoding, errors="replace")
        truncated = file_size > max_bytes

        return (
            True,
            text,
            {
                "path": str(p),
                "bytes_read": len(raw),
                "file_size": file_size,
                "truncated": truncated,
            },
            None,
        )

    async def _fs_stat(self, action: FsStatAction, ctx: ExecutionContext) -> ToolResultTuple:
        p = normalize_path(action.path, ctx.workdir)
        if not p.exists():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_found", message=f"Path '{action.path}' does not exist"),
            )

        st = p.stat()
        info = {
            "path": str(p),
            "name": p.name,
            "exists": True,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "size": st.st_size if p.is_file() else None,
            "created": datetime.fromtimestamp(st.st_ctime, tz=UTC).isoformat(),
            "modified": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            "suffix": p.suffix,
        }
        output = (
            f"Path: {p}\n"
            f"Type: {'Directory' if p.is_dir() else 'File'}\n"
            f"Size: {st.st_size} bytes\n"
            f"Modified: {info['modified']}"
        )
        return True, output, info, None

    async def _fs_mkdir(self, action: FsMkdirAction, ctx: ExecutionContext) -> ToolResultTuple:
        p = normalize_path(action.path, ctx.workdir)
        p.mkdir(parents=True, exist_ok=True)
        return True, f"Created directory: {p}", {"path": str(p)}, None

    async def _fs_write(self, action: FsWriteAction, ctx: ExecutionContext) -> ToolResultTuple:
        p = normalize_path(action.path, ctx.workdir)
        if p.exists() and not action.overwrite:
            return (
                False,
                None,
                None,
                ErrorInfo(
                    code="dest_exists",
                    message=f"Destination '{action.path}' already exists and overwrite is False",
                ),
            )

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(action.content, encoding="utf-8")
        return (
            True,
            f"Wrote {len(action.content)} characters ({p.stat().st_size} bytes) to {p}",
            {"path": str(p), "bytes_written": p.stat().st_size},
            None,
        )

    async def _fs_copy(self, action: FsCopyAction, ctx: ExecutionContext) -> ToolResultTuple:
        src = normalize_path(action.src, ctx.workdir)
        dst = normalize_path(action.dst, ctx.workdir)

        if not src.exists():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_found", message=f"Source path '{action.src}' does not exist"),
            )

        target = dst / src.name if dst.is_dir() else dst

        if target.exists() and not action.overwrite:
            return (
                False,
                None,
                None,
                ErrorInfo(
                    code="dest_exists",
                    message=f"Destination '{target}' already exists and overwrite is False",
                ),
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=action.overwrite)
        else:
            shutil.copy2(src, target)

        return True, f"Copied '{src}' to '{target}'", {"src": str(src), "dst": str(target)}, None

    async def _fs_move(self, action: FsMoveAction, ctx: ExecutionContext) -> ToolResultTuple:
        src = normalize_path(action.src, ctx.workdir)
        dst = normalize_path(action.dst, ctx.workdir)

        if not src.exists():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_found", message=f"Source path '{action.src}' does not exist"),
            )

        target = dst / src.name if dst.is_dir() else dst

        if target.exists() and target != src:
            if not action.overwrite:
                return (
                    False,
                    None,
                    None,
                    ErrorInfo(
                        code="dest_exists",
                        message=f"Destination '{target}' already exists and overwrite is False",
                    ),
                )
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))

        return True, f"Moved '{src}' to '{target}'", {"src": str(src), "dst": str(target)}, None

    async def _fs_delete(self, action: FsDeleteAction, ctx: ExecutionContext) -> ToolResultTuple:
        p = normalize_path(action.path, ctx.workdir)
        if not p.exists():
            return (
                False,
                None,
                None,
                ErrorInfo(code="not_found", message=f"Path '{action.path}' does not exist"),
            )

        send2trash.send2trash(str(p))
        return True, f"Sent '{p}' to Recycle Bin", {"path": str(p), "recycled": True}, None

    async def postcondition(
        self, action: Action, result: ActionResult, obs_after: Observation
    ) -> Any | None:
        if not result.success:
            return None

        act_type = action.type
        if act_type == "fs_mkdir":
            p = normalize_path(getattr(action, "path", ""))
            return p.is_dir()
        elif act_type == "fs_write":
            p = normalize_path(getattr(action, "path", ""))
            return p.is_file() and p.stat().st_size >= 0
        elif act_type == "fs_copy":
            dst = normalize_path(getattr(action, "dst", ""))
            src = normalize_path(getattr(action, "src", ""))
            target = dst / src.name if dst.is_dir() else dst
            return target.exists()
        elif act_type == "fs_move":
            src = normalize_path(getattr(action, "src", ""))
            dst = normalize_path(getattr(action, "dst", ""))
            target = dst / src.name if dst.is_dir() else dst
            return target.exists() and not src.exists()
        elif act_type == "fs_delete":
            p = normalize_path(getattr(action, "path", ""))
            return not p.exists()

        return True
