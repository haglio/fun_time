from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from fun_time.project_paths import PROJECT_ICON
from fun_time.win32_process import is_process_alive

LAST_WORDS_LINES = 12


@dataclass(frozen=True)
class LaunchedPlayer:
    name: str
    pid: int
    log_file: Path | None = None


class PlayerDied(Exception):
    def __init__(self, player: LaunchedPlayer, said: str,
                 launched_pids: list[int] | None = None, rfb_hwnd: int = 0):
        super().__init__(f"{player.name} exited during startup: {said or 'it said nothing'}")
        self.player = player
        self.said = said
        self.launched_pids: list[int] = launched_pids if launched_pids is not None else []
        self.rfb_hwnd = rfb_hwnd
        self.origenerator_taken_over = False


def the_last_words(log_file: Path | None, *, lines: int = LAST_WORDS_LINES) -> str:
    if log_file is None:
        return ""
    try:
        said = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    kept = [line.rstrip() for line in said if line.strip()]
    return "\n".join(kept[-lines:])


def raise_if_a_player_died(
    players: Iterable[LaunchedPlayer],
    *,
    alive: Callable[[int], bool] | None = None,
) -> None:
    asks = alive if alive is not None else is_process_alive
    for player in players:
        if player.pid and not asks(player.pid):
            raise PlayerDied(player, the_last_words(player.log_file))


def player_died_message(player: LaunchedPlayer, said: str) -> str:
    return (
        f"Fun Time stopped starting up: {player.name} closed itself before the room "
        f"was up, so the session would have opened without it.\n\n"
        f"{said or 'Its log says nothing.'}"
    )


def show_player_died_alert(text: str) -> None:
    from shared_ui.alert import Level, show_alert  # noqa: PLC0415

    show_alert("Fun Time", text, level=Level.ERROR, icon=PROJECT_ICON)
