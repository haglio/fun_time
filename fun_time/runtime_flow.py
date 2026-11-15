from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_OFF

from .config import SatelliteChannel
from .fun_time_funestras import FunTimeFunestra
from .hosted_contract import OPEN_SLIDESHOW, said_about
from .mode_plan import build_mode_switch_plan
from .polled_files import READER_HOLD_BUDGET_S, replace_despite_readers

logger = logging.getLogger(__name__)


def write_flag_file(path: str | Path, value: bool) -> None:
    flag = Path(path)
    if not replace_despite_readers(flag, "1" if value else "0"):
        logger.warning("Could not set %s to %d: another process kept it open for %gs",
                       flag.name, value, READER_HOLD_BUDGET_S)


@dataclass(frozen=True)
class ModeSwitchFlowResult:
    next_mode: str
    is_transition: bool
    log_message: str


def apply_mode_switch(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
    main_funestra_paused_file: str | Path,
    main_funestra_cmd_file: str | Path,
) -> ModeSwitchFlowResult:
    plan = build_mode_switch_plan(
        current_mode=current_mode,
        target_mode=target_mode,
        omni_paused=omni_paused,
    )
    if plan.is_transition:
        if plan.main_funestra_should_play is not None:
            write_flag_file(main_funestra_paused_file, not plan.main_funestra_should_play)
        append_command(Path(main_funestra_cmd_file), plan.show_cmd)
    return ModeSwitchFlowResult(
        next_mode=plan.target_mode,
        is_transition=plan.is_transition,
        log_message=plan.log_message,
    )


def hand_the_satellite_to_the_app(funestra: FunTimeFunestra, channel: SatelliteChannel,
                                  origenerator_cmd_file: str | Path) -> None:
    append_command(Path(channel.cmd_file), LOCK_OFF)
    append_command(Path(origenerator_cmd_file), said_about(funestra.label, OPEN_SLIDESHOW))
