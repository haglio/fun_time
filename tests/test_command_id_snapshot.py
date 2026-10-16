"""The full dispatch-command id set, pinned byte-for-byte.

Command ids are public surface: windows_bridge_hotkeys.ahk queues them, the
Funestra HUDs in funestra post them (``portrait_minimize``,
``portrait_play_video|<path>``), the Main Funestra's console posts more, and the in-app
reference prints them.  The command-registry restructure (audit item 33) may
move where an id is *defined*, but no id may change spelling — this snapshot
is the gate.  It unions every id the three in-repo surfaces name (the spoken
vocabulary, the AHK script, the reference) plus the handful only a Funestra HUD
or console posts, and holds the result to one literal list.

The act-filter ids (``filter_<scope>_<act>``) come from the content overlay,
which differs per machine, so they are pinned by shape against
:mod:`fun_time.filter_vocab` rather than by literal act names.  The numeric
families (``robot_hand_amp_50``, ``main_player_speed_150``, ...) are pinned by regenerating
them the way the vocabulary does.
"""
from __future__ import annotations

import re
from pathlib import Path

from fun_time.command_reference import build_reference_sections
from fun_time.filter_vocab import load_acts, set_command
from fun_time.voice_commands import VOICE_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Commands no spoken phrase, hotkey or reference row names: posted straight off
# a Funestra's own surface as a literal string in that Funestra's repo, or off the
# dashboard's strip.  Each satellite's Kino / Origenerator pair is on its HUD, and
# the strip's two buttons are the strip's own (the hosted Origenerator posts its
# second one too, to be shown).  The three
# minimize buttons live on the HUDs (funestra / the Main Funestra's console); the
# padlock on that console locks whichever Funestra is on the main screen; the speed
# pair is Genau's console's own ± marks beside its drive readout; the enhanced
# filter is the switch the console draws over a hosted Origenerator's slideshows,
# which the room hears as "enhanced only" rather than under this id; the OSR2's
# control-off is the console's switch off, which the room says as park.  The two
# scene jumps are the VR controllers' A and B with the
# trigger held.
HUD_ONLY_COMMAND_IDS = (
    "genau_filter_enhanced",
    "landscape_kino_activate",
    "landscape_minimize",
    "landscape_origenerator_activate",
    "landscape_take_osr2",
    "main_lock",
    "main_minimize",
    "main_scene_next",
    "main_scene_prev",
    "main_take_osr2",
    "origenerator_activate",
    "osr2_control_off",
    "portrait_kino_activate",
    "portrait_minimize",
    "portrait_origenerator_activate",
    "portrait_take_osr2",
    "rfb_activate",
    "robot_hand_speed_down",
    "robot_hand_speed_up",
)

# The argument-carrying forms, matched by prefix rather than listed whole: the
# HUD thumbnail clicks and the Main Funestra's volume slider carry their payload after a "|".
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
    "active_hud_lower_left",
    "active_hud_lower_right",
    "active_hud_minimize",
    "active_hud_restore",
    "active_hud_upper_left",
    "active_hud_upper_right",
    "active_latest",
    "active_lock_action",
    "active_lock_off",
    "active_lock_on",
    "active_more_seeds",
    "active_mute",
    "active_next",
    "active_no_filter",
    "active_no_loop",
    "active_nudge_next",
    "active_nudge_prev",
    "active_prev",
    "active_reset",
    "active_seed_loop",
    "active_shuffle",
    "active_speed_down",
    "active_speed_max",
    "active_speed_min",
    "active_speed_up",
    "active_trash",
    "active_unmute",
    "active_volume_down",
    "active_volume_up",
    "active_wrong_action",
    "all_reset",
    "audio_mute",
    "audio_unmute",
    "audio_volume_down",
    "audio_volume_up",
    "broker_panel",
    "broker_start",
    "broker_stop",
    "browse_library",
    "enter_omnipause",
    "enter_vr",
    "exit_vr",
    "fmode_off",
    "fmode_on",
    "fmode_toggle",
    "genau_activate",
    "genau_flick_seconds_down",
    "genau_flick_seconds_up",
    "genau_flip_ends",
    "genau_next_flick",
    "genau_prev_flick",
    "genau_weird_flick",
    "genaumacher_save",
    "help_reference",
    "help_reference_close",
    "kino_activate",
    "landscape_action_loop",
    "landscape_cycle_action",
    "landscape_cycle_seed",
    "landscape_cycle_version",
    "landscape_cycle_version_back",
    "landscape_fmode",
    "landscape_fmode_off",
    "landscape_fmode_on",
    "landscape_hud_down",
    "landscape_hud_left",
    "landscape_hud_lower_left",
    "landscape_hud_lower_right",
    "landscape_hud_minimize",
    "landscape_hud_restore",
    "landscape_hud_right",
    "landscape_hud_up",
    "landscape_hud_upper_left",
    "landscape_hud_upper_right",
    "landscape_latest",
    "landscape_lock",
    "landscape_lock_action",
    "landscape_lock_off",
    "landscape_lock_on",
    "landscape_loop",
    "landscape_more_seeds",
    "landscape_mute",
    "landscape_nav_down",
    "landscape_nav_up",
    "landscape_next",
    "landscape_no_filter",
    "landscape_no_loop",
    "landscape_nudge_next",
    "landscape_nudge_prev",
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
    "landscape_say_genau",
    "landscape_say_go_now",
    "landscape_say_pause_slideshow",
    "landscape_say_play_slideshow",
    "landscape_say_requests",
    "landscape_say_start_slideshow",
    "landscape_say_stop_slideshow",
    "landscape_seed_loop",
    "landscape_shuffle",
    "landscape_speed_down",
    "landscape_speed_max",
    "landscape_speed_min",
    "landscape_speed_up",
    "landscape_trash",
    "landscape_unmute",
    "landscape_volume_down",
    "landscape_volume_up",
    "landscape_wrong_action",
    "main_crown",
    "main_fmode",
    "main_fmode_off",
    "main_fmode_on",
    "main_hud_down",
    "main_hud_left",
    "main_hud_lower_left",
    "main_hud_lower_right",
    "main_hud_minimize",
    "main_hud_restore",
    "main_hud_right",
    "main_hud_up",
    "main_hud_upper_left",
    "main_hud_upper_right",
    "main_kino_activate",
    "main_latest",
    "main_lock_off",
    "main_lock_on",
    "main_next",
    "main_nudge_next",
    "main_nudge_prev",
    "main_player_clip_jump",
    "main_player_compilation",
    "main_player_cycle_version",
    "main_player_cycle_version_back",
    "main_player_end_compilation",
    "main_player_full_vid",
    "main_player_funscript_jump",
    "main_player_length_clips",
    "main_player_length_full",
    "main_player_length_mixed",
    "main_player_length_none",
    "main_player_loop_cancel",
    "main_player_next_funscripted",
    "main_player_record_down",
    "main_player_record_tap",
    "main_player_record_up",
    "main_player_speed_down",
    "main_player_speed_max",
    "main_player_speed_min",
    "main_player_speed_up",
    "main_prev",
    "main_projection_both",
    "main_projection_flat",
    "main_projection_none",
    "main_projection_vr",
    "main_reset",
    "main_shuffle",
    "omnipause_toggle",
    "pause",
    "play",
    "portrait_action_loop",
    "portrait_crown",
    "portrait_cycle_action",
    "portrait_cycle_seed",
    "portrait_cycle_version",
    "portrait_cycle_version_back",
    "portrait_fmode",
    "portrait_fmode_off",
    "portrait_fmode_on",
    "portrait_hud_down",
    "portrait_hud_left",
    "portrait_hud_lower_left",
    "portrait_hud_lower_right",
    "portrait_hud_minimize",
    "portrait_hud_restore",
    "portrait_hud_right",
    "portrait_hud_up",
    "portrait_hud_upper_left",
    "portrait_hud_upper_right",
    "portrait_latest",
    "portrait_lock",
    "portrait_lock_action",
    "portrait_lock_off",
    "portrait_lock_on",
    "portrait_loop",
    "portrait_more_seeds",
    "portrait_mute",
    "portrait_nav_down",
    "portrait_nav_up",
    "portrait_next",
    "portrait_no_filter",
    "portrait_no_loop",
    "portrait_nudge_next",
    "portrait_nudge_prev",
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
    "portrait_say_genau",
    "portrait_say_go_now",
    "portrait_say_pause_slideshow",
    "portrait_say_play_slideshow",
    "portrait_say_requests",
    "portrait_say_start_slideshow",
    "portrait_say_stop_slideshow",
    "portrait_seed_loop",
    "portrait_shuffle",
    "portrait_speed_down",
    "portrait_speed_max",
    "portrait_speed_min",
    "portrait_speed_up",
    "portrait_trash",
    "portrait_unmute",
    "portrait_volume_down",
    "portrait_volume_up",
    "portrait_wrong_action",
    "projection_cycle",
    "projection_cycle_back",
    "quarter_button",
    "quit",
    "recenter_view",
    "relief_omnipause",
    "robot_hand_amplitude_down",
    "robot_hand_amplitude_up",
    "robot_hand_center_down",
    "robot_hand_center_up",
    "robot_hand_cycle_shape",
    "robot_hand_cycle_shape_prev",
    "robot_hand_learned_off",
    "robot_hand_learned_on",
    "robot_hand_park",
    "robot_hand_release",
    "robot_hand_toggle_learned",
    "satellites_action_loop",
    "satellites_cycle_action",
    "satellites_cycle_seed",
    "satellites_cycle_version",
    "satellites_fmode",
    "satellites_fmode_off",
    "satellites_fmode_on",
    "satellites_hud_lower_left",
    "satellites_hud_lower_right",
    "satellites_hud_minimize",
    "satellites_hud_restore",
    "satellites_hud_upper_left",
    "satellites_hud_upper_right",
    "satellites_kino_activate",
    "satellites_latest",
    "satellites_lock_action",
    "satellites_lock_off",
    "satellites_lock_on",
    "satellites_more_seeds",
    "satellites_mute",
    "satellites_next",
    "satellites_no_filter",
    "satellites_no_loop",
    "satellites_nudge_next",
    "satellites_nudge_prev",
    "satellites_origenerator_activate",
    "satellites_prev",
    "satellites_reset",
    "satellites_seed_loop",
    "satellites_shuffle",
    "satellites_speed_down",
    "satellites_speed_max",
    "satellites_speed_min",
    "satellites_speed_up",
    "satellites_trash",
    "satellites_unmute",
    "satellites_volume_down",
    "satellites_volume_up",
    "satellites_wrong_action",
    "speed_down",
    "speed_up",
    "tilt_down",
    "tilt_reset",
    "tilt_up",
    "voice_accept",
    "voice_off",
    "voice_toggle",
    "vr_reset",
)


def _expected_numeric_ids() -> set[str]:
    """The generated numeric families, rebuilt the way the vocabulary builds them."""
    ids = {
        f"robot_hand_{axis}_{value}"
        for axis in ("amp", "center", "speed")
        for value in range(0, 101, 10)
    }
    ids |= {f"genau_flick_seconds_{value}" for value in range(1, 61)}
    ids |= {
        f"{funestra}_speed_{pct}"
        for funestra in ("main_player", "active", "satellites", "portrait", "landscape")
        for pct in (25, 50, 75, 100, 125, 150, 175, 200)
    }
    return ids


def _expected_filter_ids() -> set[str]:
    """The act-filter ids, shaped from whatever overlay this machine carries."""
    return {
        set_command(scope, query)
        for scope in ("satellites", "portrait", "landscape")
        for query in load_acts()
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
    rest = EXPECTED_COMMAND_IDS[len(HUD_ONLY_COMMAND_IDS):]
    assert list(rest) == sorted(set(rest))
    assert list(HUD_ONLY_COMMAND_IDS) == sorted(set(HUD_ONLY_COMMAND_IDS))
    assert len(set(EXPECTED_COMMAND_IDS)) == len(EXPECTED_COMMAND_IDS)


def test_the_prefixed_forms_keep_their_spellings():
    """The payload-carrying prefixes are cross-repo surface too: the satellite
    HUDs build ``<side>_play_video|<path>`` / ``<side>_lock_video|<path>`` and
    The Main Funestra's slider builds ``audio_set_volume|<level>`` in their own repos."""
    assert PREFIXED_COMMAND_FORMS == (
        "audio_set_volume|",
        "landscape_lock_video|",
        "landscape_play_video|",
        "portrait_lock_video|",
        "portrait_play_video|",
    )
