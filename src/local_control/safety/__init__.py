"""Safety, permissions, and kill switch module."""

from local_control.safety.approval import (
    ApprovalGate,
    AutoApprovalGate,
    CliApprovalGate,
)
from local_control.safety.kill_switch import (
    KillSwitch,
    StopRequestedError,
    StopToken,
    get_default_stop_file_path,
)

__all__ = [
    "ApprovalGate",
    "AutoApprovalGate",
    "CliApprovalGate",
    "KillSwitch",
    "StopRequestedError",
    "StopToken",
    "get_default_stop_file_path",
]
