from __future__ import annotations

import sys

from app_support.subprocess_utils import hidden_subprocess_kwargs

# The broker and its tray are both `python -m osr2_broker.<module>`; only the
# tray's launcher runs under a script host, and it exits as soon as the tray
# is up. They are kept apart because is_broker_running() asks after the broker
# alone — a live tray is not a live broker.
BROKER_PROCESS_PATTERN = "osr2_broker\\.app"
BROKER_TRAY_PATTERN = "osr2_broker\\.tray"
BROKER_LAUNCHER_PATTERN = "launch_broker_tray\\.vbs"

# The broker outlives the session that starts it — harem and the user's next Fun
# Time launch keep talking to it.  An integration run wraps its whole process
# tree in a job object that Windows destroys with the run, and a job member's
# children join its job, so the broker has to be created outside one.  The flag
# is inert when the launching process is in no job that permits breakaway, which
# is every production launch.
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def subprocess_window_kwargs() -> dict:
    """This session's standard "launch a child without a console" kwargs.

    A thin name of our own over the shared helper, so ``broker_launch_kwargs``
    below has something to extend and every launch site reads the same.
    """
    return hidden_subprocess_kwargs()


def broker_launch_kwargs() -> dict:
    """Popen kwargs for starting the broker: hidden, and outside our job."""
    kwargs = subprocess_window_kwargs()
    if sys.platform != "win32":
        return kwargs
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_BREAKAWAY_FROM_JOB
    return kwargs
