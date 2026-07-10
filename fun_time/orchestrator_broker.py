from __future__ import annotations

import sys

from .runtime_support import hidden_subprocess_kwargs

BROKER_PROCESS_PATTERN = "osr2_broker\\.app"
BROKER_TRAY_PATTERN = "broker_tray\\.ps1|launch_broker_tray\\.vbs"

# The broker outlives the session that starts it — harem and the user's next Fun
# Time launch keep talking to it.  An integration run wraps its whole process
# tree in a job object that Windows destroys with the run, and a job member's
# children join its job, so the broker has to be created outside one.  The flag
# is inert when the launching process is in no job that permits breakaway, which
# is every production launch.
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def subprocess_window_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return hidden_subprocess_kwargs()


def broker_launch_kwargs() -> dict:
    """Popen kwargs for starting the broker: hidden, and outside our job."""
    kwargs = subprocess_window_kwargs()
    if sys.platform != "win32":
        return kwargs
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_BREAKAWAY_FROM_JOB
    return kwargs
