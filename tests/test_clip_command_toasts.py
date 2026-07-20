from __future__ import annotations

from fun_time.voice_commands import SELF_REPORTING_COMMANDS, VOICE_COMMANDS


def test_the_self_reporting_commands_flash_their_own_outcome():
    """Nau flashes the outcome of the clip jumps, and the dispatch flashes F-mode's
    (green on, red off).  Fun Time must not also echo a green "I heard you" on top —
    that stacked a confirmation under a red correction."""
    assert SELF_REPORTING_COMMANDS == {
        "nau_compilation", "nau_full_vid", "nau_money_shot", "fmode_toggle",
    }


def test_every_self_reporting_command_is_a_real_voice_command():
    assert SELF_REPORTING_COMMANDS <= set(VOICE_COMMANDS.values())
