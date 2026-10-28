from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from funestra.console import ConsoleModel, aim_row
from funestra.hud_button import Button
from funestra.hud_marks import BROKER_ICON
from funestra.modes import MainMode, Osr2State
from shared_ui.spacing import BUTTON_WORD_W

from .room_funestras import RoomFunestra

if TYPE_CHECKING:
    from funestra.satellite_hud import HudModel


DASHBOARD_OSR2_PART = "dashboard_osr2"


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


def osr2_controls(*, broker: bool) -> tuple[Button, ...]:
    return (
        Button("broker_panel", BROKER_ICON,
               "Broker is running — press to stop it" if broker
               else "Broker is not running — press to start it",
               lit=broker, warn=not broker),
    )


def osr2_part(holder: RoomFunestra, *, main_mode: MainMode, osr2: Osr2State, control: str,
              paused: bool, learned: bool, shape: str, broker: bool,
              max_intensity: int) -> ConsoleModel:
    return ConsoleModel(
        funestra=holder.label,
        main_mode=main_mode if holder is RoomFunestra.MAIN else MainMode.KINO,
        osr2=osr2, osr2_control=control, paused=paused,
        osr2_rows=(aim_row(learned=learned, shape=shape, control=control),),
        osr2_controls=osr2_controls(broker=broker),
        max_intensity=max_intensity,
    )


@dataclass(frozen=True)
class DeviceBlock:
    rows: tuple[tuple[Button, ...], ...] = ()
    osr2: str = ""
    control: str = ""
    controls: tuple[Button, ...] = ()
    max_intensity: int | None = None
    paused: bool = False

    @classmethod
    def offered_to(cls, funestra: RoomFunestra) -> DeviceBlock:
        return cls(rows=((take_osr2_button(funestra),),))

    def worn_by(self, model: HudModel) -> HudModel:
        return replace(model, osr2_rows=self.rows, osr2=self.osr2,
                       osr2_control=self.control, osr2_controls=self.controls,
                       max_intensity=self.max_intensity, paused=self.paused)
