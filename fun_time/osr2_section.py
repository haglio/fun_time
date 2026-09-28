from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from player_core.hud_button import Button
from shared_ui.spacing import BUTTON_WORD_W

from .players import Player

if TYPE_CHECKING:
    from player_core.satellite_hud import HudModel


def take_osr2_command(player: Player) -> str:
    return f"{player.label}_take_osr2"


TAKE_OSR2_COMMANDS: dict[str, Player] = {take_osr2_command(player): player for player in Player}


def player_with_the_osr2(state) -> Player:
    try:
        return Player(state.osr2_player)
    except ValueError:
        return Player.MAIN


def take_osr2_button(player: Player) -> Button:
    return Button(take_osr2_command(player), "OSR2",
                  "Take over the OSR2 — it follows this player's funscripts, "
                  "and its controls move here",
                  width=BUTTON_WORD_W)


@dataclass(frozen=True)
class DeviceBlock:
    rows: tuple[tuple[Button, ...], ...] = ()
    osr2: str = ""
    control: str = ""
    controls: tuple[Button, ...] = ()
    max_intensity: int | None = None

    @classmethod
    def offered_to(cls, player: Player) -> DeviceBlock:
        return cls(rows=((take_osr2_button(player),),))

    def worn_by(self, model: HudModel) -> HudModel:
        return replace(model, osr2_rows=self.rows, osr2=self.osr2,
                       osr2_control=self.control, osr2_controls=self.controls,
                       max_intensity=self.max_intensity)
