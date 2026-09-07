"""Closing screen for Fun Time shutdown.

Runs as a subprocess: ``python -m fun_time.closing_screen <progress_file>``

Covers every monitor while the orchestrator takes the session apart, so the end
is one panel rather than windows blinking out one after another.  Drops the
ready flag the moment it is painted -- the orchestrator holds the first kill
until then -- and closes when the progress file reads DONE.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .overlay_progress import ready_file_for
from .overlay_window import OverlayWindow

# Distinct from the dashboard's "Fun Time", for the loading cover's reason.
WINDOW_TITLE = "Fun Time Closing"

# Teardown is over in seconds, so a file unmoved this long means the
# orchestrator died holding the cover up.  Far shorter than startup's: what
# this ends is a panel with nothing left under it to wait for.
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
