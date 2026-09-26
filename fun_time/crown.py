from __future__ import annotations

from enum import StrEnum

from player_core.modes import MainMode

from .mode_plan import main_player_displays

CROWN_ICON = "\U0001F451"


class Crown(StrEnum):
    MAIN = "main"
    PORTRAIT = "portrait"

    @property
    def command(self) -> str:
        return f"{self}_crown"


CROWNS = {crown.command: crown for crown in Crown}


def majority(crowned: Crown, *, main_mode: MainMode, main_portrait: bool | None) -> Crown:
    main_takes_it = crowned is Crown.MAIN and main_player_displays(main_mode) and main_portrait
    return Crown.MAIN if main_takes_it else Crown.PORTRAIT
