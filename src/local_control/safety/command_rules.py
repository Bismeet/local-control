"""Command classification and shell security rules according to SECURITY_MODEL section 4.2."""

import re
from pathlib import Path

from local_control.core.types import PolicyTier

CommandClassification = tuple[PolicyTier, str, list[str], bool]
# (tier, category, reasons, grantable_for_run)

# B-07 Permanent Deletion
B07_PATTERNS = [
    r"\bRemove-Item\b.*-Recurse\b",
    r"\brm\s+-[a-zA-Z]*r",
    r"\brmdir\s+/s\b",
    r"\bdel\s+/s\b",
    r"\bClear-RecycleBin\b",
    r"\bformat\b",
    r"\bdiskpart\b",
    r"\bcipher\s+/w",
    r"\bsdelete\b",
]

# B-08 System Altering / Privilege Escalation
B08_PATTERNS = [
    r"\bSet-ExecutionPolicy\b",
    r"\breg\s+(add|delete)\b.*HKLM",
    r"\bbcdedit\b",
    r"\bshutdown\b",
    r"\bRestart-Computer\b",
    r"\bStop-Computer\b",
    r"\bnetsh\b",
    r"\bSet-MpPreference\b",
    r"\bAdd-MpPreference\b.*-Exclusion",
    r"\btakeown\b",
    r"\bicacls\b.*/grant",
    r"\brunas\b",
    r"\bStart-Process\b.*-Verb\s+RunAs",
    r"\bschtasks\s+/create\b",
    r"\bNew-ScheduledTask\b",
    r"\bwmic\b",
    r"\bDisable-[a-zA-Z0-9_-]+",
    r"\bStop-Service\b",
    r"\bsc\s+(config|delete)\b",
    r"\bnet\s+user\b",
    r"\bnet\s+localgroup\b",
]

# B-09 Remote Code Execution
B09_PATTERNS = [
    r"\bInvoke-Expression\b",
    r"\biex\b",
    r"\bInvoke-WebRequest\b.*\|\s*(iex|Invoke-Expression)",
    r"\bcurl\b.*\|\s*(sh|bash|powershell|pwsh|iex)",
    r"-EncodedCommand\b",
    r"\bFromBase64String\b",
    r"\bDownloadString\b",
    r"\bcertutil\b.*-urlcache",
    r"\bmshta\b",
    r"\bregsvr32\b",
    r"\brundll32\b",
]

# B-10 Executing Downloads
B10_PATTERNS = [
    r"[a-zA-Z0-9_\-\\/\.]*(downloads|local-control)[a-zA-Z0-9_\-\\/\.]*\.(exe|msi|bat|cmd|ps1|vbs|js|jar|scr|com|hta|lnk)\b"
]

# B-16 Software Installation
B16_PATTERNS = [
    r"\bmsiexec\b",
    r"\.exe\s+/S\b",
    r"\bsetup\.exe\b",
    r"\bwinget\s+install\s+--silent\b",
]

# C-06 Dangerous Git Operations
C06_PATTERNS = [
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+branch\s+-D\b",
]

# C-09 Software Installation
C09_PATTERNS = [
    r"\bwinget\s+install\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\s+-g\b",
    r"\bchoco\s+install\b",
    r"\bscoop\s+install\b",
    r"\bdotnet\s+tool\s+install\s+-g\b",
]

# C-18 Sending Mail / Webhooks
C18_PATTERNS = [
    r"\bSend-MailMessage\b",
    r"\bcurl\b.*(hooks\.slack\.com|discord\.com/api/webhooks|api\.telegram\.org)",
]

# S-06 Read-only Allowed Commands (first token or tokens)
S06_ALLOWED_COMMANDS = {
    "get-childitem",
    "dir",
    "ls",
    "get-content",
    "cat",
    "type",
    "get-location",
    "pwd",
    "get-item",
    "test-path",
    "select-string",
    "findstr",
    "where.exe",
    "where",
    "get-command",
    "get-process",
    "python --version",
    "python -v",
    "node --version",
    "node -v",
    "pip list",
    "npm ls",
    "dotnet --info",
    "measure-object",
    "get-date",
    "hostname",
    "whoami",
}

# S-06 Forbidden characters/tokens
S06_FORBIDDEN_OPERATORS = [
    "|",
    ";",
    "&&",
    ">",
    ">>",
    "2>",
    "out-file",
    "-outfile",
    "set-content",
    "-setcontent",
    "invoke-",
    "`",
    "$(",
    "$env:",
    "%",
]


def split_statements(command: str) -> list[str]:
    """Split a compound PowerShell command into individual statements while respecting quotes."""
    statements: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for char in command:
        if char in ('"', "'"):
            if in_quote == char:
                in_quote = None
            elif in_quote is None:
                in_quote = char
            current.append(char)
        elif in_quote is None and char in (";", "\n"):
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(char)

    last_stmt = "".join(current).strip()
    if last_stmt:
        statements.append(last_stmt)

    return statements or [command.strip()]


def unwrap_shell_statement(statement: str) -> str:
    """Unwrap outer powershell/pwsh/cmd invocations to inspect the inner command."""
    s = statement.strip()
    # Match powershell / pwsh with optional common flags and -Command / -c
    ps_match = re.match(
        r"^(?:powershell(?:\.exe)?|pwsh(?:\.exe)?)\s+(?:-(?:noprofile|noninteractive|sta|mta|windowstyle\s+\w+|executionpolicy\s+\w+)\s+)*(?:-(?:c(?:ommand)?)\s+)?(.*)$",
        s,
        re.IGNORECASE | re.DOTALL,
    )
    if ps_match:
        inner = ps_match.group(1).strip()
        if (inner.startswith('"') and inner.endswith('"')) or (inner.startswith("'") and inner.endswith("'")):
            inner = inner[1:-1].strip()
        return inner

    # Match cmd /c or /k
    cmd_match = re.match(r"^(?:cmd(?:\.exe)?)\s+(?:/[cqk])\s+(.*)$", s, re.IGNORECASE | re.DOTALL)
    if cmd_match:
        inner = cmd_match.group(1).strip()
        if (inner.startswith('"') and inner.endswith('"')) or (inner.startswith("'") and inner.endswith("'")):
            inner = inner[1:-1].strip()
        return inner

    return s


def classify_single_statement(statement: str) -> CommandClassification:
    """Classify a single PowerShell statement against B, C, and S rules."""
    norm = statement.strip()

    # 1. Check B-07 Permanent Deletion
    for pat in B07_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "BLOCKED", "B-07", [f"Permanent deletion command pattern matched: {pat}"], False

    # 2. Check B-08 System Altering
    for pat in B08_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "BLOCKED", "B-08", [f"System-altering command pattern matched: {pat}"], False

    # 3. Check B-09 Remote Code Execution
    for pat in B09_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "BLOCKED", "B-09", [f"Remote code execution pattern matched: {pat}"], False

    # 4. Check B-10 Executing Downloads
    for pat in B10_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return (
                "BLOCKED",
                "B-10",
                ["Execution of files in download directory is blocked"],
                False,
            )

    # 5. Check B-16 Software Installation
    for pat in B16_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return (
                "BLOCKED",
                "B-16",
                ["Unattended software installation is blocked"],
                False,
            )

    # 6. Check C-06 Dangerous Git Operations
    for pat in C06_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "CONFIRM", "C-06", [f"Dangerous git operation: {pat}"], False

    # 7. Check C-09 Software Installation (non-silent)
    for pat in C09_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "CONFIRM", "C-09", ["Software package installation requires confirmation"], False

    # 8. Check C-18 Sending Mail / Webhooks
    for pat in C18_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "CONFIRM", "C-18", ["External messaging via shell requires confirmation"], False

    # 9. Check S-06 Read-only Allowlist & Safe Launchers
    SAFE_APP_LAUNCHERS = {"start-process", "start", "explorer", "explorer.exe"}

    candidates = [norm]
    unwrapped = unwrap_shell_statement(norm)
    if unwrapped and unwrapped != norm:
        candidates.append(unwrapped)

    for cand in candidates:
        cand_lower = cand.lower()
        has_forbidden = any(op in cand_lower for op in S06_FORBIDDEN_OPERATORS)
        if not has_forbidden:
            tokens = cand_lower.split()
            if tokens:
                cmd0 = tokens[0]
                if cmd0 in SAFE_APP_LAUNCHERS:
                    # Safe application or directory opening (non-elevated, non-script)
                    unsafe_tokens = ("-verb", "runas", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".reg", ".sh", ".iso", ".vhd")
                    if not any(bad in t for t in tokens for bad in unsafe_tokens):
                        return "SAFE", "S-06", ["Command matches safe app launcher allowlist"], True
                elif cmd0 in S06_ALLOWED_COMMANDS:
                    if cmd0 == "get-process" and ("-id" in tokens or "-name" in tokens):
                        pass
                    else:
                        return "SAFE", "S-06", ["Command matches read-only allowlist"], True
                elif len(tokens) >= 2:
                    cmd_two = f"{tokens[0]} {tokens[1]}"
                    if (
                        cmd_two in S06_ALLOWED_COMMANDS
                        or tokens[0] == "git"
                        and tokens[1] in {"status", "log", "diff", "remote"}
                    ):
                        return "SAFE", "S-06", ["Command matches read-only allowlist"], True
                    elif (
                        tokens[0] == "git"
                        and tokens[1] == "branch"
                        and "-d" not in tokens
                        and "-D" not in tokens
                    ):
                        # git branch is safe unless -d or -D
                        return "SAFE", "S-06", ["Command matches read-only allowlist"], True

    # 10. Default: C-05
    tokens = norm.split()
    cmd_name = tokens[0] if tokens else "shell"
    return "CONFIRM", "C-05", [f"Shell command '{cmd_name}' requires confirmation"], True


def inspect_script(path: str | Path) -> CommandClassification:
    """Inspect a script file's contents for B-rule violations before execution."""
    p = Path(path)
    if not p.is_file():
        return "SAFE", "", [], True

    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "SAFE", "", [], True

    for line in content.splitlines():
        norm_line = line.strip()
        if not norm_line or norm_line.startswith(("#", "::", "REM", "//")):
            continue

        for pat in B07_PATTERNS:
            if re.search(pat, norm_line, re.IGNORECASE):
                return (
                    "BLOCKED",
                    "B-07",
                    [f"Script '{p.name}' contains permanent deletion: {pat}"],
                    False,
                )
        for pat in B08_PATTERNS:
            if re.search(pat, norm_line, re.IGNORECASE):
                return (
                    "BLOCKED",
                    "B-08",
                    [f"Script '{p.name}' contains system-altering command: {pat}"],
                    False,
                )
        for pat in B09_PATTERNS:
            if re.search(pat, norm_line, re.IGNORECASE):
                return (
                    "BLOCKED",
                    "B-09",
                    [f"Script '{p.name}' contains remote code execution: {pat}"],
                    False,
                )
        for pat in B10_PATTERNS:
            if re.search(pat, norm_line, re.IGNORECASE):
                return (
                    "BLOCKED",
                    "B-10",
                    [f"Script '{p.name}' contains download execution: {pat}"],
                    False,
                )
        for pat in B16_PATTERNS:
            if re.search(pat, norm_line, re.IGNORECASE):
                return (
                    "BLOCKED",
                    "B-16",
                    [f"Script '{p.name}' contains software installation: {pat}"],
                    False,
                )

    return "SAFE", "", [], True


def classify_command(command: str, cwd: str | Path | None = None) -> CommandClassification:
    """Classify a full command (which may contain multiple statements).

    Classifies by its most dangerous statement (BLOCKED > CONFIRM > SAFE).
    Also inspects any referenced script files (.ps1, .bat, .cmd, .py) for B-rules.
    """
    statements = split_statements(command)
    results = [classify_single_statement(s) for s in statements]

    # Inspect any script files mentioned in the command
    base_dir = Path(cwd) if cwd else Path.cwd()
    script_pattern = re.compile(r"[\'\"]?([^\s\'\"]+\.(?:ps1|bat|cmd|py))[\'\"]?", re.IGNORECASE)
    for match in script_pattern.finditer(command):
        cand_path = Path(match.group(1))
        if not cand_path.is_absolute():
            cand_path = base_dir / cand_path
        if cand_path.is_file():
            script_res = inspect_script(cand_path)
            if script_res[0] == "BLOCKED":
                return script_res

    # Most dangerous wins
    for tier in ("BLOCKED", "CONFIRM", "SAFE"):
        for res in results:
            if res[0] == tier:
                return res

    return "CONFIRM", "C-05", ["Default command classification"], False
