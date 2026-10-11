from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from funestra.hud_button import Button
from shared_ui.spacing import BUTTON_WORD_W

from .room_funestras import RoomFunestra

if TYPE_CHECKING:
    from funestra.satellite_hud import HudModel


def take_osr2_command(funestra: RoomFunestra) -> str:
    return f"{funestra.label}_take_osr2"


TAKE_OSR2_COMMANDS: dict[str, RoomFunestra] = {take_osr2_command(funestra): funestra for funestra in RoomFunestra}


def funestra_with_the_osr2(state) -> RoomFunestra:
    try:
        return RoomFunestra(state.osr2_funestra)
    except ValueError:
        return RoomFunestra.MAIN


def take_osr2_button(funestra: RoomFunestra) -> Button:
    return Button(take_osr2_command(funestra), "OSR2",
                  "Take over the OSR2 — it follows this Funestra's funscripts, "
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
    def offered_to(cls, funestra: RoomFunestra) -> DeviceBlock:
        return cls(rows=((take_osr2_button(funestra),),))

    def worn_by(self, model: HudModel) -> HudModel:
        return replace(model, osr2_rows=self.rows, osr2=self.osr2,
                       osr2_control=self.control, osr2_controls=self.controls,
                       max_intensity=self.max_intensity)
