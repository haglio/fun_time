from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
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


def osr2_part_path(state_dir: Path) -> Path:
    return Path(state_dir) / f"{DASHBOARD_OSR2_PART}.json"


def osr2_readout_path(state_dir: Path, funestra: str) -> Path:
    return Path(state_dir) / f"{funestra}_osr2_readout.txt"


def take_osr2_command(funestra: RoomFunestra) -> str:
    return f"{funestra.label}_take_osr2"


TAKE_OSR2_COMMANDS: dict[str, RoomFunestra] = {take_osr2_command(funestra): funestra for funestra in RoomFunestra}


def funestra_with_the_osr2(state) -> RoomFunestra:
    try:
        return RoomFunestra(state.osr2_funestra)
    except ValueError:
        return RoomFunestra.MAIN


def osr2_button(funestra: RoomFunestra, *, has_it: bool) -> Button:
    if has_it:
        return Button(take_osr2_command(funestra), "OSR2",
                      "This Funestra has the OSR2 — it follows this Funestra's funscripts",
                      width=BUTTON_WORD_W, lit=True)
    return Button(take_osr2_command(funestra), "OSR2",
                  "Take over the OSR2 — it follows this Funestra's funscripts",
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

    @classmethod
    def offered_to(cls, funestra: RoomFunestra) -> DeviceBlock:
        return cls(rows=((osr2_button(funestra, has_it=False),),))

    @classmethod
    def held_by(cls, funestra: RoomFunestra, osr2: Osr2State) -> DeviceBlock:
        return cls(rows=((osr2_button(funestra, has_it=True),),), osr2=osr2)

    def worn_by(self, model: HudModel) -> HudModel:
        return replace(model, osr2_rows=self.rows, osr2=self.osr2)
