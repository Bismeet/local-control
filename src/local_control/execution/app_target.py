"""Semantic application target resolution for Windows.

Maps user-facing app names (e.g. 'Discord', 'Spotify', 'Chrome') to explicit,
strongly-typed AppTarget objects with process names, window title patterns,
executable paths, and protocol URIs.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from local_control.core.actions import AppTarget

logger = structlog.get_logger(__name__)


@dataclass
class KnownAppDef:
    """Predefined application metadata for high-confidence resolution."""

    name: str
    aliases: list[str]
    process_names: list[str]
    window_title_pattern: str
    protocol_uri: str | None = None
    appx_package: str | None = None
    known_path_patterns: list[str] = field(default_factory=list)
    launch_args: list[str] = field(default_factory=list)


# Registry of common Windows applications
KNOWN_APPS: list[KnownAppDef] = [
    KnownAppDef(
        name="Discord",
        aliases=["discord", "discord app", "discord.exe"],
        process_names=["Discord.exe", "discord.exe"],
        window_title_pattern=r"(?i)discord",
        protocol_uri="discord://",
        known_path_patterns=[
            r"%LOCALAPPDATA%\Discord\Update.exe",
            r"%LOCALAPPDATA%\Discord\app-*\Discord.exe",
        ],
        launch_args=["--processStart", "Discord.exe"],
    ),
    KnownAppDef(
        name="Spotify",
        aliases=["spotify", "spotify music", "spotify.exe"],
        process_names=["Spotify.exe", "spotify.exe"],
        window_title_pattern=r"(?i)spotify",
        protocol_uri="spotify:",
        appx_package="SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
        known_path_patterns=[
            r"%APPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
        ],
    ),
    KnownAppDef(
        name="Google Chrome",
        aliases=["chrome", "google chrome", "google-chrome", "chrome.exe"],
        process_names=["chrome.exe", "Chrome.exe"],
        window_title_pattern=r"(?i)google chrome|chrome",
        known_path_patterns=[
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ],
        launch_args=["--new-window"],
    ),
    KnownAppDef(
        name="Microsoft Edge",
        aliases=["edge", "microsoft edge", "msedge", "msedge.exe"],
        process_names=["msedge.exe"],
        window_title_pattern=r"(?i)microsoft edge|edge",
        known_path_patterns=[
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        launch_args=["--new-window"],
    ),
    KnownAppDef(
        name="Brave",
        aliases=["brave", "brave browser", "brave.exe"],
        process_names=["brave.exe"],
        window_title_pattern=r"(?i)brave",
        known_path_patterns=[
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        launch_args=["--new-window"],
    ),
    KnownAppDef(
        name="Notepad",
        aliases=["notepad", "notepad.exe"],
        process_names=["notepad.exe", "Notepad.exe"],
        window_title_pattern=r"(?i)notepad",
        known_path_patterns=["notepad.exe"],
    ),
    KnownAppDef(
        name="Calculator",
        aliases=["calculator", "calc", "calc.exe"],
        process_names=["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
        window_title_pattern=r"(?i)calculator",
        protocol_uri="calculator:",
        known_path_patterns=["calc.exe"],
    ),
    KnownAppDef(
        name="File Explorer",
        aliases=["explorer", "file explorer", "files", "explorer.exe"],
        process_names=["explorer.exe"],
        window_title_pattern=r"(?i)file explorer|this pc",
        known_path_patterns=["explorer.exe"],
    ),
]


def clean_app_name(raw_name: str) -> str:
    """Strip common conversational wrappers from target application queries."""
    cleaned = raw_name.strip()
    cleaned = re.sub(r"^(?:open|launch|start|focus|switch to)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+(?:from|on|in)\s+(?:the\s+)?(?:taskbar|task\s*bar|dock|desktop|start\s*menu)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+application$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+app$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def find_start_menu_shortcut(app_name: str) -> str | None:
    """Search Windows Start Menu program shortcuts for a matching .lnk file."""
    search_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs"),
    ]
    norm_target = app_name.lower().replace(" ", "")

    for base_dir in search_dirs:
        p = Path(base_dir)
        if not p.exists():
            continue
        try:
            for lnk in p.rglob("*.lnk"):
                stem = lnk.stem.lower().replace(" ", "")
                if norm_target in stem or stem in norm_target:
                    return str(lnk)
        except Exception:
            continue
    return None


def find_app_path_in_registry(app_name: str) -> str | None:
    """Check Windows Registry App Paths keys."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        subkeys = [f"{app_name}.exe", app_name]
        roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        for root in roots:
            for sub in subkeys:
                key_path = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{sub}"
                try:
                    with winreg.OpenKey(root, key_path) as k:
                        val, _ = winreg.QueryValueEx(k, "")
                        if val and os.path.exists(val):
                            return str(val)
                except OSError:
                    continue
    except Exception:
        pass
    return None


def resolve_app_target(query: str, existing_windows: list[Any] | None = None) -> AppTarget:
    """Resolve an application query into a typed, verified AppTarget.

    Hierarchy:
    1. Known application registry match (exact or alias)
    2. Existing running window match
    3. Start menu shortcut / registry App Paths / PATH
    4. General heuristic fallback
    """
    cleaned = clean_app_name(query)
    q_lower = cleaned.lower()

    # 1. Match against KNOWN_APPS
    for app_def in KNOWN_APPS:
        if q_lower == app_def.name.lower() or any(q_lower == a.lower() for a in app_def.aliases):
            return AppTarget(
                type="application",
                name=app_def.name,
                process_name=app_def.process_names[0],
                window_title_pattern=app_def.window_title_pattern,
                protocol=app_def.protocol_uri,
                confidence=0.98,
                strategy="known_registry",
            )
        # Substring alias match
        if any(a.lower() in q_lower or q_lower in a.lower() for a in app_def.aliases):
            return AppTarget(
                type="application",
                name=app_def.name,
                process_name=app_def.process_names[0],
                window_title_pattern=app_def.window_title_pattern,
                protocol=app_def.protocol_uri,
                confidence=0.92,
                strategy="known_registry_fuzzy",
            )

    # 2. Existing running window inspection
    if existing_windows:
        for win in existing_windows:
            title = getattr(win, "title", "") or ""
            proc = getattr(win, "process_name", "") or ""
            if q_lower in title.lower() or q_lower in proc.lower():
                return AppTarget(
                    type="application",
                    name=cleaned,
                    process_name=proc if proc and proc != "unknown" else f"{cleaned}.exe",
                    window_title_pattern=rf"(?i){re.escape(cleaned)}",
                    confidence=0.90,
                    strategy="existing_window",
                )

    # 3. Dynamic Windows resolution: Registry, Start Menu, or PATH
    reg_path = find_app_path_in_registry(cleaned)
    if reg_path:
        proc_name = Path(reg_path).name
        return AppTarget(
            type="application",
            name=cleaned,
            process_name=proc_name,
            window_title_pattern=rf"(?i){re.escape(cleaned)}",
            confidence=0.88,
            strategy="registry_app_paths",
        )

    lnk_path = find_start_menu_shortcut(cleaned)
    if lnk_path:
        return AppTarget(
            type="application",
            name=cleaned,
            process_name=f"{cleaned}.exe",
            window_title_pattern=rf"(?i){re.escape(cleaned)}",
            confidence=0.85,
            strategy="start_menu_shortcut",
        )

    which_path = shutil.which(cleaned) or shutil.which(f"{cleaned}.exe")
    if which_path:
        proc_name = Path(which_path).name
        return AppTarget(
            type="application",
            name=cleaned,
            process_name=proc_name,
            window_title_pattern=rf"(?i){re.escape(cleaned)}",
            confidence=0.82,
            strategy="path_binary",
        )

    # 4. Fallback resolution
    return AppTarget(
        type="application",
        name=cleaned,
        process_name=f"{cleaned}.exe",
        window_title_pattern=rf"(?i){re.escape(cleaned)}",
        confidence=0.75,
        strategy="generic_heuristic",
    )


def get_known_app_def(app_name: str) -> KnownAppDef | None:
    """Retrieve KnownAppDef if app_name matches any known app."""
    cleaned = clean_app_name(app_name).lower()
    for defn in KNOWN_APPS:
        if cleaned == defn.name.lower() or any(cleaned == a.lower() for a in defn.aliases):
            return defn
    return None
