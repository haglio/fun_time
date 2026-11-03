from __future__ import annotations

from .overlay_progress import progress_file_argument, ready_file_for
from .overlay_window import OverlayWindow

WINDOW_TITLE = "Fun Time Closing"

STALE_TIMEOUT_S = 20.0


def main() -> None:
    progress_file = progress_file_argument("fun_time.closing_screen")
    OverlayWindow.over_every_monitor(
        progress_file,
        title=WINDOW_TITLE,
        status="Closing...",
        stale_timeout_s=STALE_TIMEOUT_S,
    ).run(on_shown=lambda: ready_file_for(progress_file).write_text("", encoding="utf-8"))


if __name__ == "__main__":
    main()
