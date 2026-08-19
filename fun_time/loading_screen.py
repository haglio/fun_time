"""Loading screen for Fun Time startup.

Runs as a subprocess: ``python -m fun_time.loading_screen <progress_file>``

Covers every monitor while the session assembles itself, so the windows are
never watched arriving one at a time, and closes when the orchestrator writes
DONE.  Esc asks the orchestrator to abort: the cover stays up, now reading
"Cancelling...", until startup has torn down whatever it had launched, so
nothing half-started is ever revealed.

Esc is bound here for when this window holds the focus, but it is not what the
cancel rests on: the hotkey script is up alongside this cover and hooks the same
key without needing the focus at all, which is what keeps a launch cancellable
after something else has taken it.  Either route drops the same flag, and the
words follow the flag rather than the keypress.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .overlay_progress import cancel_file_for
from .overlay_window import CancelOption, OverlayWindow

# Distinct from the dashboard's "Fun Time": title-based window lookups must
# never resolve the loading overlay when they mean the dashboard (both are
# python processes whose venv-launcher pids don't own their windows). The
# overlay is borderless, so the title is never rendered anywhere.
WINDOW_TITLE = "Fun Time Loading"

# How long the overlay sits on a progress file that has stopped changing before
# it concludes startup died and takes itself down.  Wide enough to outlast the
# longest wait a single startup phase can take — the sequencer pins the sum
# against this.
STALE_TIMEOUT_S = 60.0


def request_startup_cancel(progress_file: str | Path) -> None:
    """Signal the orchestrator to abort startup by dropping the cancel flag.

    The orchestrator's progress reporter watches for this file and raises at
    its next checkpoint, unwinding startup and tearing down whatever launched.
    """
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
