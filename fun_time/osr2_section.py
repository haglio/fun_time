from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.file_channel import append_command
from funestra.hud_kinds import take_osr2_command
from funestra.main_hud import MainHudModel, aim_row, osr2_controls
from funestra.modes import MainMode, Osr2State

from .fun_time_funestras import FunTimeFunestra

DASHBOARD_OSR2_PART = "dashboard_osr2"


def osr2_part_path(state_dir: Path) -> Path:
    return Path(state_dir) / f"{DASHBOARD_OSR2_PART}.json"


def osr2_readout_path(state_dir: Path, funestra: str) -> Path:
    return Path(state_dir) / f"{funestra}_osr2_readout.txt"


TAKE_OSR2_COMMANDS: dict[str, FunTimeFunestra] = {
    take_osr2_command(funestra.label): funestra for funestra in FunTimeFunestra}


def funestra_with_the_osr2(state) -> FunTimeFunestra:
    try:
        return FunTimeFunestra(state.osr2_funestra)
    except ValueError:
        return FunTimeFunestra.MAIN


def osr2_said_to(funestra: FunTimeFunestra, has_it: bool) -> str:
    return f"{funestra.label}_osr2|{1 if has_it else 0}"


@dataclass(frozen=True)
class Osr2PartFacts:
    main_mode: MainMode
    osr2: Osr2State
    control: str
    paused: bool
    learned: bool
    shape: str
    broker: bool
    max_intensity: int


def osr2_part(holder: FunTimeFunestra, facts: Osr2PartFacts) -> MainHudModel:
    return MainHudModel(
        funestra=holder.label,
        main_mode=facts.main_mode if holder is FunTimeFunestra.MAIN else MainMode.KINO,
        osr2=facts.osr2, osr2_control=facts.control, paused=facts.paused,
        osr2_rows=(aim_row(learned=facts.learned, shape=facts.shape, control=facts.control),),
        osr2_controls=osr2_controls(broker_running=facts.broker),
        max_intensity=facts.max_intensity,
    )


def tell_a_hosted_satellite_whose_the_osr2_is(config, told: dict[FunTimeFunestra, bool],
                                         funestra: FunTimeFunestra, has_it: bool) -> None:
    if told.get(funestra) == has_it:
        return
    append_command(config.origenerator_cmd_file, osr2_said_to(funestra, has_it))
    told[funestra] = has_it

