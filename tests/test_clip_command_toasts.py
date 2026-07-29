from __future__ import annotations

from fun_time.voice_commands import SELF_REPORTING_COMMANDS, VOICE_COMMANDS


def test_the_self_reporting_commands_flash_their_own_outcome():
    """Nau flashes the outcome of the clip and funscript jumps, and the dispatch
    flashes F-mode's (green on, red off) and a discard's ("Unfavorited" vs "Marked
    weird").  Fun Time must not also echo a green "I heard you" on top — that
    stacked a confirmation under a red correction."""
    assert SELF_REPORTING_COMMANDS == {
        "nau_compilation", "nau_full_vid", "nau_clip_jump",
        "nau_funscript_jump", "nau_next_funscripted",
        "portrait_trash", "landscape_trash", "active_trash", "both_trash",
        "portrait_wrong_action", "landscape_wrong_action",
        "active_wrong_action", "both_wrong_action",
        "fmode_toggle", "fmode_on", "fmode_off",
        *(
            f"{player}_fmode{suffix}"
            for player in ("primary", "portrait", "landscape", "both", "active")
            for suffix in ("", "_on", "_off")
        ),
    }


def test_every_spoken_f_mode_is_self_reporting():
    """F-mode is per player now, so voice can hand over any of a dozen spellings —
    bare, sided, or asserting on/off.  The dispatch flashes which way each one
    went, so a spelling left off this list is one that stacks a green echo on top
    of a red "disabled"."""
    spoken = {cmd for cmd in VOICE_COMMANDS.values() if "fmode" in cmd}
    assert spoken, "expected the F-mode phrases to still exist"
    assert spoken <= SELF_REPORTING_COMMANDS


def test_every_spoken_discard_is_self_reporting():
    """Voice hands over whichever spelling was said — sided, bare (active), or
    both — so missing one would stack the echo on that phrase alone.  Genau's own
    "weird clip" is a different player's action and keeps the plain echo."""
    spoken_discards = {cmd for cmd in VOICE_COMMANDS.values() if cmd.endswith("_trash")}
    assert spoken_discards, "expected the weird phrases to still exist"
    assert spoken_discards <= SELF_REPORTING_COMMANDS


def test_every_spoken_wrong_action_is_self_reporting():
    """The dispatch names the act it struck ("Action removed: Alpha") or says
    there was none to strike — neither of which the phrase "wrong action" tells
    you, so echoing it back on top would say nothing and hide both."""
    spoken = {cmd for cmd in VOICE_COMMANDS.values() if cmd.endswith("_wrong_action")}
    assert spoken, "expected the wrong-action phrases to still exist"
    assert spoken <= SELF_REPORTING_COMMANDS


def test_every_self_reporting_command_is_a_real_voice_command():
    assert SELF_REPORTING_COMMANDS <= set(VOICE_COMMANDS.values())
