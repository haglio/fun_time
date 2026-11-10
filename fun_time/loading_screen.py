from __future__ import annotations

from .overlay_progress import STARTUP_PHASES, progress_file_argument

WINDOW_TITLE = "Fun Time Loading"

STALE_TIMEOUT_S = 60.0
WORDS_THE_LAUNCH_WROTE_BEFORE_THIS_SCREEN_EXISTED = STARTUP_PHASES[0].message


def main() -> None:
    progress_file = progress_file_argument("fun_time.loading_screen")

    from shared_ui.loading_handover import TakingOver  # noqa: PLC0415

    from .overlay_window import OverlayWindow  # noqa: PLC0415

    OverlayWindow.over_every_monitor(
        progress_file,
        title=WINDOW_TITLE,
        status=WORDS_THE_LAUNCH_WROTE_BEFORE_THIS_SCREEN_EXISTED,
        stale_timeout_s=STALE_TIMEOUT_S,
        taking_over=TakingOver.from_the_launcher(),
    ).run()


if __name__ == "__main__":
    main()
