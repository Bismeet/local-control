"""Path zone classification and security path resolution according to SECURITY_MODEL section 4.1."""

import fnmatch
import os
from pathlib import Path
from typing import Literal

from local_control.config.settings import Settings

PathZone = Literal["protected", "allowed_root", "user_other", "external"]

SECRET_EXTENSIONS = {".pem", ".key", ".pfx", ".p12", ".kdbx"}
SECRET_NAME_PATTERNS = [".env*", "id_rsa*", "credentials*", "secrets*"]
SECRET_DIR_NAMES = {".ssh", ".gnupg", ".aws", ".azure", ".kube"}


def resolve_path(path: Path | str, default_workdir: Path | str | None = None) -> Path:
    """Normalize and resolve path (handling environment vars, ~, and symlinks)."""
    raw_str = os.path.expandvars(str(path))
    p = Path(raw_str).expanduser()
    if not p.is_absolute():
        base = Path(default_workdir).expanduser() if default_workdir else Path.home()
        p = base / p
    try:
        return p.resolve(strict=False)
    except Exception:
        return p.absolute()


def is_secret_path(p: Path) -> bool:
    """Check if the given path targets known secret files or directories (B-06)."""
    name_lower = p.name.lower()

    # Secret extensions
    if p.suffix.lower() in SECRET_EXTENSIONS:
        return True

    # Secret filename patterns
    for pat in SECRET_NAME_PATTERNS:
        if fnmatch.fnmatch(name_lower, pat):
            return True

    # Secret directory names anywhere in ancestors or path itself
    for part in p.parts:
        part_lower = part.lower()
        if part_lower in SECRET_DIR_NAMES:
            return True
        if part_lower == "gcloud" and ".config" in [x.lower() for x in p.parts]:
            return True

    # Windows Vault / Credential Manager / Protect paths
    parts_lower = [x.lower() for x in p.parts]
    return bool(
        ("credentials" in parts_lower or "vault" in parts_lower or "protect" in parts_lower)
        and ("microsoft" in parts_lower or "appdata" in parts_lower)
    )


def is_browser_profile(p: Path) -> bool:
    """Check if path targets default user browser profile directories (B-06)."""
    parts_lower = [x.lower() for x in p.parts]
    # Google Chrome / Microsoft Edge / Brave User Data
    if "user data" in parts_lower:
        return True
    # Mozilla Firefox profiles
    return bool("mozilla" in parts_lower and "firefox" in parts_lower and "profiles" in parts_lower)


def classify_path(path: Path | str, settings: Settings | None = None) -> tuple[PathZone, str]:
    """Classify path into protected, allowed_root, user_other, or external zone.

    Returns:
        (zone, reason)
    """
    resolved = resolve_path(path)
    res_str = str(resolved).lower()
    res_parts = [part.lower() for part in resolved.parts]

    # 1. Check for UNC paths
    if str(path).startswith("\\\\") or str(path).startswith("//"):
        # Extract host
        unc_parts = [x for x in str(path).replace("/", "\\").split("\\") if x]
        host = unc_parts[0] if unc_parts else ""
        allowed_unc = getattr(settings.safety, "allowed_unc_hosts", []) if settings else []
        if host.lower() not in [h.lower() for h in allowed_unc]:
            return "protected", f"UNC host '{host}' is not in allowed_unc_hosts (B-15)"

    # 2. Check secret locations (B-06)
    if is_secret_path(resolved):
        return "protected", f"Path '{resolved}' targets a secret or credential location (B-06)"

    # 3. Check browser profile directories
    if is_browser_profile(resolved):
        return "protected", f"Path '{resolved}' targets a browser profile directory (B-06)"

    # 4. NTUSER.DAT* files
    if resolved.name.lower().startswith("ntuser.dat"):
        return "protected", "NTUSER.DAT registry hive is protected (B-05)"

    # 5. System directories
    # Windows, Program Files, ProgramData, System Volume Information, $Recycle.Bin
    for part in res_parts:
        if part in {
            "windows",
            "program files",
            "program files (x86)",
            "programdata",
            "system volume information",
            "$recycle.bin",
        }:
            return "protected", f"Path '{resolved}' is in system directory '{part}' (B-05)"

    # 6. AppData / LocalAppData
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    home = str(Path.home()).lower()

    # Exempt agent's own data directory and browser download dir
    agent_dir = str(Path.home() / ".local_control").lower()
    agent_appdata = (
        str(Path(localappdata) / "local-control").lower() if localappdata else "___dummy___"
    )

    is_in_appdata = (appdata and res_str.startswith(appdata.lower())) or (
        localappdata and res_str.startswith(localappdata.lower())
    )

    if is_in_appdata and not (res_str.startswith(agent_dir) or res_str.startswith(agent_appdata)):
        return "protected", f"Path '{resolved}' is in protected AppData location (B-05)"

    # 7. Check if targeting another user's profile (B-15)
    users_dir = Path(home).parent
    if resolved.is_relative_to(users_dir):
        try:
            rel = resolved.relative_to(users_dir)
            profile_name = rel.parts[0].lower() if rel.parts else ""
            my_profile = Path.home().name.lower()
            if profile_name and profile_name != my_profile:
                return "protected", f"Path targets another user profile '{profile_name}' (B-15)"
        except Exception:
            pass

    # 8. Allowed roots: ~/Downloads, ~/Documents, ~/Desktop, ~/Pictures, ~/Videos, ~/Music
    user_home = Path.home()
    default_allowed = [
        user_home / "Downloads",
        user_home / "Documents",
        user_home / "Desktop",
        user_home / "Pictures",
        user_home / "Videos",
        user_home / "Music",
    ]
    if settings:
        for extra in getattr(settings.safety, "allowed_roots", []):
            default_allowed.append(Path(extra).expanduser())

    for root in default_allowed:
        try:
            if resolved.is_relative_to(root.resolve(strict=False)):
                return "allowed_root", f"Path is inside allowed root '{root.name}'"
        except Exception:
            pass

    # 9. User other: elsewhere under %USERPROFILE%
    try:
        if resolved.is_relative_to(user_home.resolve(strict=False)):
            return "user_other", "Path is inside user profile but outside allowed roots"
    except Exception:
        pass

    # 10. External
    return "external", "Path is on an external volume, other drive, or allowed UNC path"
