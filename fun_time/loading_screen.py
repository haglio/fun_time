"""Loading screen for Fun Time startup.

Runs as a subprocess: ``python -m fun_time.loading_screen <progress_file>``

Covers every monitor while the session assembles itself, so the windows are
never watched arriving one at a time, and closes when the orchestrator writes
DONE.  What Esc cancels, if anything, rides the progress file's own line; the
two routes it arrives by are in :mod:`overlay_progress`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .overlay_progress import STARTUP_PHASES

# Distinct from the dashboard's "Fun Time": a title lookup meaning the dashboard
# must never resolve this cover.  Borderless, so the title is never rendered.
WINDOW_TITLE = "Fun Time Loading"

# How long the cover sits on a progress file that has stopped changing before it
# concludes startup died.  Wide enough to outlast the longest single phase --
# the sequencer pins the sum against this.
STALE_TIMEOUT_S = 60.0


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m fun_time.loading_screen <progress_file>", file=sys.stderr)
        sys.exit(1)

    # Tk is the screen's own; the launch imports this module for the title.
    from .overlay_window import OverlayWindow  # noqa: PLC0415

    OverlayWindow(
        Path(sys.argv[1]),
        title=WINDOW_TITLE,
        status=STARTUP_PHASES[0].message,
        stale_timeout_s=STALE_TIMEOUT_S,
    ).run()


if __name__ == "__main__":
    main()
