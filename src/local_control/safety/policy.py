"""Action classification against deterministic security policy tables (SECURITY_MODEL section 3)."""

import os
import re

from local_control.config.settings import Settings
from local_control.core.actions import (
    Action,
    ClickAction,
    CloseWindowAction,
    DoneAction,
    DragAction,
    FailAction,
    FocusWindowAction,
    ListWindowsAction,
    MoveMouseAction,
    PressKeysAction,
    ScrollAction,
    TypeTextAction,
    WaitAction,
)
from local_control.core.types import Observation, PolicyTier
from local_control.safety.command_rules import classify_command
from local_control.safety.path_rules import classify_path

PolicyResult = tuple[PolicyTier, str, list[str], bool, str]
# (tier, category, reasons, grantable_for_run, human_summary)

COMMUNICATION_APPS = {
    "outlook",
    "teams",
    "slack",
    "discord",
    "whatsapp",
    "telegram",
    "signal",
    "thunderbird",
}

COMMUNICATION_INTENTS = re.compile(
    r"\b(send|reply|post|publish|tweet|share)\b",
    re.IGNORECASE,
)

PAYMENT_INTENTS = re.compile(
    r"\b(pay|purchase|buy now|place order|checkout|confirm payment|subscribe|donate|transfer|send money)\b",
    re.IGNORECASE,
)

CREDENTIAL_INTENTS = re.compile(
    r"\b(password|passwd|pin|otp|2fa|cvv|card[-_ ]?number|cc[-_ ]?number)\b",
    re.IGNORECASE,
)

SUBMIT_PATTERNS = re.compile(
    r"\b(send|submit|post|publish|apply|delete|remove|unsubscribe)\b",
    re.IGNORECASE,
)

PAYMENT_DOMAINS = {
    "paypal.com",
    "checkout.stripe.com",
    "pay.google.com",
    "pay.apple.com",
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
}


def _get_snapshot_ref_text(snapshot: str | None, ref: str | None) -> str:
    if not snapshot or not ref:
        return ""
    target = f"[{ref}]"
    for line in snapshot.splitlines():
        if target in line:
            return line
    return ""


SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\b"),
]


def classify(
    action: Action,
    obs: Observation | None = None,
    settings: Settings | None = None,
) -> PolicyResult:
    """Classify an Action proposal into (tier, category, reasons, grantable_for_run, human_summary).

    Evaluation order: B-* (Blocked) -> C-* (Confirm) -> S-* (Safe) -> C-17 (Unclassified).
    """
    act_type = action.type

    # =========================================================================
    # 1. BLOCKED RULES (B-*)
    # =========================================================================

    # B-11: Abnormal screen state
    if obs and obs.screen_state != "normal":
        return (
            "BLOCKED",
            "B-11",
            [f"Screen state is '{obs.screen_state}' (non-interactive or secure prompt)"],
            False,
            f"Blocked action during abnormal screen state ({obs.screen_state})",
        )

    # B-01: Coordinates bounds check
    if (
        isinstance(action, (ClickAction, MoveMouseAction, ScrollAction))
        and action.x is not None
        and action.y is not None
        and obs
        and obs.image
        and obs.image.model_width
        and obs.image.model_height
        and (
            action.x < 0
            or action.x >= obs.image.model_width
            or action.y < 0
            or action.y >= obs.image.model_height
        )
    ):
        return (
            "BLOCKED",
            "B-01",
            [
                f"Coordinates ({action.x}, {action.y}) are outside model image bounds "
                f"(0..{obs.image.model_width}, 0..{obs.image.model_height})"
            ],
            False,
            f"Blocked out-of-bounds input at ({action.x}, {action.y})",
        )

    if (
        isinstance(action, DragAction)
        and obs
        and obs.image
        and obs.image.model_width
        and obs.image.model_height
    ):
        fx, fy = action.from_point.x, action.from_point.y
        tx, ty = action.to_point.x, action.to_point.y
        mw, mh = obs.image.model_width, obs.image.model_height
        if fx < 0 or fx >= mw or fy < 0 or fy >= mh or tx < 0 or tx >= mw or ty < 0 or ty >= mh:
            return (
                "BLOCKED",
                "B-01",
                [f"Drag coordinates ({fx},{fy})->({tx},{ty}) are outside image bounds"],
                False,
                "Blocked out-of-bounds drag",
            )

    # B-02: Credential prompt / password targeting
    if isinstance(action, (TypeTextAction, PressKeysAction)):
        target_desc = action.target_description or ""
        if CREDENTIAL_INTENTS.search(target_desc):
            return (
                "BLOCKED",
                "B-02",
                [f"Target description '{target_desc}' matches credential intent"],
                False,
                f"Blocked typing into credential field: {target_desc}",
            )
        if obs and obs.foreground:
            fg_title = obs.foreground.title.lower()
            fg_proc = obs.foreground.process_name.lower()
            if any(
                w in fg_title
                for w in (
                    "windows security",
                    "credentialuibroker",
                    "password",
                    "bitwarden",
                    "1password",
                    "keepass",
                )
            ) or any(p in fg_proc for p in ("credentialuibroker.exe",)):
                return (
                    "BLOCKED",
                    "B-02",
                    [f"Foreground window '{obs.foreground.title}' is a credential prompt"],
                    False,
                    f"Blocked typing into credential prompt '{obs.foreground.title}'",
                )

    # B-03: Browser credential field
    if act_type == "browser_type":
        selector = getattr(action, "selector", "") or ""
        target_desc = action.target_description or ""
        expected = action.expected_outcome or ""
        ref = getattr(action, "ref", None)
        ref_text = _get_snapshot_ref_text(
            obs.browser.snapshot if (obs and obs.browser) else None, ref
        )
        combined = f"{selector} {target_desc} {expected} {ref_text}".lower()
        if (
            "password" in selector.lower()
            or "type=password" in ref_text.lower()
            or CREDENTIAL_INTENTS.search(combined)
        ):
            return (
                "BLOCKED",
                "B-03",
                ["Browser password or credential input targeted"],
                False,
                f"Blocked browser password typing: {target_desc or ref_text or selector}",
            )

    # B-04: Payment intent
    if isinstance(action, (ClickAction, PressKeysAction)) or act_type in (
        "browser_click",
        "browser_type",
    ):
        target_desc = action.target_description or ""
        expected = action.expected_outcome or ""
        selector = getattr(action, "selector", "") or ""
        ref = getattr(action, "ref", None)
        ref_text = _get_snapshot_ref_text(
            obs.browser.snapshot if (obs and obs.browser) else None, ref
        )
        combined_text = f"{target_desc} {expected} {selector} {ref_text}"

        is_payment_domain = False
        if obs and obs.browser and obs.browser.url:
            from urllib.parse import urlparse

            netloc = urlparse(obs.browser.url).netloc.lower()
            if any(netloc == pd or netloc.endswith("." + pd) for pd in PAYMENT_DOMAINS):
                is_payment_domain = True

        if PAYMENT_INTENTS.search(combined_text) or is_payment_domain:
            return (
                "BLOCKED",
                "B-04",
                [f"Action mentions payment intent: '{combined_text.strip()}'"],
                False,
                f"Blocked payment intent action: {target_desc or ref_text or selector or 'payment domain'}",
            )

    # B-05 & B-06: Filesystem actions
    if act_type in ("fs_write", "fs_move", "fs_copy", "fs_delete", "fs_mkdir"):
        dest_path = (
            getattr(action, "dst", None)
            or getattr(action, "destination", None)
            or getattr(action, "path", None)
        )
        if dest_path:
            zone, reason = classify_path(dest_path, settings)
            if zone == "protected":
                return (
                    "BLOCKED",
                    "B-05",
                    [f"Target path in protected zone: {reason}"],
                    False,
                    f"Blocked filesystem modification in protected zone: {dest_path}",
                )

    if act_type in ("fs_read", "fs_list", "fs_stat", "fs_copy", "fs_move"):
        src_path = (
            getattr(action, "src", None)
            or getattr(action, "source", None)
            or getattr(action, "path", None)
        )
        if src_path:
            zone, reason = classify_path(src_path, settings)
            if "B-06" in reason or "B-15" in reason:
                return (
                    "BLOCKED",
                    "B-06",
                    [f"Target path is secret or credential location: {reason}"],
                    False,
                    f"Blocked reading secret path: {src_path}",
                )

    # B-07, B-08, B-09, B-10, B-16: Shell command rules
    if act_type == "shell_run":
        cmd_str = getattr(action, "command", "") or ""
        cmd_cwd = getattr(action, "cwd", None)
        tier, cat, reasons, grantable = classify_command(cmd_str, cwd=cmd_cwd)
        if tier == "BLOCKED":
            return tier, cat, reasons, grantable, f"Blocked dangerous shell command: {cmd_str}"

    # B-12: Dangerous key combinations
    if isinstance(action, PressKeysAction):
        keys_lower = [k.lower() for k in action.keys]
        combo_str = "+".join(keys_lower)
        if any(comb in combo_str for comb in ("win+l", "ctrl+alt+delete", "ctrl+alt+del", "win+x")):
            return (
                "BLOCKED",
                "B-12",
                [f"System-intercepted key combination blocked: {combo_str}"],
                False,
                f"Blocked dangerous system hotkey: {combo_str}",
            )
        if "alt+f4" in combo_str and obs and obs.foreground:
            fg_proc = obs.foreground.process_name.lower()
            if "local-control" in fg_proc or "python" in fg_proc:
                return (
                    "BLOCKED",
                    "B-12",
                    ["Alt+F4 targeting agent process is blocked"],
                    False,
                    "Blocked Alt+F4 on agent process",
                )

    # B-13: Browser dangerous schemes
    if act_type == "browser_navigate":
        url = (getattr(action, "url", "") or "").lower()
        if any(
            url.startswith(scheme)
            for scheme in ("file://", "chrome://", "edge://", "javascript:", "data:")
        ) or (url.startswith("about:") and url != "about:blank"):
            return (
                "BLOCKED",
                "B-13",
                [f"Privileged or dangerous browser URL scheme: {url}"],
                False,
                f"Blocked browser navigation to {url}",
            )

    # B-14: Closing agent's own window
    if isinstance(action, CloseWindowAction) and obs and obs.windows:
        for win in obs.windows:
            if win.handle == action.handle and (
                win.pid == os.getpid() or "local-control" in win.title.lower()
            ):
                return (
                    "BLOCKED",
                    "B-14",
                    [f"Cannot close agent's own window (handle {action.handle})"],
                    False,
                    f"Blocked closing agent window (handle {action.handle})",
                )

    # =========================================================================
    # 2. CONFIRM RULES (C-*)
    # =========================================================================

    # C-01: fs_move, fs_delete in allowed_root
    if act_type in ("fs_move", "fs_delete"):
        target_path = (
            getattr(action, "dst", None)
            or getattr(action, "destination", None)
            or getattr(action, "path", None)
        )
        zone, _ = classify_path(target_path, settings) if target_path else ("allowed_root", "")
        if zone == "allowed_root":
            return (
                "CONFIRM",
                "C-01",
                [f"Filesystem {act_type} inside allowed root"],
                True,
                f"{act_type.replace('fs_', '').capitalize()} file: {target_path}",
            )

    # C-02: fs_write, fs_copy with overwrite=True
    if act_type in ("fs_write", "fs_copy") and getattr(action, "overwrite", False):
        target_path = (
            getattr(action, "dst", None)
            or getattr(action, "destination", None)
            or getattr(action, "path", None)
        )
        return (
            "CONFIRM",
            "C-02",
            ["Overwriting existing file requires confirmation"],
            False,
            f"Overwrite file: {target_path}",
        )

    # C-03: fs_* write in user_other or external
    if act_type in ("fs_write", "fs_move", "fs_copy", "fs_delete", "fs_mkdir"):
        target_path = (
            getattr(action, "dst", None)
            or getattr(action, "destination", None)
            or getattr(action, "path", None)
        )
        zone, reason = classify_path(target_path, settings) if target_path else ("user_other", "")
        if zone in ("user_other", "external"):
            return (
                "CONFIRM",
                "C-03",
                [f"Filesystem write in {zone} zone: {reason}"],
                True,
                f"Modify file in {zone} zone: {target_path}",
            )

    # C-04: fs_read in user_other/external or > 5MB
    if act_type in ("fs_read", "fs_list", "fs_stat"):
        target_path = getattr(action, "path", None)
        zone, reason = classify_path(target_path, settings) if target_path else ("allowed_root", "")
        if zone in ("user_other", "external"):
            return (
                "CONFIRM",
                "C-04",
                [f"Reading file in {zone} zone: {reason}"],
                True,
                f"Read file in {zone} zone: {target_path}",
            )

    # C-05, C-06, C-09, C-18: Shell command confirm rules
    if act_type == "shell_run":
        cmd_str = getattr(action, "command", "") or ""
        cmd_cwd = getattr(action, "cwd", None)
        tier, cat, reasons, grantable = classify_command(cmd_str, cwd=cmd_cwd)
        if tier == "CONFIRM":
            return tier, cat, reasons, grantable, f"Execute command: {cmd_str}"

    # C-07: External communication / messaging
    if (
        isinstance(action, (TypeTextAction, PressKeysAction, ClickAction))
        and obs
        and obs.foreground
    ):
        fg_proc = obs.foreground.process_name.lower().replace(".exe", "")
        target_desc = action.target_description or ""
        if fg_proc in COMMUNICATION_APPS and COMMUNICATION_INTENTS.search(target_desc):
            return (
                "CONFIRM",
                "C-07",
                [f"Sending message in communication app '{obs.foreground.process_name}'"],
                False,
                f"Send external message in {obs.foreground.title}: {target_desc}",
            )

    # C-08: Browser form submit / send
    if act_type in ("browser_click", "browser_type"):
        is_submit_flag = getattr(action, "submit", False)
        target_desc = action.target_description or ""
        selector = (getattr(action, "selector", "") or "").lower()
        ref = getattr(action, "ref", None)
        ref_text = _get_snapshot_ref_text(
            obs.browser.snapshot if (obs and obs.browser) else None, ref
        )
        combined = f"{target_desc} {selector} {ref_text}"
        if (
            is_submit_flag
            or "type=submit" in selector
            or "type=submit" in ref_text.lower()
            or SUBMIT_PATTERNS.search(combined)
        ):
            return (
                "CONFIRM",
                "C-08",
                ["Browser form submission requires confirmation"],
                False,
                f"Submit browser form: {target_desc or ref_text or selector or 'submit'}",
            )
        if settings and settings.safety.confirm_browser_type and act_type == "browser_type":
            return (
                "CONFIRM",
                "C-08",
                ["Browser typing confirmation required by safety settings"],
                False,
                f"Type in browser: {target_desc or ref_text or selector}",
            )

    # C-10: close_window (not blocked)
    if isinstance(action, CloseWindowAction):
        return (
            "CONFIRM",
            "C-10",
            [f"Closing application window handle {action.handle}"],
            False,
            f"Close application window (handle {action.handle})",
        )

    # C-11: Sensitive hotkeys
    if isinstance(action, PressKeysAction):
        keys_lower = [k.lower() for k in action.keys]
        combo_str = "+".join(keys_lower)
        if any(comb in combo_str for comb in ("win+r", "ctrl+shift+esc", "win+e", "ctrl+w")):
            return (
                "CONFIRM",
                "C-11",
                [f"Sensitive system hotkey '{combo_str}' requires confirmation"],
                False,
                f"Execute sensitive hotkey '{combo_str}'",
            )

    # C-12: browser_download
    if act_type == "browser_download":
        return (
            "CONFIRM",
            "C-12",
            ["Browser file download requires confirmation"],
            True,
            "Download file in browser",
        )

    # C-13: browser_navigate to new host
    if act_type == "browser_navigate" and settings and settings.safety.confirm_new_hosts:
        nav_url = getattr(action, "url", "")
        from urllib.parse import urlparse

        host = urlparse(nav_url).netloc
        seen_hosts = getattr(settings.safety, "seen_hosts", None)
        if host and (seen_hosts is None or host not in seen_hosts):
            return (
                "CONFIRM",
                "C-13",
                [f"First visit to host '{host}' requires confirmation"],
                True,
                f"Navigate to new host {host}",
            )

    # C-16: Large text or secret patterns in type_text
    if isinstance(action, TypeTextAction):
        if len(action.text) > 1000:
            return (
                "CONFIRM",
                "C-16",
                [f"Text length ({len(action.text)}) exceeds 1000 characters"],
                False,
                "Type large text block (>1000 chars)",
            )
        for pat in SECRET_PATTERNS:
            if pat.search(action.text):
                return (
                    "CONFIRM",
                    "C-16",
                    ["Text content matches pattern resembling API key or secret token"],
                    False,
                    "Type suspected API key or secret token",
                )

    # =========================================================================
    # 3. SAFE RULES (S-*)
    # =========================================================================

    # S-01: Read-only inspection and standard agent control actions
    if isinstance(
        action,
        (
            WaitAction,
            ListWindowsAction,
            DoneAction,
            FailAction,
            MoveMouseAction,
            ScrollAction,
        ),
    ) or act_type in ("zoom_region", "ocr_region", "read_ui_tree", "ask_user"):
        return (
            "SAFE",
            "S-01",
            [f"Safe inspection/control action '{act_type}'"],
            True,
            f"Control action: {act_type}",
        )

    # S-03: focus_window (non-elevated)
    if isinstance(action, FocusWindowAction):
        return (
            "SAFE",
            "S-03",
            [f"Focus window handle {action.handle}"],
            True,
            f"Focus window (handle {action.handle})",
        )

    # S-04: fs_list, fs_stat, fs_read (<= 5MB) in allowed_root
    if act_type in ("fs_list", "fs_stat", "fs_read"):
        target_path = getattr(action, "path", None)
        zone, _ = classify_path(target_path, settings) if target_path else ("allowed_root", "")
        if zone == "allowed_root":
            return (
                "SAFE",
                "S-04",
                ["Read-only filesystem operation in allowed root"],
                True,
                f"Read-only filesystem: {act_type} {target_path}",
            )

    # S-05: fs_mkdir, fs_write (new, overwrite=False), fs_copy in allowed_root
    if act_type in ("fs_mkdir", "fs_write", "fs_copy"):
        target_path = getattr(action, "destination", None) or getattr(action, "path", None)
        zone, _ = classify_path(target_path, settings) if target_path else ("allowed_root", "")
        if zone == "allowed_root" and not getattr(action, "overwrite", False):
            return (
                "SAFE",
                "S-05",
                ["Non-destructive file creation in allowed root"],
                True,
                f"Create file/folder: {target_path}",
            )

    # S-06: Read-only shell allowlist
    if act_type == "shell_run":
        cmd_str = getattr(action, "command", "") or ""
        cmd_cwd = getattr(action, "cwd", None)
        tier, cat, reasons, grantable = classify_command(cmd_str, cwd=cmd_cwd)
        if tier == "SAFE":
            return tier, cat, reasons, grantable, f"Read-only command: {cmd_str}"

    # S-07: Safe browser actions
    if act_type in (
        "browser_navigate",
        "browser_read",
        "browser_snapshot",
        "browser_back",
        "browser_tabs",
        "browser_click",
        "browser_type",
    ):
        return (
            "SAFE",
            "S-07",
            [f"Normal browser action '{act_type}'"],
            True,
            f"Browser interaction: {act_type}",
        )

    # S-02: click, drag, press_keys, type_text not matched by B/C
    if isinstance(action, (ClickAction, DragAction, PressKeysAction, TypeTextAction)):
        desc = action.target_description or act_type
        return (
            "SAFE",
            "S-02",
            [f"Standard user interface action '{act_type}'"],
            True,
            f"Input action: {desc}",
        )

    # =========================================================================
    # 4. UNCLASSIFIED FALLBACK -> CONFIRM (C-17)
    # =========================================================================
    return (
        "CONFIRM",
        "C-17",
        [f"Unclassified action type '{act_type}' defaults to CONFIRM"],
        False,
        f"Unclassified action: {act_type}",
    )
