"""The cover the monitors wear while the room changes shape.

Runs as a subprocess: ``python -m fun_time.transition_screen <progress_file>``,
raised by the session leaving and taken down by the one arriving
(``docs/entering-vr.md``).
"""
from __future__ import annotations

import sys
from pathlib import Path

from .overlay_progress import ready_file_for
from .overlay_window import OverlayWindow

WINDOW_TITLE = "Fun Time Transition"  # distinct: an exact-title lookup resolves one
STALE_TIMEOUT_S = 180.0  # the backstop for a relay that died outright


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m fun_time.transition_screen <progress_file>", file=sys.stderr)
        sys.exit(1)

    progress_file = Path(sys.argv[1])
    OverlayWindow(
        progress_file,
        title=WINDOW_TITLE,
        status="Changing over...",
        stale_timeout_s=STALE_TIMEOUT_S,
    ).run(on_shown=lambda: ready_file_for(progress_file).write_text("", encoding="utf-8"))
    # Nobody else is left to tidy: the session that raised it has exited.
    progress_file.unlink(missing_ok=True)
    ready_file_for(progress_file).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
