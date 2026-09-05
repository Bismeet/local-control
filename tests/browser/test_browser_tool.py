"""Integration tests for BrowserTool with Playwright."""

from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from local_control.core.actions import (
    BrowserBackAction,
    BrowserClickAction,
    BrowserDownloadAction,
    BrowserNavigateAction,
    BrowserReadAction,
    BrowserSnapshotAction,
    BrowserTabsAction,
    BrowserTypeAction,
)
from local_control.execution.tools.base import ExecutionContext
from local_control.execution.tools.browser_tool import BrowserTool
from local_control.safety.kill_switch import StopToken


@pytest.fixture(scope="module")
def fixture_server():
    """Run a local HTTP server serving test fixtures."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: SimpleHTTPRequestHandler(
            *args, directory=str(fixtures_dir), **kwargs
        ),
    )
    host, port = server.server_address
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def exec_context(tmp_path):
    return ExecutionContext(
        run_id="test-browser-run",
        stop=StopToken(),
        workdir=tmp_path,
    )


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_navigate_and_read(fixture_server, tmp_path, exec_context):
    tool = BrowserTool(profile_dir=tmp_path / "browser_profile", headless=True)
    try:
        # 1. Navigate
        act_nav = BrowserNavigateAction(
            url=f"{fixture_server}/form.html",
            target_description="Go to form",
            expected_outcome="Form loaded",
        )
        res_nav = await tool.execute(act_nav, exec_context)
        assert res_nav.success is True
        assert "Customer Support" in (res_nav.output or "")

        # 2. Read
        act_read = BrowserReadAction(
            target_description="Read page",
            expected_outcome="Text extracted",
        )
        res_read = await tool.execute(act_read, exec_context)
        assert res_read.success is True
        assert "Customer Support" in (res_read.output or "")
        assert "Please fill in the form" in (res_read.output or "")
    finally:
        await tool.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_snapshot_type_and_click(fixture_server, tmp_path, exec_context):
    tool = BrowserTool(profile_dir=tmp_path / "browser_profile", headless=True)
    try:
        # Navigate
        act_nav = BrowserNavigateAction(
            url=f"{fixture_server}/form.html",
            target_description="Go to form",
            expected_outcome="Form loaded",
        )
        await tool.execute(act_nav, exec_context)

        # Snapshot
        act_snap = BrowserSnapshotAction(
            target_description="Capture snapshot",
            expected_outcome="Refs generated",
        )
        res_snap = await tool.execute(act_snap, exec_context)
        assert res_snap.success is True
        snapshot_text = res_snap.output or ""
        assert "[e" in snapshot_text
        assert "Customer Support" in snapshot_text

        # Find ref for name input
        name_ref = None
        submit_ref = None
        for ref_id, info in tool._refs.items():
            if info.get("tag") == "input" and info.get("name") == "name":
                name_ref = ref_id
            if info.get("role") == "button" and "submit" in info.get("text", "").lower():
                submit_ref = ref_id

        assert name_ref is not None, "Name input ref should be found"
        assert submit_ref is not None, "Submit button ref should be found"

        # Type into name input
        act_type = BrowserTypeAction(
            ref=name_ref,
            text="Alex Doe",
            target_description="Enter name",
            expected_outcome="Name filled",
        )
        res_type = await tool.execute(act_type, exec_context)
        assert res_type.success is True

        # Click submit
        act_click = BrowserClickAction(
            ref=submit_ref,
            target_description="Click submit",
            expected_outcome="Form submitted",
        )
        res_click = await tool.execute(act_click, exec_context)
        assert res_click.success is True
    finally:
        await tool.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_stale_ref_detection(fixture_server, tmp_path, exec_context):
    tool = BrowserTool(profile_dir=tmp_path / "browser_profile", headless=True)
    try:
        # Navigate to form.html
        act_nav = BrowserNavigateAction(
            url=f"{fixture_server}/form.html",
            target_description="Go to form",
            expected_outcome="Form loaded",
        )
        await tool.execute(act_nav, exec_context)

        # Snapshot on form.html
        act_snap = BrowserSnapshotAction(
            target_description="Capture snapshot",
            expected_outcome="Snapshot captured",
        )
        await tool.execute(act_snap, exec_context)
        assert "e1" in tool._refs

        # Navigate away to list.html
        act_nav2 = BrowserNavigateAction(
            url=f"{fixture_server}/list.html",
            target_description="Go to list",
            expected_outcome="List loaded",
        )
        await tool.execute(act_nav2, exec_context)

        # Attempt to use old ref from form.html -> must fail with browser_stale_ref
        act_stale_click = BrowserClickAction(
            ref="e1",
            target_description="Stale click",
            expected_outcome="Error expected",
        )
        res_stale = await tool.execute(act_stale_click, exec_context)
        assert res_stale.success is False
        assert res_stale.error is not None
        assert res_stale.error.code == "browser_stale_ref"

        act_stale_type = BrowserTypeAction(
            ref="e1",
            text="test",
            target_description="Stale type",
            expected_outcome="Error expected",
        )
        res_stale_type = await tool.execute(act_stale_type, exec_context)
        assert res_stale_type.success is False
        assert res_stale_type.error is not None
        assert res_stale_type.error.code == "browser_stale_ref"
    finally:
        await tool.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_tabs_and_back(fixture_server, tmp_path, exec_context):
    tool = BrowserTool(profile_dir=tmp_path / "browser_profile", headless=True)
    try:
        # Navigate first tab
        await tool.execute(
            BrowserNavigateAction(
                url=f"{fixture_server}/list.html",
                target_description="Go to list",
                expected_outcome="Loaded",
            ),
            exec_context,
        )

        # Open new tab
        res_new = await tool.execute(
            BrowserTabsAction(
                op="new", target_description="New tab", expected_outcome="Tab opened"
            ),
            exec_context,
        )
        assert res_new.success is True

        # Navigate second tab
        await tool.execute(
            BrowserNavigateAction(
                url=f"{fixture_server}/form.html",
                target_description="Go to form",
                expected_outcome="Loaded",
            ),
            exec_context,
        )

        # List tabs
        res_list = await tool.execute(
            BrowserTabsAction(
                op="list", target_description="List tabs", expected_outcome="Tabs listed"
            ),
            exec_context,
        )
        assert res_list.success is True
        assert "[0]" in (res_list.output or "")
        assert "[1]" in (res_list.output or "")

        # Switch to tab 0
        res_switch = await tool.execute(
            BrowserTabsAction(
                op="switch", index=0, target_description="Switch tab", expected_outcome="Switched"
            ),
            exec_context,
        )
        assert res_switch.success is True

        # Close tab 1
        res_close = await tool.execute(
            BrowserTabsAction(
                op="close", index=1, target_description="Close tab", expected_outcome="Closed"
            ),
            exec_context,
        )
        assert res_close.success is True

        # Navigate tab 0 to form then back
        await tool.execute(
            BrowserNavigateAction(
                url=f"{fixture_server}/form.html",
                target_description="Go to form",
                expected_outcome="Loaded",
            ),
            exec_context,
        )
        res_back = await tool.execute(
            BrowserBackAction(target_description="Back", expected_outcome="Previous page"),
            exec_context,
        )
        assert res_back.success is True
        assert "list.html" in (res_back.output or "")
    finally:
        await tool.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_download(fixture_server, tmp_path, exec_context):
    dl_dir = tmp_path / "downloads"
    tool = BrowserTool(profile_dir=tmp_path / "browser_profile", download_dir=dl_dir, headless=True)
    try:
        await tool.execute(
            BrowserNavigateAction(
                url=f"{fixture_server}/download.html",
                target_description="Go to downloads",
                expected_outcome="Loaded",
            ),
            exec_context,
        )

        # Trigger download via selector
        act_dl = BrowserDownloadAction(
            dest_dir=str(dl_dir),
            selector="#download-link",
            target_description="Download report",
            expected_outcome="File downloaded",
        )
        res_dl = await tool.execute(act_dl, exec_context)
        assert res_dl.success is True
        assert "Downloaded 'data.csv'" in (res_dl.output or "")

        # Verify file on disk
        target_file = dl_dir / "data.csv"
        assert target_file.exists()
        assert "product,price,weight,warranty" in target_file.read_text(encoding="utf-8")
    finally:
        await tool.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_get_observation(fixture_server, tmp_path, exec_context):
    tool = BrowserTool(profile_dir=tmp_path / "browser_profile", headless=True)
    try:
        await tool.execute(
            BrowserNavigateAction(
                url=f"{fixture_server}/list.html",
                target_description="Go to list",
                expected_outcome="Loaded",
            ),
            exec_context,
        )
        await tool.execute(
            BrowserSnapshotAction(target_description="Snap", expected_outcome="Snapped"),
            exec_context,
        )

        obs = await tool.get_observation()
        assert obs is not None
        assert "list.html" in obs.url
        assert "Item List" in obs.title
        assert len(obs.tabs) == 1
        assert obs.tabs[0].active is True
        assert "[e" in obs.snapshot
    finally:
        await tool.close()
