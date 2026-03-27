from __future__ import annotations

import sys

from .runtime_support import hidden_subprocess_kwargs

BROKER_PROCESS_PATTERN = "fun_time\\.broker_app"
BROKER_TRAY_PATTERN = "broker_tray\\.ps1|launch_broker_tray\\.vbs"


def subprocess_window_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return hidden_subprocess_kwargs()
