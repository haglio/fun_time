"""The cover the monitors wear while the room changes shape.

Runs as a subprocess: ``python -m fun_time.transition_screen <progress_file>``,
raised by the session leaving and taken down by the one arriving
(``docs/entering-vr.md``).
"""
from __future__ import annotations

from .overlay_progress import progress_file_argument, ready_file_for
from .overlay_window import OverlayWindow
from .session_handoff import COVER_STALE_S

WINDOW_TITLE = "Fun Time Transition"  # distinct: an exact-title lookup resolves one
STALE_TIMEOUT_S = COVER_STALE_S  # a crossing that never arrives; 180 read as dead


def main() -> None:
    progress_file = progress_file_argument("fun_time.transition_screen")
    OverlayWindow.over_every_monitor(
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
