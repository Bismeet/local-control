"""Terminal tool implementation for safe, non-interactive PowerShell execution."""

import asyncio
import contextlib
import fnmatch
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from local_control.core.actions import Action, ShellRunAction
from local_control.core.types import ActionResult, ErrorInfo, Observation
from local_control.execution.tools.base import ExecutionContext, Tool
from local_control.execution.tools.filesystem_tool import normalize_path

logger = structlog.get_logger(__name__)

STRIP_ENV_PATTERNS = [
    "*_API_KEY",
    "*_TOKEN",
    "*_SECRET",
    "*PASSWORD*",
    "OPENAI_*",
    "ANTHROPIC_*",
    "AZURE_*",
    "AWS_*",
    "GITHUB_*",
    "GH_*",
    "LOCAL_CONTROL_*",
    "SLACK_*",
    "DISCORD_*",
]

MAX_INLINE_OUTPUT_BYTES = 32 * 1024  # 32 KB


def find_shell_executable() -> str:
    """Detect available shell executable (pwsh -> powershell -> bash -> sh)."""
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        if pwsh:
            return pwsh
        ps = shutil.which("powershell.exe") or shutil.which("powershell")
        if ps:
            return ps
        return "powershell.exe"
    else:
        pwsh = shutil.which("pwsh")
        if pwsh:
            return pwsh
        bash = shutil.which("bash")
        if bash:
            return bash
        return "/bin/sh"


def sanitize_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Create a sanitized copy of environment variables, stripping sensitive secrets."""
    source = base_env if base_env is not None else os.environ
    cleaned: dict[str, str] = {}

    for key, value in source.items():
        key_upper = key.upper()
        # Check against strip patterns
        should_strip = False
        for pat in STRIP_ENV_PATTERNS:
            if fnmatch.fnmatch(key_upper, pat.upper()):
                should_strip = True
                break

        if not should_strip:
            cleaned[key] = value

    return cleaned


def kill_process_tree(pid: int) -> None:
    """Kill process and all child processes recursively."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except Exception as e:
            logger.warning("terminal_tool.taskkill_failed", pid=pid, error=str(e))
    else:
        with contextlib.suppress(Exception):
            os.kill(pid, 9)


class TerminalTool(Tool):
    """Tool adapter for executing non-interactive shell commands."""

    def __init__(self, shell_path: str | None = None) -> None:
        self.shell_path = shell_path or find_shell_executable()

    @property
    def handles(self) -> frozenset[str]:
        return frozenset({"shell_run"})

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        if not isinstance(action, ShellRunAction):
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=0,
                error=ErrorInfo(
                    code="unsupported_action",
                    message=f"TerminalTool cannot handle '{action.type}'",
                ),
            )

        # Resolve working directory
        cwd: Path | None = None
        if action.cwd:
            cwd = normalize_path(action.cwd, ctx.workdir)
            if not cwd.exists() or not cwd.is_dir():
                duration_ms = int((time.monotonic() - start_mono) * 1000)
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    error=ErrorInfo(
                        code="invalid_cwd",
                        message=f"Specified working directory does not exist: {action.cwd}",
                    ),
                )
        elif ctx.workdir and ctx.workdir.is_dir():
            cwd = ctx.workdir
        else:
            cwd = Path.cwd()

        # Build command args
        shell = self.shell_path or find_shell_executable()
        is_pwsh_or_ps = "powershell" in shell.lower() or "pwsh" in shell.lower()

        if is_pwsh_or_ps:
            cmd_args = [
                shell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                action.command,
            ]
        else:
            cmd_args = [shell, "-c", action.command]

        env = sanitize_environment()
        timeout_s = max(1, min(action.timeout_s or 60, 300))

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout_s)
            )
        except TimeoutError:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            if proc and proc.pid:
                kill_process_tree(proc.pid)
                with contextlib.suppress(Exception):
                    proc.kill()

            logger.warning("terminal_tool.timeout", command=action.command, timeout_s=timeout_s)
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(
                    code="timeout",
                    message=f"Command timed out after {timeout_s}s and process tree was terminated",
                ),
                data={"command": action.command, "timeout_s": timeout_s},
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            logger.error("terminal_tool.execution_failed", error=str(e), command=action.command)
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(code="process_error", message=str(e)),
                data={"command": action.command},
            )

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        full_output = stdout_text
        if stderr_text:
            if full_output:
                full_output += "\n[stderr]\n" + stderr_text
            else:
                full_output = stderr_text

        # Large output persistence handling
        output_to_return = full_output
        output_file_path: str | None = None

        if len(full_output) > MAX_INLINE_OUTPUT_BYTES:
            try:
                log_dir = cwd
                if ctx.workdir and ctx.workdir.is_dir():
                    log_dir = ctx.workdir
                log_file = log_dir / f"shell_output_{int(time.time() * 1000)}.log"
                log_file.write_text(full_output, encoding="utf-8")
                output_file_path = str(log_file)

                # Keep head and tail
                half = MAX_INLINE_OUTPUT_BYTES // 2
                head = full_output[:half]
                tail = full_output[-half:]
                output_to_return = (
                    f"{head}\n\n"
                    f"[... Output truncated ({len(full_output)} bytes). Full output saved to {log_file} ...]\n\n"
                    f"{tail}"
                )
            except Exception as e:
                logger.warning("terminal_tool.failed_saving_output_file", error=str(e))

        exit_code = proc.returncode if proc.returncode is not None else -1
        success = exit_code == 0

        error_info: ErrorInfo | None = None
        if not success:
            error_info = ErrorInfo(
                code="nonzero_exit",
                message=f"Command exited with non-zero status code {exit_code}",
            )

        return ActionResult(
            action_type=action.type,
            success=success,
            started_at=started_at,
            duration_ms=duration_ms,
            output=output_to_return,
            error=error_info,
            data={
                "command": action.command,
                "exit_code": exit_code,
                "cwd": str(cwd),
                "output_file": output_file_path,
            },
        )

    async def postcondition(
        self, action: Action, result: ActionResult, obs_after: Observation
    ) -> Any | None:
        return result.success
