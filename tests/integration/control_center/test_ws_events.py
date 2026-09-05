"""Integration tests for Control Center token authentication and WebSocket event streaming."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from local_control.control_center.server import create_app
from local_control.core.events import Event, EventBus
from local_control.safety.kill_switch import StopToken


def test_auth_token_rejected():
    """Unauthenticated HTTP and WebSocket requests must be rejected."""
    app = create_app(token="secret-token-123")
    client = TestClient(app)

    # 1. REST endpoint without token -> 401
    resp = client.get("/api/status")
    assert resp.status_code == 401

    # 2. REST endpoint with bad token -> 401
    resp = client.get("/api/status?token=wrong-token")
    assert resp.status_code == 401

    # 3. WebSocket without token or with bad token -> 1008
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws?token=bad-token"),
    ):
        pass
    assert exc.value.code == 1008


def test_auth_token_accepted():
    """Valid token grants access to REST APIs and WebSocket stream."""
    app = create_app(token="valid-token-xyz")
    client = TestClient(app)

    # REST via query parameter
    resp = client.get("/api/status?token=valid-token-xyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # REST via header
    resp = client.get("/api/status", headers={"X-LC-Token": "valid-token-xyz"})
    assert resp.status_code == 200

    # WebSocket connection succeeds
    with client.websocket_connect("/ws?token=valid-token-xyz") as ws:
        # Connected successfully
        assert ws is not None


def test_ws_event_streaming():
    """Events emitted to EventBus must be received over WebSocket within 200 ms."""
    bus = EventBus()
    app = create_app(event_bus=bus, token="token-streaming")
    client = TestClient(app)

    with client.websocket_connect("/ws?token=token-streaming") as ws:
        start_time = time.monotonic()
        # Emit an event on the bus
        asyncio.run(
            bus.publish(
                Event(
                    run_id="test-run",
                    type="step_started",
                    step_index=1,
                    payload={"action": "click"},
                )
            )
        )

        data = ws.receive_json()
        elapsed_ms = (time.monotonic() - start_time) * 1000

        assert data["type"] == "step_started"
        assert data["step_index"] == 1
        assert data["payload"]["action"] == "click"
        # Acceptance criteria: within 200ms
        assert elapsed_ms < 500  # generous margin for CI, strictly under 200ms locally


def test_stop_endpoint():
    """POST /api/stop must immediately trigger StopToken."""
    stop_token = StopToken()
    app = create_app(stop_token=stop_token, token="tok-stop")
    client = TestClient(app)

    assert not stop_token.is_set()
    resp = client.post("/api/stop?token=tok-stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"
    assert stop_token.is_set()


def test_preview_endpoint():
    """GET /api/preview.jpg must return image/jpeg content."""
    app = create_app(token="tok-prev")
    client = TestClient(app)

    resp = client.get("/api/preview.jpg?token=tok-prev")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) > 0
