"""The full dispatch-command id set, pinned byte-for-byte.

Command ids are public surface: windows_bridge_hotkeys.ahk queues them, the
player HUDs in player_core post them (``portrait_minimize``,
``portrait_play_video|<path>``), Nau's console posts more, and the in-app
reference prints them.  The command-registry restructure (audit item 33) may
move where an id is *defined*, but no id may change spelling — this snapshot
is the gate.  It unions every id the three in-repo surfaces name (the spoken
vocabulary, the AHK script, the reference) plus the handful only a player HUD
or console posts, and holds the result to one literal list.

The act-filter ids (``filter_<scope>_<act>``) come from the content overlay,
which differs per machine, so they are pinned by shape against
:mod:`fun_time.filter_vocab` rather than by literal act names.  The numeric
families (``genau_amp_50``, ``nau_speed_150``, ...) are pinned by regenerating
them the way the vocabulary does.
"""
from __future__ import annotations

import re
from pathlib import Path

from fun_time.command_reference import build_reference_sections
from fun_time.filter_vocab import FILTER_ACTS, set_command
from fun_time.voice_commands import VOICE_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Commands no spoken phrase, hotkey or reference row names: posted straight off
# a player's own surface as a literal string in that player's repo.  The three
# minimize buttons live on the HUDs (player_core / Nau's console); the speed
# pair is Genau's console's own ± marks beside its drive readout.
HUD_ONLY_COMMAND_IDS = (
    "genau_speed_down",
    "genau_speed_up",
    "landscape_minimize",
    "main_minimize",
    "portrait_minimize",
)

# The argument-carrying forms, matched by prefix rather than listed whole: the
# HUD thumbnail clicks and Nau's volume slider carry their payload after a "|".
PREFIXED_COMMAND_FORMS = (
    "audio_set_volume|",
    "landscape_lock_video|",
    "landscape_play_video|",
    "portrait_lock_video|",
    "portrait_play_video|",
)

# Every literal command id, sorted.  The overlay-derived filter ids and the
# generated numeric families are asserted separately below.
EXPECTED_COMMAND_IDS = HUD_ONLY_COMMAND_IDS + (
    "active_action_loop",
    "active_cycle_action",
    "active_cycle_seed",
    "active_fmode",
    "active_fmode_off",
    "active_fmode_on",
    "active_latest",
    "active_lock_action",
    "active_lock_off",
    "active_lock_on",
    "active_more_seeds",
    "active_next",
    "active_no_filter",
    "active_no_loop",
    "active_prev",
    "active_reset",
    "active_seed_loop",
    "active_shuffle",
    "active_trash",
    "active_wrong_action",
    "audio_mute",
    "audio_unmute",
    "audio_volume_down",
    "audio_volume_up",
    "backslash_key",
    "both_action_loop",
    "both_cycle_action",
    "both_cycle_seed",
    "both_fmode",
    "both_fmode_off",
    "both_fmode_on",
    "both_latest",
    "both_lock_action",
    "both_lock_off",
    "both_lock_on",
    "both_more_seeds",
    "both_next",
    "both_no_filter",
    "both_no_loop",
    "both_prev",
    "both_reset",
    "both_seed_loop",
    "both_shuffle",
    "both_trash",
    "both_wrong_action",
    "broker_panel",
    "broker_start",
    "broker_stop",
    "browse_library",
    "clipper_save",
    "enter_omnipause",
    "fmode_off",
    "fmode_on",
    "fmode_toggle",
    "genau_activate",
    "genau_amplitude_down",
    "genau_amplitude_up",
    "genau_center_down",
    "genau_center_up",
    "genau_clip_seconds_down",
    "genau_clip_seconds_up",
    "genau_cruise_off",
    "genau_cruise_on",
    "genau_cycle_shape",
    "genau_cycle_shape_prev",
    "genau_next_clip",
    "genau_park",
    "genau_prev_clip",
    "genau_release",
    "genau_retract",
    "genau_toggle_auto",
    "genau_toggle_cruise",
    "genau_weird_clip",
    "help_reference",
    "help_reference_close",
    "hybrid_activate",
    "landscape_action_loop",
    "landscape_cycle_action",
    "landscape_cycle_seed",
    "landscape_fmode",
    "landscape_fmode_off",
    "landscape_fmode_on",
    "landscape_latest",
    "landscape_lock",
    "landscape_lock_action",
    "landscape_lock_off",
    "landscape_lock_on",
    "landscape_loop",
    "landscape_more_seeds",
    "landscape_nav_down",
    "landscape_nav_left",
    "landscape_nav_right",
    "landscape_nav_up",
    "landscape_next",
    "landscape_no_filter",
    "landscape_no_loop",
    "landscape_prev",
    "landscape_reset",
    "landscape_say_enhanced_only",
    "landscape_say_experiments",
    "landscape_say_favorites",
    "landscape_say_filter_enhanced",
    "landscape_say_fix_eyes",
    "landscape_say_fix_face",
    "landscape_say_fix_hands",
    "landscape_say_fix_teeth",
    "landscape_say_go_now",
    "landscape_say_pause_slideshow",
    "landscape_say_play_slideshow",
    "landscape_say_requests",
    "landscape_say_start_slideshow",
    "landscape_say_stop_slideshow",
    "landscape_seed_loop",
    "landscape_shuffle",
    "landscape_trash",
    "landscape_wrong_action",
    "main_fmode",
    "main_fmode_off",
    "main_fmode_on",
    "main_latest",
    "main_lock",
    "main_lock_off",
    "main_lock_on",
    "main_next",
    "main_nudge_next",
    "main_nudge_prev",
    "main_prev",
    "main_reset",
    "main_shuffle",
    "nau_activate",
    "nau_clip_jump",
    "nau_compilation",
    "nau_cycle_version",
    "nau_end_compilation",
    "nau_full_vid",
    "nau_funscript_jump",
    "nau_length_full",
    "nau_length_mixed",
    "nau_length_shorts",
    "nau_loop_cancel",
    "nau_next_funscripted",
    "nau_record_down",
    "nau_record_tap",
    "nau_record_up",
    "nau_speed_down",
    "nau_speed_up",
    "nau_toggle_length",
    "omnipause_toggle",
    "origenerator_activate",
    "pause",
    "play",
    "players_activate",
    "portrait_action_loop",
    "portrait_cycle_action",
    "portrait_cycle_seed",
    "portrait_fmode",
    "portrait_fmode_off",
    "portrait_fmode_on",
    "portrait_latest",
    "portrait_lock",
    "portrait_lock_action",
    "portrait_lock_off",
    "portrait_lock_on",
    "portrait_loop",
    "portrait_more_seeds",
    "portrait_nav_down",
    "portrait_nav_left",
    "portrait_nav_right",
    "portrait_nav_up",
    "portrait_next",
    "portrait_no_filter",
    "portrait_no_loop",
    "portrait_prev",
    "portrait_reset",
    "portrait_say_enhanced_only",
    "portrait_say_experiments",
    "portrait_say_favorites",
    "portrait_say_filter_enhanced",
    "portrait_say_fix_eyes",
    "portrait_say_fix_face",
    "portrait_say_fix_hands",
    "portrait_say_fix_teeth",
    "portrait_say_go_now",
    "portrait_say_pause_slideshow",
    "portrait_say_play_slideshow",
    "portrait_say_requests",
    "portrait_say_start_slideshow",
    "portrait_say_stop_slideshow",
    "portrait_seed_loop",
    "portrait_shuffle",
    "portrait_trash",
    "portrait_wrong_action",
    "projection_cycle",
    "quarter_button",
    "quit",
    "recenter_view",
    "relief_omnipause",
    "satellites_toggle",
    "speed_down",
    "speed_max",
    "speed_min",
    "speed_up",
    "tilt_down",
    "tilt_reset",
    "tilt_up",
    "voice_off",
    "voice_toggle",
)


def _expected_numeric_ids() -> set[str]:
    """The generated numeric families, rebuilt the way the vocabulary builds them."""
    ids = {
        f"genau_{axis}_{value}"
        for axis in ("amp", "center", "speed")
        for value in range(0, 101, 10)
    }
    ids |= {f"genau_clip_seconds_{value}" for value in range(1, 61)}
    ids |= {f"nau_speed_{pct}" for pct in (25, 50, 75, 100, 125, 150, 175, 200)}
    return ids


def _expected_filter_ids() -> set[str]:
    """The act-filter ids, shaped from whatever overlay this machine carries."""
    return {
        set_command(scope, query)
        for scope in ("both", "portrait", "landscape")
        for query in FILTER_ACTS
    }


def _ahk_ids() -> set[str]:
    script = (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")
    return set(re.findall(r'QueueCommand\("([^"]+)"\)', script))


def _reference_ids() -> set[str]:
    return {
        command
        for section in build_reference_sections()
        for row in section.rows
        for command in row.commands
    }


def test_the_command_id_set_is_exactly_the_snapshot():
    surfaced = set(VOICE_COMMANDS.values()) | _ahk_ids() | _reference_ids()
    surfaced |= set(HUD_ONLY_COMMAND_IDS)
    expected = set(EXPECTED_COMMAND_IDS) | _expected_numeric_ids() | _expected_filter_ids()
    unexpected = surfaced - expected
    missing = expected - surfaced
    assert not unexpected, f"command ids not in the snapshot: {sorted(unexpected)}"
    assert not missing, f"snapshot ids no surface names any more: {sorted(missing)}"


def test_the_snapshot_is_sorted_and_duplicate_free():
    """Sorted so a diff of this file reads as the id it adds or removes."""
    assert list(EXPECTED_COMMAND_IDS[5:]) == sorted(set(EXPECTED_COMMAND_IDS[5:]))
    assert list(HUD_ONLY_COMMAND_IDS) == sorted(set(HUD_ONLY_COMMAND_IDS))
    assert len(set(EXPECTED_COMMAND_IDS)) == len(EXPECTED_COMMAND_IDS)


def test_the_prefixed_forms_keep_their_spellings():
    """The payload-carrying prefixes are cross-repo surface too: the satellite
    HUDs build ``<side>_play_video|<path>`` / ``<side>_lock_video|<path>`` and
    Nau's slider builds ``audio_set_volume|<level>`` in their own repos."""
    assert PREFIXED_COMMAND_FORMS == (
        "audio_set_volume|",
        "landscape_lock_video|",
        "landscape_play_video|",
        "portrait_lock_video|",
        "portrait_play_video|",
    )
