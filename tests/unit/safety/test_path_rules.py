"""Unit tests for path_rules.py zone classification and resolution."""

from pathlib import Path

from local_control.safety.path_rules import classify_path, is_secret_path, resolve_path


def test_system_directories_are_protected() -> None:
    system_paths = [
        "C:\\Windows",
        "C:\\Windows\\System32\\cmd.exe",
        "C:\\Program Files\\App\\binary.exe",
        "C:\\Program Files (x86)\\Steam\\steam.exe",
        "C:\\ProgramData\\Secret\\data.db",
        "C:\\System Volume Information",
        "C:\\$Recycle.Bin\\12345",
    ]
    for p in system_paths:
        zone, reason = classify_path(p)
        assert zone == "protected", f"Path {p} should be protected, got {zone} ({reason})"


def test_secret_paths_are_protected() -> None:
    secret_paths = [
        "~/.ssh/id_rsa",
        "~/.ssh/known_hosts",
        "~/.gnupg/secring.gpg",
        "~/.aws/credentials",
        "~/.azure/accessTokens.json",
        "~/.kube/config",
        "~/Downloads/cert.pem",
        "~/Downloads/private.key",
        "~/Downloads/database.kdbx",
        "~/Downloads/.env",
        "~/Downloads/.env.production",
        "~/Downloads/credentials.json",
        "~/Downloads/secrets.yaml",
    ]
    for p in secret_paths:
        resolved = resolve_path(p)
        assert is_secret_path(resolved), f"Path {p} should be detected as secret"
        zone, reason = classify_path(p)
        assert zone == "protected", f"Path {p} should be classified as protected, got {zone}"


def test_allowed_roots() -> None:
    allowed = [
        "~/Downloads/document.pdf",
        "~/Documents/tax_return.xlsx",
        "~/Desktop/notes.txt",
        "~/Pictures/photo.png",
        "~/Videos/recording.mp4",
        "~/Music/song.mp3",
    ]
    for p in allowed:
        zone, reason = classify_path(p)
        assert zone == "allowed_root", f"Path {p} should be allowed_root, got {zone} ({reason})"


def test_user_other() -> None:
    # A path under user home that is not in allowed roots or protected
    user_other_path = Path.home() / "custom_random_folder_xyz" / "file.txt"
    zone, reason = classify_path(user_other_path)
    assert zone == "user_other", f"Path {user_other_path} should be user_other, got {zone}"


def test_external_drive() -> None:
    # Other drives or removable media
    zone, reason = classify_path("E:\\external_backup\\data.tar")
    assert zone == "external"


def test_unc_paths_unallowed_host() -> None:
    zone, reason = classify_path("\\\\evil-server\\share\\payload.exe")
    assert zone == "protected"
    assert "allowed_unc_hosts" in reason
