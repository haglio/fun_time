from __future__ import annotations

from .overlay_progress import progress_file_argument, ready_file_for
from .overlay_window import OverlayWindow
from .session_handoff import LOADING_SCREEN_STALE_S

WINDOW_TITLE = "Fun Time Transition"
STALE_TIMEOUT_S = LOADING_SCREEN_STALE_S


def main() -> None:
    progress_file = progress_file_argument("fun_time.transition_screen")
    OverlayWindow.over_every_monitor(
        progress_file,
        title=WINDOW_TITLE,
        status="Changing over...",
        stale_timeout_s=STALE_TIMEOUT_S,
    ).run(on_shown=lambda: ready_file_for(progress_file).write_text("", encoding="utf-8"))
    progress_file.unlink(missing_ok=True)
    ready_file_for(progress_file).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
