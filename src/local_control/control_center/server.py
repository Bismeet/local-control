"""FastAPI Control Center server with WebSocket event streaming and REST APIs."""

from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.control_center.gate import ControlCenterApprovalGate
from local_control.control_center.preview import PreviewPublisher
from local_control.core.events import Event, EventBus
from local_control.core.run_store import RunStore
from local_control.core.types import ApprovalDecision
from local_control.safety.kill_switch import StopToken

logger = structlog.get_logger(__name__)


class RunCreateRequest(BaseModel):
    goal: str
    autonomy_mode: str = "assisted"
    run_id: str | None = None
    force: bool = False


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "denied", "approved_for_run"]
    note: str | None = None
    request_id: str | None = None


class UserAnswerRequest(BaseModel):
    answer: str
    request_id: str | None = None


def create_app(
    runner: AgentRunner | None = None,
    run_store: RunStore | None = None,
    event_bus: EventBus | None = None,
    gate: ControlCenterApprovalGate | None = None,
    stop_token: StopToken | None = None,
    token: str | None = None,
    preview_publisher: PreviewPublisher | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Factory function creating authenticated FastAPI Control Center application."""
    auth_token = token or secrets.token_urlsafe(16)
    bus = event_bus or (runner.event_bus if runner and runner.event_bus else EventBus())
    store = run_store or (runner.run_store if runner and runner.run_store else RunStore())
    app_gate = gate or ControlCenterApprovalGate(event_bus=bus)
    token_obj = stop_token or (runner.stop_token if runner else StopToken())
    preview = preview_publisher or PreviewPublisher(event_bus=bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("control_center.started", token=auth_token)
        yield
        preview.stop()
        logger.info("control_center.stopped")

    app = FastAPI(
        title="local-control Control Center",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Attach shared dependencies to app state
    app.state.token = auth_token
    app.state.runner = runner
    app.state.run_store = store
    app.state.event_bus = bus
    app.state.gate = app_gate
    app.state.stop_token = token_obj
    app.state.preview_publisher = preview
    app.state.settings = settings or Settings()
    app.state.active_run_id = None
    app.state.active_run_task = None

    # Static assets
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def verify_token(
        request: Request,
        token_query: str | None = Query(default=None, alias="token"),
    ) -> str:
        """Enforce per-process authentication token across API requests."""
        candidate = (
            token_query or request.headers.get("X-LC-Token") or request.cookies.get("lc_token")
        )
        if not candidate or candidate != app.state.token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid or missing token",
            )
        return candidate

    @app.get("/")
    async def index(request: Request, token: str | None = Query(default=None)):
        """Serve main dashboard UI, validating token if query parameter is present."""
        if token and token != app.state.token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        resp = FileResponse(static_dir / "index.html")
        if token == app.state.token:
            resp.set_cookie(key="lc_token", value=app.state.token, httponly=True)
        return resp

    @app.get("/api/status")
    async def get_status(_: str = Depends(verify_token)) -> dict[str, Any]:
        """Return server status, active run ID, and pending interactions."""
        # Ensure active_run_id is synchronized with active_run_task
        if app.state.active_run_id:
            if not app.state.active_run_task or app.state.active_run_task.done():
                app.state.active_run_id = None

        pending_approval = None
        pending_question = None
        if hasattr(app.state.gate, "get_pending_approval"):
            pending_approval = app.state.gate.get_pending_approval()
        if hasattr(app.state.gate, "get_pending_answer"):
            pending_question = app.state.gate.get_pending_answer()

        status_str = "IDLE"
        if app.state.active_run_id:
            if pending_approval:
                status_str = "WAITING_APPROVAL"
            elif pending_question:
                status_str = "WAITING_USER"
            else:
                status_str = "RUNNING"

        return {
            "status": "ok",
            "run_status": status_str,
            "active_run": app.state.active_run_id,
            "pending_approval": pending_approval,
            "pending_question": pending_question,
            "token": app.state.token,
        }

    @app.post("/api/runs")
    async def start_run(
        req: RunCreateRequest,
        _: str = Depends(verify_token),
    ) -> dict[str, Any]:
        """Start an autonomous agent run towards the requested goal."""
        if app.state.runner is None:
            raise HTTPException(status_code=500, detail="No AgentRunner configured on server")

        if app.state.active_run_task and not app.state.active_run_task.done():
            if req.force:
                logger.info("control_center.aborting_previous_run_for_force_start")
                if hasattr(app.state.gate, "abort_all"):
                    app.state.gate.abort_all("Superseded by new run")
                app.state.stop_token.set("Superseded by new run")
                app.state.active_run_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(app.state.active_run_task), timeout=1.0)
                except Exception:
                    pass
            else:
                raise HTTPException(status_code=400, detail="A run is already currently active")

        # Clear stop token if previously set
        if app.state.stop_token.is_set():
            app.state.stop_token.clear()

        rid = req.run_id or f"run-{int(time.time())}"
        app.state.active_run_id = rid

        async def _run_worker():
            app.state.preview_publisher.start()
            try:
                await app.state.runner.run(
                    goal=req.goal,
                    autonomy_mode=req.autonomy_mode,
                    run_id=rid,
                )
            except asyncio.CancelledError:
                logger.info("control_center.runner_task_cancelled", run_id=rid)
            except Exception as e:
                logger.error("control_center.runner_task_error", error=str(e), exc_info=True)
                if app.state.event_bus:
                    await app.state.event_bus.publish(
                        Event(
                            run_id=rid,
                            type="run_finished",
                            payload={"status": "FAILED_EXCEPTION", "error": str(e)},
                        )
                    )
            finally:
                app.state.preview_publisher.stop()
                app.state.active_run_id = None

        task = asyncio.create_task(_run_worker())
        app.state.active_run_task = task
        return {"run_id": rid, "status": "started"}

    @app.get("/api/runs")
    async def list_runs(_: str = Depends(verify_token)) -> list[dict[str, Any]]:
        """List past runs from the RunStore."""
        store: RunStore = app.state.run_store
        return list(store.list_runs())

    @app.get("/api/runs/{run_id}")
    async def get_run_details(
        run_id: str,
        _: str = Depends(verify_token),
    ) -> dict[str, Any]:
        """Get run metadata, latest state, and summary."""
        run_dir = app.state.run_store.get_run_dir(run_id)
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        meta, state, _ = app.state.run_store.load_run(run_id)
        summary_file = run_dir / "summary.md"
        summary_content = (
            summary_file.read_text(encoding="utf-8") if summary_file.exists() else None
        )

        return {
            "meta": meta,
            "state": state.model_dump(mode="json") if state else None,
            "summary": summary_content,
        }

    @app.get("/api/runs/{run_id}/replay")
    async def replay_run(
        run_id: str,
        _: str = Depends(verify_token),
    ) -> dict[str, Any]:
        """Return timeline of steps, actions, verdicts, and screenshots for replay."""
        run_dir = app.state.run_store.get_run_dir(run_id)
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        meta, state, _ = app.state.run_store.load_run(run_id)
        steps_timeline: list[dict[str, Any]] = []

        if state and state.steps:
            for s in state.steps:
                steps_timeline.append(
                    {
                        "step_index": s.step_index,
                        "action": s.planner_response.action.model_dump(mode="json")
                        if s.planner_response
                        else None,
                        "verdict": s.verdict.model_dump(mode="json") if s.verdict else None,
                        "result": s.result.model_dump(mode="json") if s.result else None,
                        "verification": s.verification.model_dump(mode="json")
                        if s.verification
                        else None,
                        "screenshot_path": s.observation_ref
                        if isinstance(s.observation_ref, str)
                        else (getattr(s.observation_ref, "path_model", "")),
                    }
                )

        summary_file = run_dir / "summary.md"
        summary = summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""

        return {
            "run_id": run_id,
            "steps": steps_timeline,
            "summary": summary,
        }

    @app.post("/api/runs/{run_id}/approve")
    async def submit_approval(
        run_id: str,
        req: ApprovalDecisionRequest,
        _: str = Depends(verify_token),
    ) -> dict[str, Any]:
        """Submit approval decision from human user."""
        decision = ApprovalDecision(decision=req.decision, note=req.note)
        resolved = app.state.gate.resolve_approval(decision, request_id=req.request_id)
        return {"status": "ok" if resolved else "not_found", "decision": req.decision}

    @app.post("/api/runs/{run_id}/answer")
    async def submit_answer(
        run_id: str,
        req: UserAnswerRequest,
        _: str = Depends(verify_token),
    ) -> dict[str, Any]:
        """Submit text answer for user inquiry."""
        resolved = app.state.gate.resolve_answer(req.answer, request_id=req.request_id)
        return {"status": "ok" if resolved else "not_found"}

    @app.post("/api/stop")
    async def stop_execution(_: str = Depends(verify_token)) -> dict[str, Any]:
        """Instant kill switch: set StopToken, abort pending gates, and stop preview."""
        app.state.stop_token.set("Stop requested from Control Center")
        if hasattr(app.state.gate, "abort_all"):
            app.state.gate.abort_all("Stop requested from Control Center")
        if app.state.active_run_task and not app.state.active_run_task.done():
            app.state.active_run_task.cancel()
        app.state.preview_publisher.stop()
        app.state.active_run_id = None
        if app.state.event_bus:
            await app.state.event_bus.publish(
                Event(
                    run_id="current",
                    type="run_finished",
                    payload={"status": "STOPPED", "reason": "Emergency stop requested"},
                )
            )
        return {"status": "stopped"}

    @app.get("/api/preview.jpg")
    async def get_preview_frame(_: str = Depends(verify_token)) -> Response:
        """Return live screen preview JPEG frame."""
        frame = app.state.preview_publisher.get_latest_frame()
        return Response(content=frame, media_type="image/jpeg")

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        """Stream real-time EventBus events and live preview frames over WebSocket."""
        # Validate authentication token
        candidate = token or websocket.headers.get("X-LC-Token")
        if not candidate or candidate != app.state.token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        event_queue: asyncio.Queue[Event] = asyncio.Queue()

        async def event_handler(event: Event) -> None:
            await event_queue.put(event)

        app.state.event_bus.subscribe(event_handler)
        logger.debug("control_center.websocket_connected")

        try:
            while True:
                # Send queued events to client
                event = await event_queue.get()
                await websocket.send_json(event.model_dump(mode="json"))
        except (WebSocketDisconnect, asyncio.CancelledError):
            logger.debug("control_center.websocket_disconnected")
        finally:
            app.state.event_bus.unsubscribe(event_handler)

    return app
