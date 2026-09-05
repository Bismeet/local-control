"""Unit tests for StopToken and KillSwitch mechanisms."""

import tempfile
import time
from pathlib import Path

import pytest

from local_control.safety.kill_switch import (
    KillSwitch,
    StopRequestedError,
    StopToken,
)


@pytest.mark.unit
def test_stop_token_initial_state() -> None:
    token = StopToken()
    assert not token.is_set()
    assert token.reason() is None
    # check() does not raise
    token.check()


@pytest.mark.unit
def test_stop_token_set_and_reason() -> None:
    token = StopToken()
    token.set("corner")
    assert token.is_set()
    assert token.reason() == "corner"

    # Subsequent set() should not overwrite the first reason
    token.set("hotkey")
    assert token.reason() == "corner"

    with pytest.raises(StopRequestedError) as exc_info:
        token.check()
    assert exc_info.value.reason == "corner"


@pytest.mark.unit
def test_stop_token_clear() -> None:
    token = StopToken()
    token.set("user")
    assert token.is_set()
    token.clear()
    assert not token.is_set()
    assert token.reason() is None
    token.check()


@pytest.mark.unit
def test_kill_switch_trigger_manual() -> None:
    token = StopToken()
    ks = KillSwitch(token=token)
    ks.trigger("test_manual")
    assert token.is_set()
    assert token.reason() == "test_manual"


@pytest.mark.unit
def test_kill_switch_stop_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        stop_file = Path(tmpdir) / "STOP"
        token = StopToken()

        # Start KillSwitch polling the temp stop file
        with KillSwitch(token=token, poll_interval_s=0.05, stop_file_path=stop_file):
            assert not token.is_set()
            # Create stop file
            stop_file.touch()

            # Wait briefly for poller to pick it up
            start = time.monotonic()
            while not token.is_set() and (time.monotonic() - start) < 2.0:
                time.sleep(0.05)

            assert token.is_set()
            assert token.reason() == "stop_file"
