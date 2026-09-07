"""Loading screen for Fun Time startup.

Runs as a subprocess: ``python -m fun_time.loading_screen <progress_file>``

Covers every monitor while the session assembles itself, so the windows are
never watched arriving one at a time, and closes when the orchestrator writes
DONE.  Esc asks the orchestrator to abort; the two routes it arrives by, and
why the cover stays up through the teardown, are in :mod:`overlay_progress`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .overlay_progress import cancel_file_for
from .overlay_window import CancelOption, OverlayWindow

# Distinct from the dashboard's "Fun Time": a title lookup meaning the dashboard
# must never resolve this cover.  Borderless, so the title is never rendered.
WINDOW_TITLE = "Fun Time Loading"

# How long the cover sits on a progress file that has stopped changing before it
# concludes startup died.  Wide enough to outlast the longest single phase --
# the sequencer pins the sum against this.
STALE_TIMEOUT_S = 60.0


def request_startup_cancel(progress_file: str | Path) -> None:
    """Signal the orchestrator to abort by dropping the cancel flag, which its
    progress reporter raises at on the next checkpoint."""
    cancel_file_for(progress_file).write_text("", encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m fun_time.loading_screen <progress_file>", file=sys.stderr)
        sys.exit(1)

    progress_file = Path(sys.argv[1])
    OverlayWindow(
        progress_file,
        title=WINDOW_TITLE,
        status="Starting...",
        stale_timeout_s=STALE_TIMEOUT_S,
        cancel=CancelOption(
            hint="Press Esc to cancel",
            pending="Cancelling...",
            request=lambda: request_startup_cancel(progress_file),
            requested=lambda: cancel_file_for(progress_file).exists(),
        ),
    ).run()


if __name__ == "__main__":
    main()
