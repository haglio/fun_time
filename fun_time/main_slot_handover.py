from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from player_core.file_channel import append_command
from player_core.modes import MainMode

from .mode_plan import hud_verb, main_player_display_verb
from .player_status import read_genau_status
from .role_windows import MAIN_BLANK_SETTLE_S, WindowRoles

logger = logging.getLogger(__name__)

WAIT_FOR_GENAU_TO_TURN_SOLID_S = 2.0


@dataclass(frozen=True)
class _Handover:
    to: MainMode
    due: float


class MainSlotHandover:
    def __init__(self, *, windows: WindowRoles, genau_cmd_file: Path,
                 main_player_cmd_file: Path, genau_status_file: Path) -> None:
        self._windows = windows
        self._genau_cmd_file = genau_cmd_file
        self._main_player_cmd_file = main_player_cmd_file
        self._genau_status_file = genau_status_file
        self._pending: _Handover | None = None

    def begin(self, to: MainMode) -> None:
        wait = MAIN_BLANK_SETTLE_S if to == MainMode.VIDEO else WAIT_FOR_GENAU_TO_TURN_SOLID_S
        self._pending = _Handover(to, self._windows.clock() + wait)

    def sync(self, main_mode: MainMode, *, paused: bool) -> None:
        pending = self._pending
        if pending is None:
            return
        if pending.to != main_mode:
            self._pending = None
        elif pending.to == MainMode.VIDEO and self._is_due(pending):
            self._pending = None
            self._genau_turns_into_the_hud()
        elif pending.to == MainMode.GENAU and self._genau_is_solid():
            self._pending = None
            self._main_player_steps_aside(paused=paused)
        elif pending.to == MainMode.GENAU and self._is_due(pending):
            logger.warning("Genau did not report turning solid within %.1fs of the switch; "
                           "the main player stepped aside anyway", WAIT_FOR_GENAU_TO_TURN_SOLID_S)
            self._pending = None
            self._main_player_steps_aside(paused=paused)

    def _is_due(self, pending: _Handover) -> bool:
        return self._windows.clock() >= pending.due

    def _genau_is_solid(self) -> bool:
        return read_genau_status(self._genau_status_file).hud_on is False

    def _genau_turns_into_the_hud(self) -> None:
        append_command(self._genau_cmd_file, hud_verb(MainMode.VIDEO))

    def _main_player_steps_aside(self, *, paused: bool) -> None:
        append_command(self._main_player_cmd_file, main_player_display_verb(MainMode.GENAU))
        self._windows.hide_after_settle("main_player")
        self._windows.restack_main_slot(MainMode.GENAU, paused=paused)
