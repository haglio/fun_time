from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from fun_time import preview_marker
from fun_time.child_launch import marks_a_launch
from fun_time.project_paths import PROJECT_ICON
from fun_time.win32_process import get_process_creation_time, is_process_alive

LAST_WORDS_LINES = 12


@dataclass(frozen=True)
class LogFromLaunch:
    path: Path
    start: int

    def lines(self) -> list[str]:
        try:
            with self.path.open("rb") as handle:
                rolled_aside = handle.seek(0, os.SEEK_END) < self.start
                handle.seek(0 if rolled_aside else self.start)
                written = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        return [line.rstrip() for line in written.splitlines()
                if line.strip() and not marks_a_launch(line)]


def _length(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def logs_as_they_stand(*paths: Path) -> tuple[LogFromLaunch, ...]:
    return tuple(LogFromLaunch(path, _length(path)) for path in paths)


@dataclass(frozen=True)
class LaunchedFunestra:
    name: str
    pid: int
    logs: tuple[LogFromLaunch, ...] = ()


class FunestraDied(Exception):
    def __init__(self, funestra: LaunchedFunestra, said: str,
                 launched_pids: list[int] | None = None, rfb_hwnd: int = 0):
        super().__init__(f"{funestra.name} exited during startup: {said or 'it said nothing'}")
        self.funestra = funestra
        self.said = said
        self.launched_pids: list[int] = launched_pids if launched_pids is not None else []
        self.rfb_hwnd = rfb_hwnd
        self.origenerator_taken_over = False


def the_last_words(logs: Iterable[LogFromLaunch], *, lines: int = LAST_WORDS_LINES) -> str:
    said = [line for log in logs for line in log.lines()]
    return "\n".join(said[-lines:])


def raise_if_a_funestra_died(
    funestras: Iterable[LaunchedFunestra],
    *,
    alive: Callable[[int], bool] | None = None,
) -> None:
    asks = alive if alive is not None else is_process_alive
    for funestra in funestras:
        if funestra.pid and not asks(funestra.pid):
            raise FunestraDied(funestra, the_last_words(funestra.logs))


PART_NAMES = {
    "main_funestra_pid": "the Main Funestra",
    "portrait_pid": "the Portrait Funestra",
    "landscape_pid": "the Landscape Funestra",
    "dashboard_pid": "the dashboard",
    "audio_pid": "the audio companion",
    "origenerator_pid": "Origenerator Core",
}


def the_part_that_closed(children: Mapping, *,
                         created_at: Callable[[int], int | None] | None = None) -> str | None:
    born = created_at or get_process_creation_time
    for key, child in children.items():
        if child.created_at and born(child.pid) != child.created_at:
            return PART_NAMES.get(key, key)
    return None


def part_closed_message(part: str) -> str:
    return (f"Fun Time stopped: {part} closed itself while the room was up, "
            f"so the whole session was closed with it.")


def funestra_died_message(funestra: LaunchedFunestra, said: str) -> str:
    return (
        f"Fun Time stopped starting up: {funestra.name} closed itself before the room "
        f"was up, so the session would have opened without it.\n\n"
        f"{said or 'Its log says nothing.'}"
    )


def show_funestra_died_alert(text: str) -> None:
    from shared_ui.alert import Level, show_alert  # noqa: PLC0415

    shown = preview_marker.shown_as()
    show_alert(preview_marker.app_title(shown), text, level=Level.ERROR,
               icon=preview_marker.icon_file(PROJECT_ICON, shown))
