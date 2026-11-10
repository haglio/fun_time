from __future__ import annotations

import re
import subprocess
import sys

from shared_ui.reference import (
    ACTION_COLUMN_AT_LEAST_PX,
    CONTROLLER_COLUMN_PX,
    CONTROLLER_HEADER,
    HOTKEY_BLOCK_PX,
    HOTKEY_HEADER,
    VOICE_COLUMN_PX,
    VOICE_HEADER,
    CommandRef,
    ReferenceGroup,
    ReferenceSection,
    narrowest_page_px,
)
from shared_ui.reference import BORDER as _BORDER

from fun_time.command_reference import (
    _SECTIONS,
    A_OR_X,
    ANGLE_BAR,
    B_OR_Y,
    HOLD_TRIGGER_AND_A_OR_X,
    HOLD_TRIGGER_AND_B_OR_Y,
    HOLD_TRIGGER_AND_MOVE,
    HOLD_TRIGGER_AND_STICK_UP_DOWN,
    STICK_SIDEWAYS,
    STICK_UP_DOWN,
    TRIGGER,
    _collapse_scopes,
    _display_voice,
    build_reference_sections,
    flip_row,
    reference_details,
    render_reference_html,
)
from fun_time.command_value import parse_command
from fun_time.content import Noun, genau_flick_noun
from fun_time.filter_vocab import display_forms, filter_voice_commands
from fun_time.genau_commands import about_genaus_flick
from fun_time.main_funestra_commands import MAIN_FUNESTRA_CMD_MAP
from fun_time.voice_commands import (
    GENAU_SAID,
    RELIEF_SAID_IN_A_HURRY,
    VOICE_COMMANDS,
    build_voice_commands,
    friendly_voice,
)
from fun_time.voice_control import SPOKEN_FOR_THE_KEYS_A_PAUSE_LEAVES_LIVE, SUSPEND_EXEMPT_COMMANDS
from tests.ahk_script import script_text, suspend_exempt_block, suspend_exempt_keys

_NUMERIC_RE = re.compile(r"^(robot_hand_(amp|center|speed)|genau_flick_seconds)_\d+$")
_AN_UNFILLED_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_QUEUED_RE = re.compile(r'QueueCommand\("([^"]+)"\)')


def _ahk_hotkey_commands() -> set[str]:
    return set(_QUEUED_RE.findall(script_text()))


def _ahk_bindings_for(command: str) -> list[str]:
    return re.findall(rf'^(\S+)::QueueCommand\("{re.escape(command)}"\)', script_text(), re.MULTILINE)


def _ahk_binding_for(command: str) -> str | None:
    bound = _ahk_bindings_for(command)
    return bound[0] if bound else None


def _ahk_suspend_exempt_commands() -> set[str]:
    return set(_QUEUED_RE.findall(suspend_exempt_block()))


def _keys(row: CommandRef) -> tuple[str, ...]:
    return tuple(key for column in row.key_columns for key in column)


def _all_rows() -> list[CommandRef]:
    return [row for section in build_reference_sections() for row in section.rows]


def _covered_commands() -> set[str]:
    return {cmd for row in _all_rows() for cmd in row.commands}


def test_sections_are_non_empty_and_well_formed():
    sections = build_reference_sections()
    assert sections, "reference must have at least one section"
    for section in sections:
        assert isinstance(section, ReferenceSection)
        assert section.title
        assert section.rows, f"section {section.title!r} has no rows"
        for row in section.rows:
            assert row.description
            assert _keys(row) or row.voice or row.controller, f"row {row.description!r} has no trigger"


def test_every_voice_command_is_represented():
    covered = _covered_commands()
    missing = {
        cmd
        for cmd in VOICE_COMMANDS.values()
        if not _NUMERIC_RE.match(cmd) and cmd not in covered
    }
    assert not missing, f"voice commands missing from reference: {sorted(missing)}"


def test_numeric_voice_pattern_is_documented():
    voice_blobs = [" ".join(row.voice) for row in _all_rows()]
    assert any("amp" in blob and "0" in blob and "100" in blob for blob in voice_blobs), (
        "expected a row describing the spoken amp/center/speed 0-100 pattern"
    )


def test_every_ahk_hotkey_command_is_represented_with_a_hotkey():
    rows = _all_rows()
    for cmd in _ahk_hotkey_commands():
        owning = [row for row in rows if cmd in row.commands]
        assert owning, f"AHK hotkey command {cmd!r} is missing from the reference"
        assert any(_keys(row) for row in owning), (
            f"AHK hotkey command {cmd!r} is shown without a hotkey label"
        )


def test_voice_toggle_is_not_key_bound():
    assert "voice_toggle" not in _ahk_hotkey_commands()
    owning = [row for row in _all_rows() if "voice_toggle" in row.commands]
    assert owning, "voice_toggle must still be documented"
    for row in owning:
        assert _keys(row) == (), f"{row.description!r} must show no hotkey"


def test_either_enter_key_accepts_what_voice_was_not_sure_of():
    bound = re.findall(r'^(\S+)::QueueCommand\("voice_accept"\)', script_text(), re.MULTILINE)
    [row] = [r for r in _all_rows() if "voice_accept" in r.commands]

    assert sorted(bound) == ["Enter", "NumpadEnter"]
    assert _keys(row) == ("Enter",)
    assert "voice_accept" not in _ahk_suspend_exempt_commands()


def test_the_vr_view_is_spoken_only_since_a_keyboard_is_no_use_in_a_headset():
    for cmd in ("recenter_view", "tilt_up", "tilt_down", "tilt_reset", "vr_reset"):
        assert _ahk_binding_for(cmd) is None, cmd
        assert cmd in _covered_commands()
    for row in _section("VR").rows:
        assert _keys(row) == (), row.description


def test_cycle_action_and_seed_are_spoken_only():
    expected = {
        "portrait_cycle_action": ("portrait action", "action"),
        "portrait_cycle_seed": ("portrait seed", "seed"),
        "landscape_cycle_action": ("landscape action", "action"),
        "landscape_cycle_seed": ("landscape seed", "seed"),
    }
    queued = _ahk_hotkey_commands()
    for cmd, (phrase, shown) in expected.items():
        assert VOICE_COMMANDS[phrase] == cmd
        assert cmd not in queued, f"{cmd} is spoken-only and must hold no key"
        row = _the_row_of(cmd)
        assert _keys(row) == ()
        assert shown in row.voice


def test_the_group_loops_are_cycled_by_one_key_per_side():
    expected = {"portrait_loop": ("Home", 0), "landscape_loop": ("E", 1)}
    for cmd, (key, column) in expected.items():
        bound = _ahk_binding_for(cmd)
        assert bound is not None, f"{cmd} must be bound in the AHK hotkey script"
        assert bound.lower() == key.lower(), f"{cmd} is on {bound!r}, expected {key!r}"
        assert _the_row_of(cmd).key_columns[column] == (key,)


def test_ending_a_loop_outright_is_spoken_and_holds_no_key():
    assert VOICE_COMMANDS["portrait end loop"] == "portrait_no_loop"
    for cmd in ("portrait_no_loop", "landscape_no_loop"):
        assert _ahk_binding_for(cmd) is None, f"{cmd} gave its key to the cycle"
        assert _keys(_the_row_of(cmd)) == ()


def test_the_script_spells_letter_keys_lowercase_and_the_reference_uppercase():
    assert _ahk_binding_for("landscape_loop") == "e"
    assert _the_row_of("landscape_loop").key_columns[1] == ("E",)


def _section(title: str) -> ReferenceSection:
    return {s.title: s for s in build_reference_sections()}[title]


def _group(section_title: str, group_title: str) -> ReferenceGroup:
    return {group.title: group for group in _section(section_title).groups}[group_title]


def _funestras_section() -> ReferenceSection:
    return _section("Funestras")


def _shared_sections() -> tuple[ReferenceSection, ...]:
    return (_funestras_section(), _section("Origenerator"))


def _the_row_of(cmd: str) -> CommandRef:
    [row] = [row for row in _all_rows() if cmd in row.commands]
    return row


def _the_set_line_directly_follows_its_nudge(rows, nudge: str, set_line: str) -> bool:
    names = [row.description for row in rows]
    return names.index(set_line) == names.index(nudge) + 1


def test_every_scope_shares_one_grid_with_a_key_column_per_funestra():
    assert _funestras_section().key_headers == ("Portrait", "Landscape", "Main")
    for section in _shared_sections():
        for row in section.rows:
            if row in _group("Funestras", "Other").rows:
                continue
            if "portrait_say_genau" in row.commands:
                continue
            by_scope = {
                scope: {c[len(scope) + 1:] for c in row.commands if c.startswith(f"{scope}_")}
                for scope in ("portrait", "landscape", "satellites", "active")
            }
            assert by_scope["portrait"] == by_scope["landscape"], (
                f"row {row.description!r} names different actions per side"
            )
            for scope, actions in by_scope.items():
                for action in by_scope["portrait"] | actions:
                    command = f"{scope}_{action}"
                    if command in VOICE_COMMANDS.values():
                        assert command in row.commands, (
                            f"row {row.description!r} omits {command}"
                        )
            assert len(row.key_columns) == len(section.key_headers)


def test_the_shared_grid_drops_the_scope_word_from_the_say_column():
    section = _funestras_section()
    nxt = next(r for r in section.rows if "portrait_next" in r.commands)
    assert nxt.voice[:2] == ("next", "next video")
    assert nxt.key_columns == (("Right",), ("D",), (".",))
    lock = next(r for r in section.rows if "portrait_lock_on" in r.commands)
    flick = genau_flick_noun().one
    assert lock.voice == ("lock", "lock video", f"lock {flick}", "unlock", "unlock video",
                          f"unlock {flick}")
    assert "main_lock_on" in lock.commands
    sayable_bare = {friendly_voice(phrase) for phrase in VOICE_COMMANDS}
    for section in _shared_sections():
        for word in ("portrait", "landscape", "satellites"):
            assert word in section.note
        for row in section.rows:
            if row.description.startswith(("filter by action", "drop ")):
                continue
            for phrase in row.voice:
                if _collapse_scopes(phrase) not in sayable_bare:
                    continue
                assert not {"portrait", "landscape", "satellites", "main"} & set(phrase.split()), (
                    f"{phrase!r} still carries a scope word"
                )


def test_the_grid_carries_every_satellites_and_active_command():
    covered = {c for section in _shared_sections() for row in section.rows for c in row.commands}
    assert {
        "satellites_prev", "satellites_next", "satellites_trash",
        "satellites_lock_on", "satellites_lock_off",
        "satellites_cycle_action", "satellites_cycle_seed", "satellites_more_seeds", "satellites_wrong_action",
        "satellites_action_loop", "satellites_seed_loop", "satellites_no_loop", "satellites_lock_action",
        "satellites_latest", "satellites_shuffle", "satellites_no_filter", "satellites_reset",
        "satellites_fmode", "satellites_fmode_on", "satellites_fmode_off",
    } <= covered
    assert {
        "active_prev", "active_next", "active_trash",
        "active_lock_on", "active_lock_off", "active_wrong_action",
        "active_cycle_action", "active_cycle_seed", "active_more_seeds",
        "active_action_loop", "active_seed_loop", "active_no_loop",
        "active_lock_action", "active_latest", "active_shuffle",
        "active_no_filter", "active_reset",
        "active_fmode", "active_fmode_on", "active_fmode_off",
    } <= covered


def test_the_bare_phrases_read_as_the_say_column():
    rows = [row for section in _shared_sections() for row in section.rows]
    by_command = {cmd: r for r in rows for cmd in r.commands}
    expected = {
        "active_lock_on": "lock",
        "active_lock_off": "unlock",
        "active_prev": "previous",
        "active_next": "next",
        "active_trash": "trash",
        "active_cycle_action": "action",
        "active_cycle_seed": "seed",
        "active_wrong_action": "wrong action",
        "active_fmode": "f mode",
    }
    for cmd, phrase in expected.items():
        assert cmd in by_command, f"{cmd} missing from the satellite grid"
        assert phrase in by_command[cmd].voice


def test_a_sides_favorites_are_an_alias_on_the_f_mode_row():
    f_mode = next(r for r in _funestras_section().rows if "active_fmode" in r.commands)

    assert f_mode.voice == ("f mode", "f mode on", "f mode off", "funscripts", "favorites")
    assert {"portrait_say_favorites", "landscape_say_favorites"} <= set(f_mode.commands)
    assert not [r for r in _all_rows() if r.description == "favorites"]
    assert "“favorites”" in f_mode.detail
    assert "“funscripts”" in f_mode.detail
    assert VOICE_COMMANDS["fun scripts"] == VOICE_COMMANDS["main fun scripts"] == "main_fmode_on"


def test_the_shared_grid_carries_every_funestras_own_playback_speed():
    rows = _funestras_section().rows
    nudge = next(r for r in rows if "portrait_speed_up" in r.commands)
    rate = next(r for r in rows if "portrait_speed_50" in r.commands)

    assert nudge.voice == ("playback speed up", "playback slow down", "playback speed down")
    assert {"active_speed_up", "satellites_speed_down", "landscape_speed_up",
            "main_funestra_speed_up", "main_funestra_speed_down"} <= set(nudge.commands)
    assert {"active_speed_min", "satellites_speed_max", "active_speed_150", "satellites_speed_200",
            "landscape_speed_25", "main_funestra_speed_50"} <= set(rate.commands)


def test_the_reference_calls_the_mode_kino_and_never_video():
    rows = _all_rows()
    said = [(row.description, phrase) for row in rows for phrase in (row.description, *row.voice)
            if "video mode" in phrase.lower()]

    assert said == []
    assert [row.description for row in rows if "kino_activate" in row.commands] == ["Kino mode"]


def test_no_say_column_leaks_a_sound_alike_and_the_flick_is_stepped_by_its_word():
    rows = _all_rows()
    genau_next_row = next(r for r in rows if "genau_next_flick" in r.commands)
    assert f"next {genau_flick_noun().one}" in genau_next_row.voice
    assert not {"genau next", "next genau"} & set(genau_next_row.voice)
    for row in rows:
        for phrase in row.voice:
            assert "now mode" not in phrase, phrase
            assert "go now" not in phrase, phrase
            assert "aura generator" not in phrase, phrase


def test_no_row_tells_you_to_say_genau_by_its_sound_alike():
    assert not [row.description for row in _all_rows() if '"go now' in row.description]


def test_the_main_funestra_shares_the_navigation_rows_under_its_own_key_column():
    rows = _funestras_section().rows
    prev = next(r for r in rows if "main_prev" in r.commands)
    nxt = next(r for r in rows if "main_next" in r.commands)
    flick = genau_flick_noun().one
    assert prev.voice == ("previous", "previous video", f"previous {flick}")
    assert nxt.voice == ("next", "next video", f"next {flick}")
    assert (prev.key_columns[2], nxt.key_columns[2]) == (("M",), (".",))
    assert "“next main”" in _funestras_section().note


def test_the_navigation_rows_are_the_steps_the_nudges_the_scenes_then_what_is_done_to_the_clip():
    navigation = _group("Funestras", "Navigation")
    back, forward = navigation.rows[2], navigation.rows[3]

    assert [row.description for row in navigation.rows] == [
        "previous", "next", "nudge back 10 seconds", "nudge forward 10 seconds",
        "previous scene in the video", "next scene in the video", "trash", "lock / unlock",
        "cycle enhancement level", "clip navigation", "jump to funscript", "next funscripted",
        "navigate the map", "cycle loop mode"]
    assert (back.voice, forward.voice) == (("back",), ("skip",))
    assert (back.detail, forward.detail) == ("", "")
    assert (back.key_columns, forward.key_columns) == (((), (), ("-",)), ((), (), ("+",)))
    assert {"portrait_nudge_prev", "satellites_nudge_prev", "active_nudge_prev", "main_nudge_prev"
            } <= set(back.commands)
    assert not [row for row in _all_rows() if row.description.startswith("skip / back")]
    assert not [row for row in _section("Main Funestra").rows if "nudge" in row.description]


def test_the_scene_jumps_are_the_headsets_buttons_with_the_trigger_held():
    previous, following = _the_row_of("main_scene_prev"), _the_row_of("main_scene_next")

    assert (previous.controller, following.controller) == (
        (HOLD_TRIGGER_AND_B_OR_Y,), (HOLD_TRIGGER_AND_A_OR_X,))
    assert _keys(previous) == _keys(following) == ()
    assert previous.voice == following.voice == ()
    assert previous.detail == following.detail == ""
    assert (HOLD_TRIGGER_AND_B_OR_Y, HOLD_TRIGGER_AND_A_OR_X) == (
        "hold trigger + B / Y", "hold trigger + A / X")


def test_genaus_flick_steps_and_trash_ride_the_shared_rows_and_k_sits_under_main():
    trash = _the_row_of("genau_trash_flick")

    assert trash.description == "trash"
    assert trash.key_columns == (("Up",), ("W",), ("K",))
    assert trash.voice == ("trash", "trash video")
    assert trash.detail == (
        "in Kino mode it sends the video to Trash and moves on, or unfavorites it instead "
        "when it has been previously favorited; in Origenerator mode it deletes the "
        "image or video and moves on")
    assert _the_row_of("genau_prev_flick").description == "previous"
    assert _the_row_of("genau_next_flick").description == "next"


def test_one_row_steps_every_funestra_through_the_enhancement_levels():
    versions = _the_row_of("satellites_cycle_version")

    assert versions == _the_row_of("main_funestra_cycle_version_back")
    assert versions.description == "cycle enhancement level"
    assert versions.detail == "the upscale, the original it was made from, or an enhancement"
    assert versions.voice == ("version", "change version", "cycle version", "next version")
    assert versions.key_columns == (("Shift+Left", "Shift+Right"), ("Shift+A", "Shift+D"),
                                    ("Shift+M", "Shift+."))
    assert not [row for row in _all_rows() if row.description.startswith("step through")]


def test_the_playback_nudge_is_its_own_spoken_row_ahead_of_the_absolute_sets():
    rows = _funestras_section().rows
    nudge = next(r for r in rows if "main_funestra_speed_up" in r.commands)
    assert _keys(nudge) == ()
    assert nudge.voice == ("playback speed up", "playback slow down", "playback speed down")
    assert _the_set_line_directly_follows_its_nudge(rows, nudge.description, "set playback speed")


def test_the_speed_keys_ride_the_bare_nudge_the_osr2s_driver_answers():
    assert [_ahk_binding_for(cmd) for cmd in ("speed_up", "speed_down")] == ["l", "j"]
    assert _the_row_of("speed_up").key_columns == (("L", "J"),)
    assert _keys(_the_row_of("main_funestra_speed_up")) == ()


def test_the_quit_row_no_longer_offers_the_exit_synonym():
    rows = _all_rows()
    quit_rows = [r for r in rows if "quit" in r.commands]
    assert quit_rows
    assert set(quit_rows[0].voice) == {"quit"}


def test_reference_popup_row_shows_toggle_and_close_names():
    rows = _all_rows()
    help_rows = [r for r in rows if "help_reference" in r.commands]
    assert len(help_rows) == 1, "expected exactly one reference-popup row"
    row = help_rows[0]
    assert "help_reference_close" in row.commands
    voice = set(row.voice)
    assert {"help", "hotkeys", "reference", "voice commands"} <= voice
    assert {"close help", "close hotkeys", "close reference", "close voice commands"} <= voice
    assert "hot keys" not in voice


def test_sound_rows_are_voice_only_and_reach_every_funestra():
    rows = _funestras_section().rows
    mute = next(r for r in rows if "audio_mute" in r.commands)
    assert _keys(mute) == ()
    assert mute.voice == ("mute", "unmute")
    assert {"audio_unmute", "portrait_mute", "landscape_unmute", "satellites_mute",
            "active_unmute"} <= set(mute.commands)

    steps = [r for r in _all_rows() if "audio_volume_up" in r.commands]
    assert steps == [r for r in rows if "audio_volume_up" in r.commands]
    assert len(steps) == 1, "expected one volume row"
    assert {"audio_volume_down", "portrait_volume_up", "landscape_volume_down",
            "satellites_volume_up", "active_volume_down"} <= set(steps[0].commands)
    assert _keys(steps[0]) == ()


def test_neither_word_of_a_volume_pair_is_shown_as_the_real_one():
    assert set(_the_row_of("audio_volume_up").voice) == {"quiet", "quieter", "loud", "louder"}


def test_no_say_column_leaks_the_raw_un_mute_form():
    for row in _all_rows():
        for phrase in row.voice:
            assert "un mute" not in phrase, phrase


def test_latest_is_spoken_only_and_the_older_names_are_gone():
    [row] = [r for r in _all_rows() if "satellites_latest" in r.commands]
    assert _keys(row) == ()
    assert row.voice == ("latest",)
    assert {"portrait_latest", "landscape_latest", "active_latest", "main_latest"} <= set(row.commands)
    assert "premiere" not in VOICE_COMMANDS
    assert "recents" not in VOICE_COMMANDS


def test_end_loop_follows_the_funestra_last_spoken_to():
    assert VOICE_COMMANDS["end loop"] == "active_no_loop"
    assert VOICE_COMMANDS["portrait end loop"] == "portrait_no_loop"
    assert VOICE_COMMANDS["end loop landscape"] == "landscape_no_loop"
    assert VOICE_COMMANDS["satellites end loop"] == "satellites_no_loop"


def test_latest_and_shuffle_reach_one_side_or_both():
    for word, action in (("latest", "latest"), ("shuffle", "shuffle")):
        assert VOICE_COMMANDS[word] == f"active_{action}"
        assert VOICE_COMMANDS[f"portrait {word}"] == f"portrait_{action}"
        assert VOICE_COMMANDS[f"{word} landscape"] == f"landscape_{action}"
        assert VOICE_COMMANDS[f"satellites {word}"] == f"satellites_{action}"


def test_voice_phrases_are_derived_from_voice_commands():
    inverse: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        inverse.setdefault(cmd, []).append(phrase)
    for section in _SECTIONS:
        for row in (row for group in section.groups for row in group.rows):
            if row.voice_display is not None:
                continue
            built = _display_voice(row, merge_scopes=section.merge_scopes)
            derived = sorted(friendly_voice(p) for cmd in row.commands for p in inverse.get(cmd, []))
            if section.merge_scopes:
                derived = [_collapse_scopes(p) for p in derived]
            for phrase in derived:
                assert phrase in built, (
                    f"row {row.description!r} should list derived phrase {phrase!r}"
                )


def test_the_genau_row_shows_each_phrase_once_though_the_recognizer_hears_two_spellings():
    assert VOICE_COMMANDS["genau"] == VOICE_COMMANDS["go now"] == "genau_activate"
    genau_rows = [r for r in _all_rows() if "genau_activate" in r.commands]
    assert genau_rows
    assert genau_rows[0].voice == ("genau", "genau mode")


def test_the_reference_names_genaus_flicks_in_the_overlays_word():
    flick = genau_flick_noun()
    genau = _section("Genau")
    seconds = next(row for row in genau.rows if "genau_flick_seconds_up" in row.commands)

    assert [row.description for row in genau.rows] == [
        f"flip the Genau {flick.one} half a loop",
        f"seconds a {flick.one} holds the screen before Genau moves on"]
    assert seconds.voice == (f"{flick.one} seconds 1\u201360",)
    assert [row.description for row in genau.rows if re.search(r"\bclips?\b", row.description)] == []
    assert [row.description for row in _all_rows()
            if re.search(r"Genau(?:'s)? clips?\b", row.description)] == []


def test_genau_mode_row_lists_genau_phrase_and_no_key():
    rows = _all_rows()
    genau_rows = [r for r in rows if "genau_activate" in r.commands]
    assert genau_rows, "expected a row for genau_activate"
    row = genau_rows[0]
    assert "genau" in row.voice
    assert "genau mode" in row.voice
    assert "go now" not in row.voice
    assert _keys(row) == ()


def test_the_keys_nobody_reached_for_are_gone_and_what_only_they_reached_went_with_them():
    for key in ("g", "x", "v", "t", "p", "!Left", "!Right"):
        assert not re.search(rf"^{re.escape(key)}::", script_text(), re.MULTILINE), key
    retired = {"satellites_toggle", "main_funestra_toggle_length",
               "projection_narrower", "projection_wider"}
    assert not retired & _covered_commands()
    assert not retired & set(VOICE_COMMANDS.values())
    assert not retired & _ahk_hotkey_commands()
    modes = _group("Funestras", "Modes")
    assert [_keys(row) for row in modes.rows] == [(), (), ()]
    assert "X" not in _the_row_of("satellites_origenerator_activate").detail
    assert _keys(_the_row_of("main_funestra_length_clips")) == ()
    assert _keys(_the_row_of("projection_cycle")) == ()
    [angle] = [row for row in _all_rows() if row.description.startswith("narrow / widen")]
    assert (_keys(angle), angle.voice, angle.commands) == ((), (), ())


def test_the_sections_are_the_dashboard_vr_the_osr2_every_funestra_the_main_one_genau_and_origenerator():
    titles = [s.title for s in build_reference_sections()]
    assert titles == [
        "Dashboard",
        "VR",
        "OSR2",
        "Funestras",
        "Main Funestra",
        "Genau",
        "Origenerator",
    ]


def test_every_section_is_read_in_groups_of_a_few_rows_each():
    for section in build_reference_sections():
        assert section.groups, section.title
        assert all(group.rows for group in section.groups), section.title
        assert all(group.title for group in section.groups[1:]), section.title
        assert len(section.groups) > 1 or len(section.rows) <= 7, section.title
        assert section.rows == tuple(row for group in section.groups for row in group.rows)


def test_what_a_row_explains_is_its_detail_and_what_it_names_is_its_description():
    relief = next(row for row in _all_rows() if "relief_omnipause" in row.commands)
    quit_ = next(row for row in _all_rows() if "quit" in row.commands)

    assert relief.description == "relief OmniPause"
    assert relief.detail.startswith("OmniPause and retract together")
    assert quit_.description == "quit"
    assert quit_.detail == "close everything"
    for row in _all_rows():
        assert " — " not in row.description, row.description
        assert not row.description.endswith(":"), row.description


def test_the_genau_groups_rows_take_the_overlays_word():
    assert flip_row(Noun(one="bloop", many="bloops")).description.startswith(
        "flip the Genau bloop half a loop")



def test_genaus_flick_commands_are_the_genau_section_or_ride_a_shared_navigation_row():
    genau = _section("Genau")
    in_genau = {cmd for row in genau.rows for cmd in row.commands}
    elsewhere = {cmd for row in _all_rows() if row not in genau.rows for cmd in row.commands
                 if about_genaus_flick(parse_command(cmd))}

    assert in_genau
    assert all(about_genaus_flick(parse_command(cmd)) for cmd in in_genau)
    assert elsewhere == {"genau_prev_flick", "genau_next_flick", "genau_trash_flick"}
    assert all(_the_row_of(cmd) in _group("Funestras", "Navigation").rows for cmd in elsewhere)


def test_what_only_kino_answers_is_the_loops_group_or_rides_a_shared_navigation_row():
    versions = {"main_funestra_cycle_version", "main_funestra_cycle_version_back"}
    loops = _group("Main Funestra", "Loops")
    in_loops = {cmd for row in loops.rows for cmd in row.commands}
    navigation = {cmd for row in _group("Funestras", "Navigation").rows for cmd in row.commands}

    assert [row.description for row in loops.rows] == ["loop control", "save loop"]
    assert loops.rows[0].detail == (
        "hold to start recording a loop, release to end recording, and tap again to "
        "terminate a recorded loop")
    assert loops.rows[1].detail == (
        f"save loop to send to Genaumacher to produce a new Genau {genau_flick_noun().one}")
    assert set(MAIN_FUNESTRA_CMD_MAP) - versions <= in_loops | navigation
    assert {"main_funestra_compilation", "main_funestra_end_compilation", "main_funestra_full_vid",
            "main_funestra_clip_jump"} <= navigation
    assert "genaumacher_save" in in_loops
    assert versions <= set(_the_row_of("satellites_cycle_version").commands)
    assert not [row for row in _all_rows() if row.description == "save clip"]


def test_a_satellites_command_has_one_row_whichever_mode_the_side_is_in():
    for cmd in ("landscape_trash", "portrait_nav_up", "landscape_loop", "satellites_cycle_version"):
        assert _keys(_the_row_of(cmd)), cmd
    assert "in Origenerator mode" in _the_row_of("portrait_trash").detail
    assert "enhancement" in _the_row_of("satellites_cycle_version").detail


def test_the_main_funestras_step_and_hold_keys_reach_whatever_is_on_it():
    assert _ahk_binding_for("main_prev") == "m"
    assert _ahk_binding_for("main_next") == "SC034"
    assert _ahk_binding_for("main_lock") == "SC033"
    assert _ahk_bindings_for("main_nudge_next") == ["=", "+="]
    for retired in ("main_kino_activate", "browse_library", "robot_hand_toggle_learned",
                    "genau_lock", "main_funestra_lock"):
        assert _ahk_binding_for(retired) is None, retired
    keyed = {cmd: row.key_columns[2] for row in _funestras_section().rows
             for cmd in row.commands if row.key_columns[2]}
    assert keyed["main_prev"] == ("M",)
    assert keyed["main_next"] == (".",)
    assert keyed["main_lock"] == (",",)
    assert _keys(_the_row_of("main_nudge_prev")) == ("-",)
    assert _keys(_the_row_of("main_nudge_next")) == ("+",)


def test_the_shifted_and_ctrl_pairs_followed_m_and_the_period():
    assert _ahk_bindings_for("main_funestra_cycle_version_back") == ["+m"]
    assert _ahk_bindings_for("main_funestra_cycle_version") == ["+SC034"]
    assert _ahk_bindings_for("main_hud_left") == ["^m"]
    assert _ahk_bindings_for("main_hud_right") == ["^SC034"]
    assert _ahk_bindings_for("main_hud_up") == ["^k"]
    assert _ahk_bindings_for("main_hud_down") == ["^SC033"]
    assert not re.search(r"^[+^]?(\[|\]|SC01A|SC01B)::", script_text(), re.MULTILINE)
    assert "clockwise" not in script_text()
    assert _the_row_of("main_funestra_cycle_version_back").key_columns[2] == ("Shift+M", "Shift+.")
    assert [_the_row_of(f"main_hud_{direction}").key_columns[2] for direction in
            ("left", "right", "up", "down")] == [("Ctrl+M",), ("Ctrl+.",), ("Ctrl+K",), ("Ctrl+,",)]


def test_kino_mode_is_spoken_as_written_of_either_side_or_of_both():
    assert VOICE_COMMANDS["kino mode"] == "kino_activate"
    assert VOICE_COMMANDS["main kino mode"] == "main_kino_activate"
    assert VOICE_COMMANDS["satellite kino mode"] == "satellites_kino_activate"
    for retired in ("now now", "now mode", "hybrid", "hybrid mode", "player mode", "video mode"):
        assert retired not in VOICE_COMMANDS, retired
    [kino] = [r for r in _all_rows() if "kino_activate" in r.commands]
    assert {"main_kino_activate", "satellites_kino_activate"} <= set(kino.commands)
    assert kino.voice == ("kino", "kino mode")
    genau_rows = [r for r in _all_rows() if "genau_activate" in r.commands]
    assert genau_rows
    assert genau_rows[0].voice == ("genau", "genau mode")
    [origenerator] = [r for r in _all_rows() if "satellites_origenerator_activate" in r.commands]
    assert origenerator.voice == ("origenerator", "origenerator mode")


def test_loop_control_row_consolidates_record_and_cancel():
    rows = _all_rows()
    loop_rows = [r for r in rows if "main_funestra_record_down" in r.commands]
    assert loop_rows, "expected a loop control row"
    row = loop_rows[0]
    assert "R" in _keys(row)
    assert "record" in row.voice
    assert "loop" in row.voice
    assert "main_funestra_loop_cancel" in row.commands


def test_previous_shape_is_a_separate_keyless_line():
    rows = _all_rows()
    next_rows = [r for r in rows if "robot_hand_cycle_shape" in r.commands]
    prev_rows = [r for r in rows if "robot_hand_cycle_shape_prev" in r.commands]
    assert next_rows
    assert _keys(next_rows[0]) == ("I",)
    assert "robot_hand_cycle_shape_prev" not in next_rows[0].commands
    assert prev_rows
    assert _keys(prev_rows[0]) == ()
    assert "previous shape" in prev_rows[0].voice


def test_min_max_value_live_on_their_own_consecutive_set_lines():
    rows = _all_rows()
    amp_updown = next(r for r in rows if "robot_hand_amplitude_up" in r.commands)
    assert not any(("min" in v or "max" in v or "0\u2013100" in v) for v in amp_updown.voice)

    osr2_rows = _section("OSR2").rows
    for updown, setname in (
        ("amplitude up / down", "set amplitude"),
        ("center up / down", "set center"),
        ("speed up / down", "set speed"),
    ):
        assert _the_set_line_directly_follows_its_nudge(osr2_rows, updown, setname), setname
    set_amp = next(r for r in osr2_rows if r.description == "set amplitude")
    assert "min amp" in set_amp.voice
    assert "max amp" in set_amp.voice


def test_human_inspired_motion_follows_the_waveform_and_lists_its_voice_on_before_off():
    rows = _all_rows()
    waveform = next(i for i, r in enumerate(rows) if "robot_hand_cycle_shape" in r.commands)
    learned = rows[waveform + 1]
    assert "robot_hand_toggle_learned" in learned.commands
    assert learned.voice == ("human inspired", "HI on", "human inspired on", "HI off",
                             "human inspired off")
    assert _keys(learned) == ()


def test_previous_next_pairs_are_ordered_previous_then_next():
    for section in build_reference_sections():
        descs = [r.description for r in section.rows]
        for d in descs:
            if d.startswith("previous "):
                nxt = "next " + d[len("previous "):]
                if nxt in descs:
                    assert descs.index(d) < descs.index(nxt), f"{d!r} should precede {nxt!r}"


def test_the_clip_navigation_row_quotes_only_what_voice_answers():
    row = next(r for r in _all_rows() if "main_funestra_clip_jump" in r.commands)
    spoken = {phrase.lower() for phrase in VOICE_COMMANDS} | set(row.voice)

    quoted = set(re.findall(r'"([^"]+)"', row.detail))
    assert quoted
    assert quoted <= spoken


def test_corrected_descriptions():
    descs = {r.description for r in _all_rows()}
    assert {"disable voice control", "start / stop the Broker"} <= descs
    assert any(d.startswith("enable / disable human-inspired motion") for d in descs)
    assert "Mute voice control" not in descs
    assert "Broker start / stop" not in descs


_NAMES_AN_ACTION_MAY_OPEN_WITH = frozenset(
    {"OmniPause", "F-mode", "VR", "Kino", "Genau", "Origenerator", "OSR2", "HUD"})


def test_an_action_opens_lowercase_unless_it_opens_with_a_name():
    for row in _all_rows():
        first = row.description.split()[0]
        assert first[0].islower() or first in _NAMES_AN_ACTION_MAY_OPEN_WITH, row.description


def test_the_dashboard_section_lists_its_buttons_in_the_bars_order():
    dashboard = _section("Dashboard")

    assert [(group.title, [row.description for row in group.rows]) for group in dashboard.groups] == [
        ("", ["quit", "OmniPause / resume", "OmniPause", "relief OmniPause",
              "open / close this hotkeys & voice reference"]),
        ("Funestra", ["F-mode all", "reset all"]),
        ("Voice control", ["disable voice control",
                           "accept the last command voice was not sure enough of"]),
    ]
    assert dashboard.rows[-1].detail == ""


def test_the_vr_section_opens_with_the_crossing_and_ends_with_the_screens():
    vr = _section("VR")
    projection, angle, screens = vr.rows[4], vr.rows[5], vr.rows[6]

    assert [row.description for row in vr.rows] == [
        "enter / exit VR", "VR reset", "recenter",
        "tilt the VR screens up / down and level them again",
        "cycle the video's VR projection",
        "narrow / widen the angle a wrapped VR video spans",
        "move or resize a VR screen"]
    assert vr.rows[0].commands == ("enter_vr", "exit_vr")
    assert vr.rows[0].detail == ""
    assert projection.detail == ""
    assert projection.controller == (STICK_SIDEWAYS,)
    assert (angle.commands, angle.voice, _keys(angle)) == ((), (), ())
    assert angle.controller == (ANGLE_BAR,)
    assert ANGLE_BAR == "Angle bar"
    assert angle.detail == ("the bar labeled Angle beside the projection controls in the "
                            "headset, dragged with the trigger")
    assert screens.controller == (TRIGGER, STICK_UP_DOWN, HOLD_TRIGGER_AND_STICK_UP_DOWN)
    assert (HOLD_TRIGGER_AND_MOVE, HOLD_TRIGGER_AND_STICK_UP_DOWN) == (
        "hold trigger + move", "hold trigger + stick forward / back")


def test_the_modes_say_only_what_a_reader_cannot_see():
    genau, kino, origenerator = _group("Funestras", "Modes").rows

    assert genau.detail
    assert (kino.description, kino.detail) == ("Kino mode", "")
    assert (origenerator.description, origenerator.detail) == (
        "Origenerator mode",
        ("Satellite Funestras show Slideshows; Origenerator Core shares real estate with "
         "the Random Favs Browser"))


def test_the_osr2_rows_say_the_cruise_control_in_words_and_the_retired_quarter_cycle_is_gone():
    learned = _the_row_of("robot_hand_toggle_learned")

    assert "quarter_button" not in _covered_commands()
    assert not [r for r in _all_rows() if "\\" in _keys(r)]
    assert learned.detail == (
        "cruise control drawing from phrases extracted from funscripts for live-action scenes")


def test_the_osr2s_control_is_one_row_park_and_its_undoing_word():
    osr2 = _section("OSR2")
    [control] = osr2.groups[0].rows

    assert [group.title for group in osr2.groups] == ["", "Robot Hand", "Broker"]
    assert (control.description, control.voice) == (
        "control on / off", ("OSR2 control off", "park", "park it", "OSR2 control on", "unpark"))
    assert set(control.commands) == {"robot_hand_park", "robot_hand_release"}
    assert "osr2_control_off" not in control.commands
    assert _keys(control) == ()


def test_the_hud_moves_a_row_per_direction_by_key_then_to_a_corner_by_name():
    hud = _group("Funestras", "HUD")
    left, right, up, down, corner, minimize = hud.rows

    assert [row.description for row in hud.rows] == [
        "HUD left", "HUD right", "HUD up", "HUD down", "HUD to a corner", "minimize the HUD"]
    assert [row.key_columns for row in (left, right, up, down)] == [
        (("Ctrl+Left",), ("Ctrl+A",), ("Ctrl+M",)), (("Ctrl+Right",), ("Ctrl+D",), ("Ctrl+.",)),
        (("Ctrl+Up",), ("Ctrl+W",), ("Ctrl+K",)), (("Ctrl+Down",), ("Ctrl+S",), ("Ctrl+,",))]
    for row, direction in zip((left, right, up, down), ("left", "right", "up", "down"), strict=True):
        assert row.commands == (f"portrait_hud_{direction}", f"landscape_hud_{direction}",
                                f"main_hud_{direction}")
        assert (row.voice, row.detail, row.controller) == ((), "", ())
    assert corner.voice == ("hud top left", "hud upper left", "hud top right", "hud upper right",
                            "hud lower left", "hud bottom left", "hud lower right", "hud bottom right")
    assert "\"bottom\" the same as \"lower\"" in corner.detail
    assert "click" in corner.detail
    assert {"portrait_hud_lower_right", "landscape_hud_upper_right", "satellites_hud_lower_left",
            "active_hud_upper_right", "main_hud_lower_left"} <= set(corner.commands)
    assert (_keys(corner), corner.controller) == ((), (TRIGGER,))
    assert "main_hud_minimize" in minimize.commands


def test_the_origenerator_section_speaks_origenerators_own_words():
    origenerator = _section("Origenerator")

    assert [group.title for group in origenerator.groups] == [""]
    assert [row.description for row in origenerator.rows] == [
        "play the experiments or requests", "play, pause, or stop the slideshow",
        "fix the image on screen", f"animate the image as a Genau {genau_flick_noun().one}",
    ]
    assert origenerator.rows[0].detail == ""
    assert origenerator.rows[2].detail == "its face, hands, teeth, or eyes"
    assert not [row for row in _all_rows()
                if "show" in row.description.split() and row not in origenerator.rows]


def test_the_origenerator_column_folds_the_side_word_and_its_note_says_how_to_aim_a_phrase():
    origenerator = _section("Origenerator")
    flick = genau_flick_noun().one

    assert [row.voice for row in origenerator.rows] == [
        ("experiments", "requests"),
        ("play slideshow", "start slideshow", "pause slideshow", "stop slideshow"),
        ("fix face", "fix hands", "fix teeth", "fix eyes"),
        ("genau",),
    ]
    assert origenerator.note == (
        "Alone, a phrase reaches the satellite you last navigated.  Add “portrait”, "
        "“landscape”, or “satellites”, before it or after, to aim it, for example "
        "“portrait experiments” or “fix face landscape”."
    )
    assert all(row.commands == () for row in origenerator.rows)
    assert "name a Satellite" in origenerator.rows[3].detail
    heard = build_voice_commands(filter_commands={}, clip_jump_phrases=(), flick_flip_phrases=(),
                                 origenerator_phrases=("fix hands", *GENAU_SAID))
    assert heard["fix hands"] == "active_say_fix_hands"
    assert heard["fix hands portrait"] == "portrait_say_fix_hands"
    assert heard["satellites fix hands"] == "satellites_say_fix_hands"
    for not_a_phrase in (f"main {flick}", "main fix hands", "fix hands main"):
        assert not_a_phrase not in heard, not_a_phrase


def test_the_maps_rows_the_loops_the_filters_and_the_crown_are_the_funestras_last_groups():
    funestras = _funestras_section()
    cycle = _the_row_of("portrait_loop")
    crown = _the_row_of("main_crown")

    assert [group.title for group in funestras.groups] == [
        "Modes", "Navigation", "Playlist", "Included video types", "Sound", "HUD", "Playback",
        "Nav Map", "Loops", "Filter", "Other"]
    assert [row.description for row in _group("Funestras", "Nav Map").rows] == [
        "cycle action", "cycle seed", "wrong action"]
    assert cycle.detail == "seeds, then actions, then off"
    assert [row.description for row in _group("Funestras", "Loops").rows] == [
        "more seeds", "loop actions", "loop seeds", "stop looping"]
    assert [row.description for row in _filter_rows(_ACTIONS)] == [
        "filter by action", "filter to the action on screen", "drop all filters"]
    assert [row.description for row in _group("Funestras", "Filter").rows] == [
        "filter to the action on screen", "drop all filters"]
    assert _group("Funestras", "Other").rows == (crown,)
    assert crown.description == "crown the Main Funestra or the Portrait Funestra"
    assert crown.detail == (
        "the crowned Funestra takes up the majority of the (portrait-shaped) secondary "
        "monitor when it is displaying portrait-shaped content; the other Funestra then "
        "either shows landscape content or tiles its portrait content")
    assert crown.voice == ("crown main", "crown portrait")


def test_the_main_funestras_own_rows_are_what_it_browses_and_its_loops():
    main = _section("Main Funestra")

    assert [group.title for group in main.groups] == ["", "Loops"]
    assert [row.description for row in main.groups[0].rows] == ["open the browser"]


def test_the_included_lengths_shapes_and_enhancement_levels_are_said_as_what_they_include():
    included = _group("Funestras", "Included video types")
    lengths, shapes, orientations, levels = included.rows

    assert lengths.description == "included video types"
    assert lengths.voice == ("clips only", "full only", "clips and full", "full and clips")
    assert lengths.detail.startswith("the Main Funestra's alone")
    assert "in Kino mode" in lengths.detail
    assert shapes.description == "included video types (projection)"
    assert set(shapes.voice) == {"flat only", "2D only", "VR only", "flat and VR", "VR and flat",
                                 "VR and 2D", "2D and VR"}
    assert orientations.description == "included video types (orientation)"
    assert orientations.voice == ("portrait only", "landscape only", "portrait and landscape",
                                  "landscape and portrait")
    assert levels.description == "included video types (enhancement level)"
    assert levels.voice == ("enhanced only", "original only", "enhanced and original",
                            "original and enhanced")
    assert {"active_enhancement_enhanced", "portrait_enhancement_original",
            "satellites_enhancement_both", "main_enhancement_enhanced"} <= set(levels.commands)
    assert "Origenerator mode" in levels.detail
    assert not [row for row in _section("Main Funestra").rows
                if row.description.startswith("included")]


def test_the_loops_save_and_the_projection_are_said_as_the_rows_name_them():
    flick = genau_flick_noun().one
    save, projection = _the_row_of("genaumacher_save"), _the_row_of("projection_cycle")

    assert (save.description, save.voice) == ("save loop", (f"save {flick}", "save loop"))
    assert projection.voice == ("cycle projection", "projection")
    assert "save clip" not in VOICE_COMMANDS
    assert "clip" not in VOICE_COMMANDS


def test_the_column_heads_say_hotkey_and_voice_command_and_a_forked_hotkey_head_spans_its_funestras():
    html = render_reference_html(1000)

    assert (HOTKEY_HEADER, VOICE_HEADER) == ("Hotkey", "Voice command")
    assert ">Say<" not in html
    assert ">Key<" not in html
    assert re.search(r'colspan="3"[^>]*>\s*<b[^>]*>Hotkey</b>', html)
    forked = re.search(r'<tr[^>]*>(?:<td[^>]*><b[^>]*>(Portrait|Landscape|Main)</b></td>){3}</tr>', html)
    assert forked, "the Funestras' names sit under the spanning Hotkey head"


def _section_html(title: str) -> str:
    [part] = [part for part in re.split(r"(?=<h3 )", render_reference_html(1000))
              if re.match(rf"<h3[^>]*>(?:<a[^>]*>)?{re.escape(title)}</", part)]
    return part


def _group_htmls(section_title: str) -> dict[str, str]:
    untitled, *titled = re.split(r"<h4[^>]*>", _section_html(section_title))
    groups = dict(chunk.split("</h4>", 1) for chunk in titled)
    if "<table" in untitled:
        groups[""] = untitled
    return groups


def test_a_group_whose_rows_hold_no_key_renders_no_hotkey_column():
    origenerator = _section_html("Origenerator")
    assert re.findall(r"<b[^>]*>([^<]+)</b>", origenerator) == ["Action", "Voice command"]
    assert origenerator.count("<td") == 3 * origenerator.count("<tr")
    vr = _section_html("VR")
    assert "Hotkey" not in vr
    assert vr.count("<td") == 3 * vr.count("<tr")
    funestras = _group_htmls("Funestras")
    assert "Hotkey" in funestras["Navigation"]
    assert "Portrait" in funestras["Navigation"]
    assert "Hotkey" not in funestras["Playlist"]
    assert "Portrait" not in funestras["Playlist"]
    assert "Hotkey" not in funestras["Other"]


def _tables(html: str) -> list[str]:
    return re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL)


def _cells_across(row: str) -> list[tuple[str, int, int, int]]:
    x, cells = 0, []
    for cell in re.findall(r"<td[^>]*>.*?</td>", row, re.DOTALL):
        width = int(re.search(r'width="(\d+)"', cell).group(1))
        span = re.search(r'colspan="(\d+)"', cell)
        cells.append((re.sub(r"<[^>]+>", "", cell), x, width, int(span.group(1)) if span else 1))
        x += width
    return cells


def _rows_of(table: str) -> list[str]:
    return re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)


def _grid_at(width: int) -> dict[str, int]:
    action = width - HOTKEY_BLOCK_PX - CONTROLLER_COLUMN_PX - VOICE_COLUMN_PX
    return {"Action": 0, HOTKEY_HEADER: action, CONTROLLER_HEADER: action + HOTKEY_BLOCK_PX,
            VOICE_HEADER: action + HOTKEY_BLOCK_PX + CONTROLLER_COLUMN_PX}


def test_every_table_lays_its_columns_on_one_grid_so_the_heads_line_up_down_the_page():
    for width in (1000, 1400):
        grid = _grid_at(width)
        heads: dict[str, set[int]] = {}
        for table in _tables(render_reference_html(width)):
            assert re.match(rf'<table width="{width}"', table)
            rows = _rows_of(table)
            for text, x, _width, _span in _cells_across(rows[0]):
                heads.setdefault(text, set()).add(x)
            for row in rows:
                cells = _cells_across(row)
                if len(cells) == 3 and cells[0][0] == "Portrait":
                    continue
                assert sum(cell[2] for cell in cells) == width, table[:120]
                assert cells[-1][1:3] == (grid[VOICE_HEADER], VOICE_COLUMN_PX), table[:120]
                if CONTROLLER_HEADER in table:
                    assert cells[-2][1:3] == (grid[CONTROLLER_HEADER], CONTROLLER_COLUMN_PX), table[:120]
        assert {head: xs for head, xs in heads.items() if head in grid} == {
            head: {x} for head, x in grid.items()}
    assert (HOTKEY_BLOCK_PX, CONTROLLER_COLUMN_PX, VOICE_COLUMN_PX) == (300, 120, 220)
    assert ACTION_COLUMN_AT_LEAST_PX == 150
    assert narrowest_page_px(build_reference_sections()) == 790


def test_a_forked_hotkey_block_is_three_columns_of_a_hundred_under_one_head():
    html = render_reference_html(1000)
    navigation = _tables(_group_htmls("Funestras")["Navigation"])[0]
    head, funestras, *body = _rows_of(navigation)
    grid = _grid_at(1000)

    assert [cell[:3] for cell in _cells_across(head)] == [
        ("Action", 0, grid[HOTKEY_HEADER]), (HOTKEY_HEADER, grid[HOTKEY_HEADER], HOTKEY_BLOCK_PX),
        (CONTROLLER_HEADER, grid[CONTROLLER_HEADER], CONTROLLER_COLUMN_PX),
        (VOICE_HEADER, grid[VOICE_HEADER], VOICE_COLUMN_PX)]
    assert [cell[:3] for cell in _cells_across(funestras)] == [
        ("Portrait", 0, 100), ("Landscape", 100, 100), ("Main", 200, 100)]
    assert [cell[1] for cell in _cells_across(body[0])][1:4] == [
        grid[HOTKEY_HEADER], grid[HOTKEY_HEADER] + 100, grid[HOTKEY_HEADER] + 200]
    assert 'colspan="3"' in html
    assert "rowspan" in head


def test_a_table_without_hotkeys_spans_the_block_with_the_action_and_one_without_inputs_leaves_the_column_blank():
    origenerator = _rows_of(_tables(_section_html("Origenerator"))[0])
    dashboard = _rows_of(_tables(_section_html("Dashboard"))[0])
    vr = _rows_of(_tables(_section_html("VR"))[0])
    grid = _grid_at(1000)

    for rows, spans, action_width, blank in (
        (origenerator, (2, 1, 1), grid[CONTROLLER_HEADER], 1),
        (dashboard, (1, 1, 1, 1), grid[HOTKEY_HEADER], 2),
        (vr, (2, 1, 1), grid[CONTROLLER_HEADER], None),
    ):
        for row in rows:
            cells = _cells_across(row)
            assert tuple(cell[3] for cell in cells) == spans, row[:160]
            assert cells[0][2] == action_width, row[:160]
            if blank is not None:
                assert cells[blank][0] == "", row[:160]
                assert cells[blank][1:3] == (grid[CONTROLLER_HEADER], CONTROLLER_COLUMN_PX), row[:160]


def test_a_table_shows_the_controller_column_only_where_a_row_has_a_controller_input():
    vr, navigation = _section_html("VR"), _group_htmls("Funestras")["Navigation"]
    playlist, origenerator = _group_htmls("Funestras")["Playlist"], _section_html("Origenerator")

    assert re.findall(r"<b[^>]*>([^<]+)</b>", _tables(vr)[0])[:3] == ["Action", CONTROLLER_HEADER, VOICE_HEADER]
    assert re.findall(r"<b[^>]*>([^<]+)</b>", navigation) == [
        "Action", HOTKEY_HEADER, CONTROLLER_HEADER, VOICE_HEADER, "Portrait", "Landscape", "Main"]
    assert re.search(rf'<td[^>]*rowspan="2"[^>]*>\s*<b[^>]*>{CONTROLLER_HEADER}</b>', navigation)
    assert CONTROLLER_HEADER not in playlist
    assert CONTROLLER_HEADER not in origenerator
    assert f">{B_OR_Y}<" in navigation
    assert f">{STICK_SIDEWAYS}<" in vr


def test_the_controller_column_says_what_the_headsets_controllers_do_on_each_row():
    by_input = {}
    for row in _all_rows():
        for word in row.controller:
            by_input.setdefault(word, []).append(row.description)

    assert by_input == {
        A_OR_X: ["nudge forward 10 seconds"],
        B_OR_Y: ["nudge back 10 seconds"],
        HOLD_TRIGGER_AND_B_OR_Y: ["previous scene in the video"],
        HOLD_TRIGGER_AND_A_OR_X: ["next scene in the video"],
        STICK_SIDEWAYS: ["cycle the video's VR projection"],
        ANGLE_BAR: ["narrow / widen the angle a wrapped VR video spans"],
        STICK_UP_DOWN: ["move or resize a VR screen", "open the browser"],
        HOLD_TRIGGER_AND_STICK_UP_DOWN: ["move or resize a VR screen"],
        HOLD_TRIGGER_AND_MOVE: ["tilt the VR screens up / down and level them again"],
        TRIGGER: ["move or resize a VR screen", "HUD to a corner", "open the browser"],
    }
    assert "handle" in next(row for row in _section("VR").rows
                            if row.description == "move or resize a VR screen").detail
    assert "squeeze" in _the_row_of("browse_library").detail


def test_the_controller_column_is_headed_vr_controller_and_says_one_input_per_phrase():
    assert CONTROLLER_HEADER == "VR Controller"
    assert re.search(r"<b[^>]*>VR Controller</b>", render_reference_html(1000))
    for row in _all_rows():
        for phrase in row.controller:
            assert "," not in phrase, phrase
            assert " and " not in phrase, phrase
            assert phrase.count("+") <= 1, phrase
            assert "+" not in phrase or phrase.startswith("hold trigger + "), phrase


def test_the_forked_hotkey_head_is_centered_over_its_funestras():
    assert re.search(
        r'<td(?=[^>]*colspan="3")(?=[^>]*align="center")[^>]*>\s*<b[^>]*>Hotkey</b>',
        render_reference_html(1000))


def test_every_column_head_sits_in_the_middle_of_its_cell_but_action_which_sits_over_the_actions():
    html = render_reference_html(1000)
    heads = re.findall(
        r"<td[^>]*>\s*<b[^>]*>(?:Action|Hotkey|VR Controller|Voice command|Portrait|Landscape|Main)</b></td>",
        html)
    forked = [head for head in heads if 'colspan="3"' in head]

    assert heads
    assert forked
    for head in heads:
        assert 'valign="middle"' in head, head
        assert ('align="left"' in head) == (">Action<" in head), head
        assert ('align="center"' in head) == (">Action<" not in head), head
        assert (f"border-bottom:1px solid {_BORDER}" in head) == (head in forked), head
    assert "border-top" not in html


def test_the_funestras_note_aims_a_phrase_and_says_genaus_word_for_a_video():
    flick = genau_flick_noun().one

    assert _funestras_section().note == (
        "Alone, a phrase reaches whatever you last navigated.  Add “portrait”, "
        "“landscape”, “main”, or “satellites”, before it or after, to aim it, for example "
        f"“portrait next” or “next main”.  In Genau mode say “{flick}” in place of “video”."
    )
    for section in build_reference_sections():
        if "“" in section.note:
            assert "for example “" in section.note, section.title


def test_an_untitled_group_renders_no_heading_and_a_flat_section_is_its_table():
    for title in ("VR", "Genau", "Origenerator"):
        assert "<h4" not in _section_html(title), title
    main = _section_html("Main Funestra")
    assert main.index("<table") < main.index("<h4")
    assert set(_group_htmls("Main Funestra")) == {"", "Loops"}


def test_a_sections_note_is_the_tooltip_on_its_title():
    html = render_reference_html(1000)
    details = reference_details()
    noted = [section for section in build_reference_sections() if section.note]

    assert noted
    for section in noted:
        anchor = re.search(rf'<a href="(detail:\d+)"[^>]*>{re.escape(section.title)}</a>', html)
        assert anchor, section.title
        assert details[anchor.group(1)] == section.note
        assert section.note not in html
    assert len(details) == len(noted) + len([r for r in _all_rows() if r.detail])


def test_omnipause_row_uses_esc_and_pause_play_voice():
    rows = _all_rows()
    esc_rows = [r for r in rows if "Esc" in _keys(r)]
    assert esc_rows, "expected an Esc hotkey row"
    row = esc_rows[0]
    assert "omnipause_toggle" in row.commands
    assert "pause" in row.voice
    assert "play" in row.voice


def test_both_ways_into_omnipause_show_on_the_space_row():
    [space_row] = [r for r in _all_rows() if "Space" in _keys(r)]

    assert space_row.commands == ("enter_omnipause",)
    assert "pause" in space_row.voice


def test_the_nav_map_columns_line_up_each_running_up_then_down():
    row = _the_row_of("portrait_nav_up")

    assert row.key_columns == (("Shift+Up", "Shift+Down"), ("Shift+W", "Shift+S"), ())


def test_relief_omnipause_row_shows_shift_esc_and_the_single_word_name():
    assert VOICE_COMMANDS["relief omni pause"] == "relief_omnipause"
    assert "relief omnipause" not in VOICE_COMMANDS

    rows = [r for r in _all_rows() if "relief_omnipause" in r.commands]
    assert len(rows) == 1, "expected exactly one relief row"
    row = rows[0]
    assert _keys(row) == ("Shift+Esc",)
    assert row.voice == ("relief omnipause", "stop")


def test_relief_said_in_a_hurry_is_a_single_word():
    assert len(RELIEF_SAID_IN_A_HURRY.split()) == 1
    assert VOICE_COMMANDS[RELIEF_SAID_IN_A_HURRY] == "relief_omnipause"


def test_relief_survives_the_omnipause_suspension_on_both_input_paths():
    assert "relief_omnipause" in _ahk_suspend_exempt_commands()
    assert "relief_omnipause" in SUSPEND_EXEMPT_COMMANDS


def test_the_spoken_three_a_pause_leaves_live_are_the_scripts_three_exempt_keys():
    spoken_for_key = {"Esc": "play", "^!q": "quit", "+Esc": "relief_omnipause"}
    assert suspend_exempt_keys() == set(spoken_for_key)
    assert frozenset(spoken_for_key.values()) == SPOKEN_FOR_THE_KEYS_A_PAUSE_LEAVES_LIVE


def test_a_paused_room_hears_what_he_ruled_and_nothing_else():
    assert frozenset({
        "play", "quit", "relief_omnipause",
        "enter_vr", "exit_vr", "recenter_view", "vr_reset",
    }) == SUSPEND_EXEMPT_COMMANDS


def test_no_say_column_leaks_the_raw_omni_pause_form():
    for row in _all_rows():
        for phrase in row.voice:
            assert "omni pause" not in phrase, phrase


def test_funscript_row_shows_the_joined_word_the_recognizer_cannot_hear():
    assert VOICE_COMMANDS["jump to fun script"] == "active_funscript_jump"
    assert "jump to funscript" not in VOICE_COMMANDS

    jump, next_scripted = _the_row_of("main_funestra_funscript_jump"), _the_row_of("main_funestra_next_funscripted")
    assert (jump.description, jump.voice) == ("jump to funscript", ("jump to funscript",))
    assert (next_scripted.description, next_scripted.voice) == ("next funscripted", ("next funscripted",))
    assert jump.detail
    assert next_scripted.detail
    assert _keys(jump) == _keys(next_scripted) == ()
    assert {"active_funscript_jump", "portrait_funscript_jump", "landscape_funscript_jump",
            "satellites_funscript_jump"} <= set(jump.commands)
    assert {"active_next_funscripted", "satellites_next_funscripted"} <= set(next_scripted.commands)
    assert jump in _group("Funestras", "Navigation").rows
    for row in _all_rows():
        for phrase in row.voice:
            assert "fun script" not in phrase, phrase


def test_reference_does_not_import_voice_runtime():
    code = (
        "import fun_time.command_reference, sys; "
        "bad = [m for m in sys.modules if m in ('fun_time.voice_control', 'vosk', 'sounddevice')]; "
        "assert not bad, bad"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, result.stderr


def test_render_reference_html_contains_key_content():
    html = render_reference_html(1000)
    assert isinstance(html, str)
    assert "<table" in html
    assert "Esc" in html
    assert "genau" in html
    assert "Genau" in html
    assert not _AN_UNFILLED_PLACEHOLDER.search(html)


def test_render_reference_html_has_no_heading_or_subtitle():
    html = render_reference_html(1000)
    assert "<h2" not in html
    assert "Hotkeys &amp; Voice Commands Reference" not in html
    assert "Global while Fun Time" not in html


_ACTIONS = {"alpha": ("alpha", "al fa"), "beta gamma": ("beta gamma",)}


def _filter_rows(actions) -> tuple[CommandRef, ...]:
    funestras = {s.title: s for s in build_reference_sections(actions)}["Funestras"]
    return {group.title: group for group in funestras.groups}["Filter"].rows


def test_the_spoken_filters_are_two_rows_of_the_satellite_grid():
    rows = _filter_rows(_ACTIONS)
    set_row = next(r for r in rows if r.description.startswith("filter by action"))
    assert set_row.voice == display_forms(_ACTIONS) == ("alpha", "beta gamma")
    drop_row = next(r for r in rows if r.description.startswith("drop all filters"))
    assert set(drop_row.voice) == {"no filter", "filter off", "clear filter", "show everything"}
    assert VOICE_COMMANDS["portrait clear filter"] == "portrait_no_filter"
    assert VOICE_COMMANDS["clear filter landscape"] == "landscape_no_filter"
    assert VOICE_COMMANDS["satellites show everything"] == "satellites_no_filter"
    assert VOICE_COMMANDS["clear filter"] == "active_no_filter"
    assert "clear portrait" not in VOICE_COMMANDS


def test_every_filter_voice_command_is_represented():
    covered = {cmd for row in _filter_rows(_ACTIONS) for cmd in row.commands}
    missing = {cmd for cmd in filter_voice_commands(_ACTIONS).values() if cmd not in covered}
    assert not missing, f"filter commands missing from reference: {sorted(missing)}"


def test_the_browser_opens_by_voice_alone():
    browser = [r for r in _section("Main Funestra").rows if "browse_library" in r.commands]
    assert len(browser) == 1
    assert _keys(browser[0]) == ()
    assert "browse" in browser[0].voice


def test_the_reference_calls_a_funestras_hud_its_hud_and_never_its_panel():
    said = [phrase for row in _all_rows() for phrase in (row.description, row.detail, *row.voice)
            if "panel" in phrase.lower()]

    assert said == []


def test_the_f_mode_row_says_what_the_main_funestra_keeps_in_each_mode():
    flick = genau_flick_noun()
    f_mode = next(row for row in _all_rows() if "main_fmode" in row.commands)

    assert "only the well funscripted videos" in f_mode.detail
    assert f"in Genau mode only the favorite {flick.many}" in f_mode.detail


def test_no_command_name_reaches_the_reference_on_either_screen():
    sections = build_reference_sections()
    names = {command for section in sections for row in section.rows
             for command in row.commands if "_" in command}
    shown = [render_reference_html(1000)] + [
        text
        for section in sections
        for text in (section.title, section.note, *section.key_headers,
                     *(group.title for group in section.groups))
    ] + [
        text
        for section in sections
        for row in section.rows
        for text in (row.description, row.detail, *row.voice, *row.controller,
                     *(key for column in row.key_columns for key in column))
    ]
    assert len(names) > 100
    assert not [name for name in sorted(names) if any(name in text for text in shown)]


def test_the_reference_html_styles_inline_with_no_style_block():
    html = render_reference_html(1000)

    assert "<style" not in html
    assert 'style="' in html


_NOT_HIS_WORDS = re.compile(r"\b(acts?|pictures?|subjects?|configs?|famil(y|ies))\b", re.IGNORECASE)


def test_the_reference_names_things_by_the_apps_own_words():
    said = [text for section in build_reference_sections()
            for text in (section.title, section.note or "",
                         *(part for row in section.rows for part in (row.description, row.detail)))]

    assert [text for text in said if _NOT_HIS_WORDS.search(text)] == []


def test_the_satellites_loops_cycles_and_more_seeds_read_in_his_words():
    rows = {f"{row.description} — {row.detail}" for section in build_reference_sections()
            for row in section.rows}

    assert {"cycle action — same seed or initial image, another action",
            "cycle seed — same settings, another seed",
            "loop actions — same seed or initial image, all of its actions",
            "loop seeds — same settings, all of their seeds",
            "wrong action — the item's actions are wrong; send it back "
            "to Origenerator's action setter tool to be set again",
            "more seeds — widen the net to include seeds from very similar settings"} <= rows
