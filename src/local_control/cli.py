"""Command-line interface for local-control."""

import ctypes
import os
import sys
from typing import Any

import typer
from PIL import Image
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from local_control import __version__
from local_control.config.settings import Settings
from local_control.observation.image import encode_png, is_black_frame
from local_control.observation.observer import Observer
from local_control.observation.screen import ScreenCapture, init_dpi_awareness

app = typer.Typer(
    name="local-control",
    help="A local-first Personal Computer Agent for Windows.",
    no_args_is_help=True,
)
console = Console()


def is_running_elevated() -> bool:
    """Check if the current process is running with administrative privileges on Windows."""
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return False


@app.command()
def version() -> None:
    """Print the local-control version."""
    console.print(f"local-control [bold green]v{__version__}[/bold green]")


@app.command()
def observe() -> None:
    """Capture screen observation, list visible windows, and report screen geometry."""
    init_dpi_awareness()
    settings = Settings.load()
    observer = Observer(settings=settings)

    console.print("[bold blue]Capturing desktop observation...[/bold blue]")
    try:
        obs = observer.observe(step_index=0)
    except Exception as e:
        console.print(f"[bold red]Observation failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    def _safe_str(s: str) -> str:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        return s.encode(enc, errors="replace").decode(enc)

    # Summary Panel
    fg_title = _safe_str(obs.foreground.title) if obs.foreground else "None"
    summary_text = (
        f"Screen Geometry: [bold green]{obs.screen.width_px}x{obs.screen.height_px}[/bold green] "
        f"(Scale: {obs.screen.scale_factor})\n"
        f"Model Image: [bold green]{obs.image.model_width}x{obs.image.model_height}[/bold green] "
        f"(dHash: [cyan]{obs.image.phash}[/cyan])\n"
        f"Screen State: [bold green]{obs.screen_state}[/bold green]\n"
        f"Cursor (model space): [yellow]({obs.cursor.x}, {obs.cursor.y})[/yellow]\n"
        f"Foreground Window: {fg_title}"
    )
    console.print(Panel(summary_text, title="Desktop Observation Summary"))

    # Windows Table
    table = Table(title=f"Visible Windows (Top {len(obs.windows)})", show_header=True)
    table.add_column("Handle", style="cyan", no_wrap=True)
    table.add_column("Title", style="white", max_width=50)
    table.add_column("Process (PID)", style="magenta")
    table.add_column("BBox (x,y,w,h)", style="yellow")
    table.add_column("Foreground?", style="green")

    for w in obs.windows:
        fg_marker = "[bold green]YES[/bold green]" if w.is_foreground else "no"
        bbox_str = f"({w.bbox.x}, {w.bbox.y}, {w.bbox.width}, {w.bbox.height})"
        proc_str = f"{_safe_str(w.process_name)} ({w.pid})"
        table.add_row(str(w.handle), _safe_str(w.title), proc_str, bbox_str, fg_marker)

    console.print(table)


@app.command()
def act(
    action_json: str = typer.Argument(..., help="JSON string representing a validated Action"),
) -> None:
    """Execute a single validated action from JSON with mandatory human approval."""
    if is_running_elevated():
        console.print(
            "[bold red]Refusing to execute: local-control must not run with Administrator privileges for safety.[/bold red]"
        )
        raise typer.Exit(code=1)

    import asyncio

    from pydantic import TypeAdapter

    from local_control.core.actions import Action
    from local_control.core.coordinates import CoordinateMapper
    from local_control.core.types import ImageRef, ScreenGeometry
    from local_control.execution.executor import Executor
    from local_control.execution.tools.base import ExecutionContext
    from local_control.execution.tools.input_tool import InputTool
    from local_control.execution.tools.wait_tool import WaitTool
    from local_control.execution.tools.window_tool import WindowTool
    from local_control.safety.approval import CliApprovalGate
    from local_control.safety.kill_switch import KillSwitch, StopToken

    try:
        action: Action = TypeAdapter(Action).validate_json(action_json)
    except Exception as e:
        console.print(f"[bold red]Invalid action JSON:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    gate = CliApprovalGate(console=console)
    if not gate.request(action):
        console.print("[bold yellow]Action execution cancelled by user.[/bold yellow]")
        raise typer.Exit(code=0)

    init_dpi_awareness()
    token = StopToken()

    with KillSwitch(token=token):
        mapper = None
        if os.name == "nt":
            try:
                capture = ScreenCapture()
                frame = capture.capture(0)
                screen_geom = ScreenGeometry(
                    width_px=frame.width,
                    height_px=frame.height,
                    scale_factor=1.0,
                    monitor_index=0,
                )
                image_ref = ImageRef(
                    path_original="",
                    path_model="",
                    model_width=frame.width,
                    model_height=frame.height,
                    phash="",
                )
                mapper = CoordinateMapper(screen=screen_geom, image=image_ref)
            except Exception:
                pass

        ctx = ExecutionContext(
            run_id="act-manual",
            stop=token,
            mapper=mapper,
            settings=Settings.load(),
        )

        from local_control.execution.tools.observation_tool import ObservationTool

        executor = Executor(
            tools=[
                InputTool(),
                WindowTool(),
                WaitTool(),
                ObservationTool(),
            ]
        )

        result = asyncio.run(executor.execute(action, ctx))
        if result.success:
            console.print(
                f"[bold green]Action {result.action_type} executed successfully in {result.duration_ms}ms[/bold green]"
            )
        else:
            err_msg = result.error.message if result.error else "Unknown error"
            err_code = result.error.code if result.error else "FAILED"
            console.print(
                f"[bold red]Action {result.action_type} failed ({err_code}): {err_msg}[/bold red]"
            )
            raise typer.Exit(code=1)


@app.command()
def run(
    goal: str = typer.Argument(..., help="High-level goal for the agent to achieve"),
    mode: str = typer.Option(
        "assisted", help="Autonomy mode: step, assisted (default), or trusted"
    ),
) -> None:
    """Run the agent loop autonomously towards a goal in step mode."""
    if is_running_elevated():
        console.print(
            "[bold red]Refusing to execute: local-control must not run with Administrator privileges for safety.[/bold red]"
        )
        raise typer.Exit(code=1)

    import asyncio

    from local_control.agent.planner import Planner
    from local_control.agent.runner import AgentRunner
    from local_control.execution.executor import Executor
    from local_control.execution.tools.input_tool import InputTool
    from local_control.execution.tools.observation_tool import ObservationTool
    from local_control.execution.tools.wait_tool import WaitTool
    from local_control.execution.tools.window_tool import WindowTool
    from local_control.models.registry import build as build_model
    from local_control.safety.approval import CliApprovalGate

    init_dpi_awareness()
    settings = Settings.load()

    provider = build_model("planner", settings)
    planner = Planner(provider=provider)
    executor = Executor(tools=[InputTool(), WindowTool(), WaitTool(), ObservationTool()])
    observer = Observer(settings=settings)
    gate = CliApprovalGate(console=console)

    runner = AgentRunner(
        planner=planner,
        executor=executor,
        observer=observer,
        approval_gate=gate,
        settings=settings,
    )

    console.print(f"[bold cyan]Starting autonomous run towards goal:[/bold cyan] {goal}")
    result = asyncio.run(runner.run(goal=goal, autonomy_mode=mode))

    color = "green" if result.status == "COMPLETED" else "red"
    console.print(
        f"\n[bold {color}]Run finished: {result.status} after {result.steps_count} steps[/bold {color}]"
    )
    console.print(f"Run ID: [cyan]{result.run_id}[/cyan]")


@app.command()
def replay(
    run_id: str = typer.Argument(..., help="Run ID or path to run directory to inspect/replay"),
) -> None:
    """Inspect and replay recorded events and state from a previous agent run."""
    from local_control.core.run_store import RunStore

    store = RunStore()
    try:
        _meta, _task_state, events = store.load_run(run_id)
    except Exception as e:
        console.print(f"[bold red]Failed to load run '{run_id}':[/bold red] {e}")
        raise typer.Exit(code=1) from e

    console.print(
        Panel.fit(
            f"[bold blue]Run Replay: {run_id}[/bold blue]",
            subtitle=f"{len(events)} Recorded Events",
        )
    )

    table = Table(show_header=True)
    table.add_column("Step", style="cyan", width=6)
    table.add_column("Type", style="magenta")
    table.add_column("Details", style="green")

    for ev in events:
        step_str = str(ev.step_index) if ev.step_index is not None else "-"
        details = ""
        if ev.type == "action_started":
            details = f"Action: {ev.payload.get('action_type')}"
        elif ev.type == "action_finished":
            success = ev.payload.get("result", {}).get("success")
            details = f"Success: {success}"
        elif ev.type == "step_completed":
            act_info = ev.payload.get("planner_response", {}).get("action", {}).get("type", "")
            details = f"Executed: {act_info}"
        else:
            details = str(ev.payload)[:60]
        table.add_row(step_str, ev.type, details)

    console.print(table)


@app.command()
def doctor() -> None:
    """Inspect environment readiness, configuration, and observation self-tests."""
    console.print(
        Panel.fit(
            f"[bold blue]local-control Doctor[/bold blue] (v{__version__})",
            subtitle="Diagnostic & Readiness Check",
        )
    )

    # 1. Environment checks
    py_version = sys.version_info
    py_ok = py_version >= (3, 11)
    py_status = "[green]OK[/green]" if py_ok else "[red]FAIL (Requires Python 3.11+)[/red]"
    console.print(f"Python Version: {sys.version.split()[0]} -> {py_status}")

    platform_status = (
        "[green]Windows[/green]"
        if os.name == "nt"
        else f"[yellow]{sys.platform} (Non-Windows)[/yellow]"
    )
    console.print(f"Operating System: {platform_status}")

    elevated = is_running_elevated()
    if elevated:
        console.print(
            "[bold red]WARNING: Running as Administrator/Elevated![/bold red] "
            "local-control must run as a standard non-elevated user."
        )
    else:
        console.print("Privilege Level: [green]Standard User (non-elevated)[/green]")

    # 2. Configuration check
    try:
        settings = Settings.load()
        console.print("Configuration: [green]Loaded successfully[/green]")
    except Exception as e:
        console.print(f"Configuration: [red]Failed to load: {e}[/red]")
        raise typer.Exit(code=1) from e

    # 3. Phase 1 Screen Capture & Observation Self-Tests
    console.print("\n[bold]Phase 1 Observation Self-Tests:[/bold]")
    dpi_ok = init_dpi_awareness()
    console.print(
        f"  DPI Awareness Init: {'[green]OK[/green]' if dpi_ok else '[yellow]Fallback[/yellow]'}"
    )

    if os.name == "nt":
        try:
            capture = ScreenCapture()
            frame = capture.capture(monitor_index=0)
            cx = ctypes.windll.user32.GetSystemMetrics(0)
            cy = ctypes.windll.user32.GetSystemMetrics(1)
            metrics_match = frame.width == cx and frame.height == cy
            status = (
                "[green]MATCH[/green]"
                if metrics_match
                else f"[yellow]Differs ({frame.width}x{frame.height} vs {cx}x{cy})[/yellow]"
            )
            console.print(
                f"  Capture Dimensions vs SystemMetrics: {frame.width}x{frame.height} -> {status}"
            )

            # Cursor bounds check
            import win32gui

            cur_x, cur_y = win32gui.GetCursorPos()
            cur_in_bounds = (0 <= cur_x <= frame.width) and (0 <= cur_y <= frame.height)
            cur_status = "[green]OK[/green]" if cur_in_bounds else "[red]OUT OF BOUNDS[/red]"
            console.print(f"  Cursor Position ({cur_x}, {cur_y}) within display: {cur_status}")

            # Image encoding self-test
            img = Image.frombytes(
                "RGB", (frame.width, frame.height), frame.raw_bytes, "raw", "BGRX"
            )
            png_bytes = encode_png(img)
            console.print(f"  PNG Encoding: [green]OK ({len(png_bytes) // 1024} KB)[/green]")

            # Black frame heuristic test
            black_img = Image.new("RGB", (100, 100), color=(0, 0, 0))
            heuristic_ok = is_black_frame(black_img) and not is_black_frame(img)
            console.print(
                f"  Black Frame Detection Heuristic: {'[green]PASS[/green]' if heuristic_ok else '[red]FAIL[/red]'}"
            )

        except Exception as e:
            console.print(f"  Observation Self-Test: [red]FAILED ({e})[/red]")
    else:
        console.print("  Observation Self-Test: [yellow]Skipped (Non-Windows)[/yellow]")

    # 4. Settings table with masked secrets
    table = Table(title="\nEffective Settings (Secrets Masked)", show_header=True)
    table.add_column("Section", style="cyan", no_wrap=True)
    table.add_column("Key", style="magenta")
    table.add_column("Value", style="green")

    masked = settings.masked_dict()

    def _add_rows(prefix: str, d: dict[str, Any]) -> None:
        for k, v in d.items():
            if isinstance(v, dict):
                _add_rows(f"{prefix}.{k}" if prefix else k, v)
            else:
                table.add_row(prefix or "root", k, str(v))

    _add_rows("", masked)
    console.print(table)


policy_app = typer.Typer(help="Policy inspection and explanation commands")
app.add_typer(policy_app, name="policy")


@policy_app.command("explain")
def policy_explain(
    action_file: str = typer.Argument(
        ...,
        help="Path to JSON file containing an Action proposal, or raw JSON string",
    ),
) -> None:
    """Explain policy classification, safety tier, and reasons for an action."""
    from datetime import UTC, datetime
    from pathlib import Path

    from pydantic import TypeAdapter

    from local_control.core.actions import Action
    from local_control.core.types import ImageRef, Observation, Point, ScreenGeometry
    from local_control.safety.validator import SafetyValidator

    try:
        p = Path(action_file)
        raw_data = p.read_text(encoding="utf-8") if p.exists() and p.is_file() else action_file

        action: Action = TypeAdapter(Action).validate_json(raw_data)
    except Exception as e:
        console.print(f"[bold red]Failed to parse action JSON:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    validator = SafetyValidator()
    dummy_obs = Observation(
        step_index=0,
        captured_at=datetime.now(UTC),
        screen=ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0),
        image=ImageRef(
            path_original="",
            path_model="",
            model_width=960,
            model_height=540,
            phash="0" * 16,
        ),
        cursor=Point(x=0, y=0),
    )

    verdict = validator.validate(action, dummy_obs)

    color = (
        "green" if verdict.tier == "SAFE" else ("yellow" if verdict.tier == "CONFIRM" else "red")
    )
    console.print(
        Panel(
            f"[bold]Tier:[/bold] [{color}]{verdict.tier}[/{color}]\n"
            f"[bold]Category:[/bold] {verdict.category}\n"
            f"[bold]Decision (assisted):[/bold] {verdict.decision}\n"
            f"[bold]Grantable for run:[/bold] {verdict.grantable_for_run}\n"
            f"[bold]Reasons:[/bold] {'; '.join(verdict.reasons) or 'None'}\n"
            f"[bold]Summary:[/bold] {verdict.human_summary}",
            title=f"Policy Explanation: {action.type}",
            border_style=color,
        )
    )


if __name__ == "__main__":
    app()
