"""Integration tests for TerminalTool execution, environment sanitization, and timeout handling."""

import os
import sys
from pathlib import Path

import pytest

from local_control.core.actions import ShellRunAction
from local_control.execution.tools.base import ExecutionContext
from local_control.execution.tools.terminal_tool import TerminalTool
from local_control.safety.kill_switch import StopToken


@pytest.fixture
def tool() -> TerminalTool:
    return TerminalTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="test-terminal-run",
        stop=StopToken(),
        workdir=tmp_path,
    )


@pytest.mark.asyncio
async def test_terminal_tool_echo(tool: TerminalTool, ctx: ExecutionContext) -> None:
    is_windows = sys.platform == "win32"
    cmd = 'Write-Output "terminal_ok"' if is_windows else 'echo "terminal_ok"'

    action = ShellRunAction(
        command=cmd,
        target_description="test echo",
        expected_outcome="outputs terminal_ok",
    )
    result = await tool.execute(action, ctx)
    assert result.success is True
    assert "terminal_ok" in result.output
    assert result.data["exit_code"] == 0


@pytest.mark.asyncio
async def test_terminal_tool_env_stripping(tool: TerminalTool, ctx: ExecutionContext) -> None:
    # Seed sensitive environment variables
    os.environ["AGENT_API_KEY"] = "super-secret-key-12345"
    os.environ["AGENT_PASSWORD"] = "ultra-secret-pass"
    os.environ["SAFE_TEST_VAR"] = "public-value"

    try:
        is_windows = sys.platform == "win32"
        if is_windows:
            cmd = 'Write-Output "API_KEY=$env:AGENT_API_KEY;PASS=$env:AGENT_PASSWORD;SAFE=$env:SAFE_TEST_VAR"'
        else:
            cmd = 'echo "API_KEY=$AGENT_API_KEY;PASS=$AGENT_PASSWORD;SAFE=$SAFE_TEST_VAR"'

        action = ShellRunAction(
            command=cmd,
            target_description="inspect env",
            expected_outcome="secrets stripped",
        )
        result = await tool.execute(action, ctx)
        assert result.success is True
        # Secret values must NOT be present in output
        assert "super-secret-key-12345" not in result.output
        assert "ultra-secret-pass" not in result.output
        # Safe variable remains available
        assert "SAFE=public-value" in result.output
    finally:
        os.environ.pop("AGENT_API_KEY", None)
        os.environ.pop("AGENT_PASSWORD", None)
        os.environ.pop("SAFE_TEST_VAR", None)


@pytest.mark.asyncio
async def test_terminal_tool_timeout_and_tree_kill(
    tool: TerminalTool, ctx: ExecutionContext
) -> None:
    is_windows = sys.platform == "win32"
    cmd = "Start-Sleep -Seconds 10" if is_windows else "sleep 10"

    action = ShellRunAction(
        command=cmd,
        timeout_s=1,
        target_description="timeout test",
        expected_outcome="timeout and process killed",
    )
    result = await tool.execute(action, ctx)
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "timeout"
    assert "timed out" in result.error.message.lower()


@pytest.mark.asyncio
async def test_terminal_tool_nonzero_exit(tool: TerminalTool, ctx: ExecutionContext) -> None:
    is_windows = sys.platform == "win32"
    cmd = "exit 7" if is_windows else "exit 7"

    action = ShellRunAction(
        command=cmd,
        target_description="exit failure",
        expected_outcome="nonzero exit code",
    )
    result = await tool.execute(action, ctx)
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "nonzero_exit"
    assert result.data["exit_code"] == 7


@pytest.mark.asyncio
async def test_terminal_tool_large_output_persistence(
    tool: TerminalTool, ctx: ExecutionContext
) -> None:
    is_windows = sys.platform == "win32"
    if is_windows:
        cmd = '1..1200 | ForEach-Object { "Line $($_) - " + ("A" * 40) }'
    else:
        cmd = 'for i in $(seq 1 1200); do echo "Line $i: ' + ("A" * 40) + '"; done'

    action = ShellRunAction(
        command=cmd,
        target_description="large output test",
        expected_outcome="output truncated and written to file",
    )
    result = await tool.execute(action, ctx)
    assert result.success is True
    assert "Output truncated" in result.output
    assert result.data["output_file"] is not None

    log_path = Path(result.data["output_file"])
    assert log_path.is_file()
    assert log_path.stat().st_size > 32768
