from __future__ import annotations

from fun_time.voice_commands import SELF_REPORTING_COMMANDS, VOICE_COMMANDS


def test_the_clip_jumps_report_themselves():
    """Nau flashes the outcome of these three, so Fun Time must not also flash a
    green confirmation — that stacked a confirmation under a red correction."""
    assert SELF_REPORTING_COMMANDS == {
        "nau_compilation", "nau_full_vid", "nau_money_shot",
    }


def test_every_self_reporting_command_is_a_real_voice_command():
    assert SELF_REPORTING_COMMANDS <= set(VOICE_COMMANDS.values())
