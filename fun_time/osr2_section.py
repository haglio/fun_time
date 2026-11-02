from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.file_channel import append_command
from funestra.hud_button import Button
from funestra.main_hud import MainHudModel, aim_row, osr2_controls
from funestra.modes import MainMode, Osr2State
from shared_ui.spacing import BUTTON_WORD_W

from .fun_time_funestras import FunTimeFunestra

if TYPE_CHECKING:
    from funestra.satellite_hud import HudModel


DASHBOARD_OSR2_PART = "dashboard_osr2"


def osr2_part_path(state_dir: Path) -> Path:
    return Path(state_dir) / f"{DASHBOARD_OSR2_PART}.json"


def osr2_readout_path(state_dir: Path, funestra: str) -> Path:
    return Path(state_dir) / f"{funestra}_osr2_readout.txt"


def take_osr2_command(funestra: FunTimeFunestra) -> str:
    return f"{funestra.label}_take_osr2"


TAKE_OSR2_COMMANDS: dict[str, FunTimeFunestra] = {take_osr2_command(funestra): funestra for funestra in FunTimeFunestra}


def funestra_with_the_osr2(state) -> FunTimeFunestra:
    try:
        return FunTimeFunestra(state.osr2_funestra)
    except ValueError:
        return FunTimeFunestra.MAIN


def osr2_said_to(funestra: FunTimeFunestra, has_it: bool) -> str:
    return f"{funestra.label}_osr2|{1 if has_it else 0}"


def osr2_button(funestra: FunTimeFunestra, *, has_it: bool) -> Button:
    if has_it:
        return Button(take_osr2_command(funestra), "OSR2",
                      "This Funestra has the OSR2 — it follows this Funestra's funscripts",
                      width=BUTTON_WORD_W, lit=True)
    return Button(take_osr2_command(funestra), "OSR2",
                  "Take over the OSR2 — it follows this Funestra's funscripts",
                  width=BUTTON_WORD_W)


def osr2_part(holder: FunTimeFunestra, *, main_mode: MainMode, osr2: Osr2State, control: str,
              paused: bool, learned: bool, shape: str, broker: bool,
              max_intensity: int) -> MainHudModel:
    return MainHudModel(
        funestra=holder.label,
        main_mode=main_mode if holder is FunTimeFunestra.MAIN else MainMode.KINO,
        osr2=osr2, osr2_control=control, paused=paused,
        osr2_rows=(aim_row(learned=learned, shape=shape, control=control),),
        osr2_controls=osr2_controls(broker_running=broker),
        max_intensity=max_intensity,
    )


@dataclass(frozen=True)
class DeviceBlock:
    rows: tuple[tuple[Button, ...], ...] = ()
    osr2: str = ""

    @classmethod
    def offered_to(cls, funestra: FunTimeFunestra) -> DeviceBlock:
        return cls(rows=((osr2_button(funestra, has_it=False),),))

    @classmethod
    def held_by(cls, funestra: FunTimeFunestra, osr2: Osr2State) -> DeviceBlock:
        return cls(rows=((osr2_button(funestra, has_it=True),),), osr2=osr2)

    def worn_by(self, model: HudModel) -> HudModel:
        return replace(model, osr2_rows=self.rows, osr2=self.osr2)


def tell_a_hosted_satellite_whose_the_osr2_is(config, told: dict[FunTimeFunestra, bool],
                                         funestra: FunTimeFunestra, has_it: bool) -> None:
    if told.get(funestra) == has_it:
        return
    append_command(config.origenerator_cmd_file, osr2_said_to(funestra, has_it))
    told[funestra] = has_it


def forget_what_a_satellite_was_told_of_the_osr2(told: dict[FunTimeFunestra, bool],
                                            funestra: FunTimeFunestra) -> None:
    told.pop(funestra, None)
