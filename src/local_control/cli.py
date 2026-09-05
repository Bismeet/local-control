"""Command-line interface for local-control."""

import ctypes
import os
import sys
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from local_control import __version__
from local_control.config.settings import Settings

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
def doctor() -> None:
    """Inspect environment readiness, configuration, and safety boundaries."""
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

    # 3. Settings table with masked secrets
    table = Table(title="Effective Settings (Secrets Masked)", show_header=True)
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


if __name__ == "__main__":
    app()
