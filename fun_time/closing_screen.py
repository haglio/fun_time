"""Closing screen for Fun Time shutdown.

Runs as a subprocess: ``python -m fun_time.closing_screen <progress_file>``

Covers every monitor while the orchestrator takes the session apart, so the end
of a session is one panel rather than the windows blinking out one after
another.  Drops the ready flag beside *progress_file* the moment it is actually
painted — the orchestrator holds the first kill until then — and closes when
that file reads DONE.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .overlay_progress import ready_file_for
from .overlay_window import OverlayWindow

# Distinct from the dashboard's "Fun Time", for the reason the loading overlay's
# title is: an exact-title lookup must never resolve this borderless cover when
# it means the dashboard.
WINDOW_TITLE = "Fun Time Closing"

# Teardown is over in a couple of seconds, so a progress file that has not moved
# for this long means the orchestrator died holding the cover up.  Far shorter
# than startup's, because what this timeout ends is a panel over the whole
# desktop with nothing left behind it to wait for.
STALE_TIMEOUT_S = 20.0


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m fun_time.closing_screen <progress_file>", file=sys.stderr)
        sys.exit(1)

    progress_file = Path(sys.argv[1])
    OverlayWindow(
        progress_file,
        title=WINDOW_TITLE,
        status="Closing...",
        stale_timeout_s=STALE_TIMEOUT_S,
    ).run(on_shown=lambda: ready_file_for(progress_file).write_text("", encoding="utf-8"))


if __name__ == "__main__":
    main()
