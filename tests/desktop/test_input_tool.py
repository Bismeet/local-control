"""Desktop tests verifying InputTool actions against LC Test Target on Windows."""

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from local_control.config.settings import Settings
from local_control.core.actions import (
    ClickAction,
    Point,
    PressKeysAction,
    TypeTextAction,
)
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ImageRef, ScreenGeometry
from local_control.execution.executor import Executor
from local_control.execution.tools.base import ExecutionContext
from local_control.execution.tools.input_backend import PyAutoGuiBackend
from local_control.execution.tools.input_tool import InputTool
from local_control.execution.tools.window_tool import WindowTool
from local_control.observation.screen import ScreenCapture, init_dpi_awareness
from local_control.observation.windows import WindowManager
from local_control.safety.kill_switch import KillSwitch, StopToken


def query_target_app(port: int, cmd: str) -> dict:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(("127.0.0.1", port))
    try:
        s.sendall(cmd.encode("utf-8") + b"\n")
        data = s.recv(4096).decode("utf-8").strip()
        return json.loads(data)
    finally:
        s.close()


@pytest.fixture(scope="module")
def target_app():
    if os.name != "nt":
        pytest.skip("Desktop tests require Windows.")

    init_dpi_awareness()
    target_app_py = Path(__file__).resolve().parent.parent / "fixtures" / "target_app" / "app.py"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tf:
        port_file = tf.name

    proc = subprocess.Popen(
        [sys.executable, str(target_app_py), "--port", "0", "--port-file", port_file]
    )

    port = None
    for _ in range(50):
        time.sleep(0.1)
        if os.path.exists(port_file) and os.path.getsize(port_file) > 0:
            try:
                with open(port_file) as f:
                    port = int(f.read().strip())
                break
            except Exception:
                pass

    with contextlib.suppress(Exception):
        os.remove(port_file)

    assert port is not None, "Target app failed to report port"

    # Ensure window is ready
    time.sleep(0.5)
    wm = WindowManager()
    windows = wm.list_windows()
    target_win = next((w for w in windows if "LC Test Target" in w.title), None)
    assert target_win is not None, "Target app window not found"

    yield {"proc": proc, "port": port, "window": target_win}

    with contextlib.suppress(Exception):
        query_target_app(port, "quit")
    proc.terminate()
    proc.wait(timeout=3)


@pytest.mark.desktop
@pytest.mark.asyncio
async def test_input_tool_click_increments_counter(target_app: dict) -> None:
    port = target_app["port"]
    hwnd = target_app["window"].handle

    # Focus target app
    wt = WindowTool()
    ctx_dummy = ExecutionContext(
        run_id="test",
        stop=StopToken(),
        settings=Settings.load(),
    )
    from local_control.core.actions import FocusWindowAction

    await wt.execute(
        FocusWindowAction(
            handle=hwnd,
            target_description="Focus LC Test Target",
            expected_outcome="Target app is in foreground",
            settle_ms=200,
        ),
        ctx_dummy,
    )

    # Get Alpha button position
    alpha_info = query_target_app(port, "get_widget_pos Alpha")
    assert alpha_info.get("status") == "ok"
    screen_x = alpha_info["x"]
    screen_y = alpha_info["y"]

    # Build mapper
    capture = ScreenCapture()
    frame = capture.capture(0)
    screen_geom = ScreenGeometry(
        width_px=frame.width,
        height_px=frame.height,
        scale_factor=1.0,
    )
    image_ref = ImageRef(
        path_original="",
        path_model="",
        model_width=frame.width,
        model_height=frame.height,
        phash="",
    )
    mapper = CoordinateMapper(screen=screen_geom, image=image_ref)

    # Map screen coords to image space
    img_pt = mapper.to_image(Point(x=screen_x, y=screen_y))

    token = StopToken()
    ctx = ExecutionContext(
        run_id="test-click",
        stop=token,
        mapper=mapper,
        settings=Settings.load(),
    )

    input_tool = InputTool(backend=PyAutoGuiBackend())
    executor = Executor(tools=[input_tool])

    state_before = query_target_app(port, "read_state")
    count_before = state_before["count"]

    click_action = ClickAction(
        x=img_pt.x,
        y=img_pt.y,
        button="left",
        clicks=1,
        target_description="Alpha button",
        expected_outcome="Counter increments by 1",
        settle_ms=300,
    )

    result = await executor.execute(click_action, ctx)
    assert result.success

    state_after = query_target_app(port, "read_state")
    assert state_after["count"] == count_before + 1


@pytest.mark.desktop
@pytest.mark.asyncio
async def test_input_tool_type_unicode_text(target_app: dict) -> None:
    port = target_app["port"]

    entry_info = query_target_app(port, "get_widget_pos main_entry")
    assert entry_info.get("status") == "ok"
    screen_x = entry_info["x"]
    screen_y = entry_info["y"]

    capture = ScreenCapture()
    frame = capture.capture(0)
    screen_geom = ScreenGeometry(
        width_px=frame.width,
        height_px=frame.height,
        scale_factor=1.0,
    )
    image_ref = ImageRef(
        path_original="",
        path_model="",
        model_width=frame.width,
        model_height=frame.height,
        phash="",
    )
    mapper = CoordinateMapper(screen=screen_geom, image=image_ref)
    img_pt = mapper.to_image(Point(x=screen_x, y=screen_y))

    token = StopToken()
    ctx = ExecutionContext(
        run_id="test-type",
        stop=token,
        mapper=mapper,
        settings=Settings.load(),
    )

    input_tool = InputTool(backend=PyAutoGuiBackend())
    executor = Executor(tools=[input_tool])

    # Focus target app
    wt = WindowTool()
    from local_control.core.actions import FocusWindowAction

    await wt.execute(
        FocusWindowAction(
            handle=target_app["window"].handle,
            target_description="Focus LC Test Target",
            expected_outcome="Target app is in foreground",
            settle_ms=200,
        ),
        ctx,
    )

    # 1. Click into main_entry
    click_act = ClickAction(
        x=img_pt.x,
        y=img_pt.y,
        target_description="main entry",
        expected_outcome="entry focused",
        settle_ms=100,
    )
    await executor.execute(click_act, ctx)

    # 2. Select all and delete previous text
    await executor.execute(
        PressKeysAction(
            keys=["ctrl", "a"],
            target_description="select all text",
            expected_outcome="all text selected",
            settle_ms=50,
        ),
        ctx,
    )
    await executor.execute(
        PressKeysAction(
            keys=["delete"],
            target_description="delete selected text",
            expected_outcome="text cleared",
            settle_ms=50,
        ),
        ctx,
    )

    # 3. Type Unicode text
    unicode_str = "héllo wörld ✓"
    type_act = TypeTextAction(
        text=unicode_str,
        target_description="type unicode text",
        expected_outcome="unicode text entered",
        settle_ms=200,
    )
    res = await executor.execute(type_act, ctx)
    assert res.success

    # Verify via target app state
    state = query_target_app(port, "read_state")
    assert state["main_text"] == unicode_str


@pytest.mark.desktop
@pytest.mark.asyncio
async def test_corner_kill_switch_stops_execution() -> None:
    """Verify that moving cursor to corner triggers StopToken within 300ms."""
    import win32api

    from local_control.observation.screen import ensure_interactive_desktop

    ensure_interactive_desktop()
    token = StopToken()
    ks = KillSwitch(
        token=token,
        poll_interval_s=0.05,
        corner_hold_time_s=0.15,
        corner_margin_px=10,
    )

    with ks:
        for _ in range(25):
            win32api.SetCursorPos((0, 0))
            time.sleep(0.05)
            if token.is_set():
                break
        assert token.is_set()
        assert token.reason() == "corner"
