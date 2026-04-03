from __future__ import annotations

import sys
from pathlib import Path

from .runtime_support import hidden_subprocess_kwargs

BROKER_PROCESS_PATTERN = "osr2_broker\\.app"
BROKER_TRAY_PATTERN = "broker_tray\\.ps1|launch_broker_tray\\.vbs"

# Absolute path to the sibling osr2_broker project — used for both
# production launches and integration tests (where config.project_dir
# points to a temp dir and cannot be used to locate the broker).
BROKER_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "osr2_broker"


def subprocess_window_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return hidden_subprocess_kwargs()
