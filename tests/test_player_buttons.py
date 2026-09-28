"""The controls the main console and each satellite's HUD both carry."""
from __future__ import annotations

from dataclasses import replace

import pytest
from player_core.modes import MainMode

from fun_time.console_buttons import MainSlot, console_rows
from fun_time.satellite_buttons import player_rows

_ON_THE_CONSOLE = {
    "prev": "main_prev",
    "nudge_prev": "main_nudge_prev",
    "nudge_next": "main_nudge_next",
    "next": "main_next",
    "lock": "main_lock",
    "reset": "main_reset",
    "shuffle": "main_shuffle",
    "latest": "main_latest",
    "cycle_version": "main_player_cycle_version",
    "minimize": "main_minimize",
}


def _declared(rows) -> dict:
    return {button.command: button for row in rows for button in row}


@pytest.mark.parametrize("state", [
    {"locked": True, "latest": True, "nothing_to_reset": True, "has_other_versions": True},
    {"locked": False, "latest": False, "nothing_to_reset": False, "has_other_versions": False},
])
def test_a_control_both_huds_carry_looks_and_reads_the_same_on_each(state):
    console = _declared(console_rows(MainSlot(main_mode=MainMode.VIDEO, **state)))
    satellite = _declared(player_rows("portrait", **state))

    for name, command in _ON_THE_CONSOLE.items():
        side = satellite[f"portrait_{name}"]
        assert replace(side, command=command,
                       tooltip=side.tooltip.replace("clip", "video")) == console[command], name
