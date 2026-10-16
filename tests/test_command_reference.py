from __future__ import annotations

import importlib
import re
import subprocess
import sys

from fun_time import command_reference, content
from fun_time.command_dispatch import _MAIN_FUNESTRA_CMD_MAP, _about_genaus_flick
from fun_time.command_reference import (
    _BORDER,
    _SECTIONS,
    A_OR_X,
    B_OR_Y,
    CONTROLLER_COLUMN_PX,
    CONTROLLER_HEADER,
    HOTKEY_BLOCK_PX,
    HOTKEY_HEADER,
    SQUEEZE_AND_MOVE,
    STICK_SIDEWAYS,
    STICK_UP_DOWN,
    TRIGGER,
    VOICE_COLUMN_PX,
    VOICE_HEADER,
    CommandRef,
    ReferenceGroup,
    ReferenceSection,
    _collapse_scopes,
    _display_voice,
    build_reference_sections,
    reference_details,
    render_reference_html,
)
from fun_time.console_buttons import MODE_BUTTONS
from fun_time.content import Noun, genau_flick_noun
from fun_time.filter_vocab import display_forms, filter_voice_commands, load_acts
from fun_time.voice_commands import VOICE_COMMANDS, friendly_voice
from fun_time.voice_control import SUSPEND_EXEMPT_COMMANDS
from tests.ahk_script import script_text, suspend_exempt_block

_NUMERIC_RE = re.compile(r"^(robot_hand_(amp|center|speed)|genau_flick_seconds)_\d+$")
_QUEUED_RE = re.compile(r'QueueCommand\("([^"]+)"\)')


def _ahk_hotkey_commands() -> set[str]:
    """Every command bound to a key via QueueCommand() in the AHK hotkey script."""
    return set(_QUEUED_RE.findall(script_text()))


def _ahk_bindings_for(command: str) -> list[str]:
    """Every key the AHK script binds to ``command``, as the script spells them."""
    return re.findall(rf'^(\S+)::QueueCommand\("{re.escape(command)}"\)', script_text(), re.MULTILINE)


def _ahk_binding_for(command: str) -> str | None:
    """The first key the AHK script binds to ``command``, as the script spells it."""
    bound = _ahk_bindings_for(command)
    return bound[0] if bound else None


def _ahk_suspend_exempt_commands() -> set[str]:
    """The commands bound inside the script's ``#SuspendExempt`` block.

    Omnipause suspends the hotkeys wholesale, so these are the only keys that
    still reach Python while the session is paused.
    """
    return set(_QUEUED_RE.findall(suspend_exempt_block()))


def _keys(row: CommandRef) -> tuple[str, ...]:
    """Every key label on a row, flattened across its section's key columns.

    The row stores the keys split per column, which is how they are drawn; a
    test that only asks "does this row hold a key at all" wants them in one
    sequence.
    """
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
            # Every row must offer at least one way to trigger it.
            assert _keys(row) or row.voice or row.controller, f"row {row.description!r} has no trigger"


def test_every_voice_command_is_represented():
    """Every non-numeric phrase in VOICE_COMMANDS maps to a command shown in the reference."""
    covered = _covered_commands()
    missing = {
        cmd
        for cmd in VOICE_COMMANDS.values()
        if not _NUMERIC_RE.match(cmd) and cmd not in covered
    }
    assert not missing, f"voice commands missing from reference: {sorted(missing)}"


def test_numeric_voice_pattern_is_documented():
    """The generated amp/center/speed numeric phrases are summarized by one row."""
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
    """Voice is muted by voice ("voice off" / "mic off") or the dashboard mic
    button — never a hotkey.  Backspace used to toggle it, which nobody could
    remember, so the key binding was removed from both the AHK script and here."""
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
    """Cycling a clip's action or seed is a spoken command on both sides.

    Each held a key once — Del/End on portrait, E/Q on landscape — and none of the
    four was ever used, so they went back to the pool; E now ends a landscape loop.
    """
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
        # The scopes share one row, so the Say column shows the folded phrase.
        assert shown in row.voice


def test_the_group_loops_are_cycled_by_one_key_per_side():
    """Each satellite's whole loop lives on one key — Home for portrait, E for
    landscape — which steps it seeds → actions → off → seeds.

    That key started out as a plain way out of a loop, but ending one is only the
    last of the three stops, so it reaches the cycle command rather than no_loop.
    Ending a loop outright stays spoken ("portrait end loop"), and holds no key.
    """
    expected = {"portrait_loop": ("Home", 0), "landscape_loop": ("E", 1)}
    for cmd, (key, column) in expected.items():
        bound = _ahk_binding_for(cmd)
        assert bound is not None, f"{cmd} must be bound in the AHK hotkey script"
        # AHK writes letter hotkeys lowercase; the reference labels them uppercase.
        assert bound.lower() == key.lower(), f"{cmd} is on {bound!r}, expected {key!r}"
        # The sides share a row, each key under its own column.
        assert _the_row_of(cmd).key_columns[column] == (key,)
    for cmd in ("portrait_no_loop", "landscape_no_loop"):
        assert _ahk_binding_for(cmd) is None, f"{cmd} gave its key to the cycle"
        assert _keys(_the_row_of(cmd)) == ()


def _section(title: str) -> ReferenceSection:
    return {s.title: s for s in build_reference_sections()}[title]


def _group(section_title: str, group_title: str) -> ReferenceGroup:
    return {group.title: group for group in _section(section_title).groups}[group_title]


def _satellite_sections() -> tuple[ReferenceSection, ...]:
    """The two sections with a key column per satellite side: what the sides
    answer in either mode, and the map they both browse by."""
    return (_section("Satellite Funestras"), _section("Nav Map"))


def _satellite_section() -> ReferenceSection:
    return _section("Satellite Funestras")


def _funestras_section() -> ReferenceSection:
    """What every Funestra answers, a key column per Funestra."""
    return _section("Funestras")


def _shared_sections() -> tuple[ReferenceSection, ...]:
    return (_funestras_section(), *_satellite_sections())


def _the_row_of(cmd: str) -> CommandRef:
    """A satellite's command has one row, whichever mode the side is in."""
    [row] = [row for row in _all_rows() if cmd in row.commands]
    return row


def test_every_scope_shares_one_grid_with_a_key_column_per_funestra():
    """Portrait, Landscape, Both, Active side and Filters ran the same list of
    actions, so they are one grid now: a row names every scope that has the
    action, and puts each Funestra's key in its own column -- the Main
    Funestra's too, where it answers the same action.  Five sections said it
    all five times and were free to drift apart."""
    for section in _satellite_sections():
        assert section.key_headers == ("Portrait", "Landscape")
    assert _funestras_section().key_headers == ("Portrait", "Landscape", "Main")
    for section in _shared_sections():
        for row in section.rows:
            by_scope = {
                scope: {c[len(scope) + 1:] for c in row.commands if c.startswith(f"{scope}_")}
                for scope in ("portrait", "landscape", "satellites", "active")
            }
            assert by_scope["portrait"] == by_scope["landscape"], (
                f"row {row.description!r} names different actions per side"
            )
            # Every scope the action exists in must be named — the grid is uniform
            # apart from the exceptions each carries a comment for.
            for scope, actions in by_scope.items():
                for action in by_scope["portrait"] | actions:
                    command = f"{scope}_{action}"
                    if command in VOICE_COMMANDS.values():
                        assert command in row.commands, (
                            f"row {row.description!r} omits {command}"
                        )
            assert len(row.key_columns) == len(section.key_headers)


def test_the_shared_grid_drops_the_scope_word_from_the_say_column():
    """A row cannot spell out "portrait next", "next landscape", "satellites next" and
    "next" without printing its own action four times, so the Say column keeps the
    action alone and the section's note explains how to aim it."""
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
            if row.description.startswith(("filter by act", "drop ", "favorites")):
                continue  # the filters scope themselves differently; bare "favorites" is no phrase
            for phrase in row.voice:
                if _collapse_scopes(phrase) not in sayable_bare:
                    continue
                assert not {"portrait", "landscape", "satellites", "main"} & set(phrase.split()), (
                    f"{phrase!r} still carries a scope word"
                )


def test_the_grid_carries_every_satellites_and_active_command():
    """Nothing was dropped in the consolidation: each command the retired "Both",
    "Active side" and "Filters" sections documented is on a row of the grid."""
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
    """The side-agnostic phrases are what the grid shows — the whole point of
    folding the scope word out is that "next" is the entry a reader sees."""
    rows = [row for section in _shared_sections() for row in section.rows]
    by_command = {cmd: r for r in rows for cmd in r.commands}
    expected = {
        "active_lock_on": "lock",
        "active_lock_off": "unlock",
        "active_prev": "previous",
        "active_next": "next",
        "active_trash": "weird",
        "active_cycle_action": "action",
        "active_cycle_seed": "seed",
        "active_wrong_action": "wrong action",
        "active_fmode": "f mode",
    }
    for cmd, phrase in expected.items():
        assert cmd in by_command, f"{cmd} missing from the satellite grid"
        assert phrase in by_command[cmd].voice


def test_a_sides_favorites_are_their_own_row_said_with_the_side_named():
    f_mode = next(r for r in _funestras_section().rows if "active_fmode" in r.commands)
    [favorites] = [r for r in _satellite_section().rows if r.description == "favorites"]

    assert f_mode.voice == ("f mode", "f mode on", "f mode off")
    assert favorites.voice == ("portrait favorites", "landscape favorites")
    assert set(favorites.commands) == {"portrait_say_favorites", "landscape_say_favorites"}


def test_the_shared_grid_carries_every_funestras_own_playback_speed():
    rows = _funestras_section().rows
    nudge = next(r for r in rows if "portrait_speed_up" in r.commands)
    rate = next(r for r in rows if "portrait_speed_50" in r.commands)

    assert nudge.voice == ("playback speed up", "playback slow down", "playback speed down")
    assert {"active_speed_up", "satellites_speed_down", "landscape_speed_up",
            "main_player_speed_up", "main_player_speed_down"} <= set(nudge.commands)
    assert {"active_speed_min", "satellites_speed_max", "active_speed_150", "satellites_speed_200",
            "landscape_speed_25", "main_player_speed_50"} <= set(rate.commands)


def test_the_reference_calls_the_mode_kino_and_never_video():
    rows = _all_rows()
    said = [(row.description, phrase) for row in rows for phrase in (row.description, *row.voice)
            if "video mode" in phrase.lower()]

    assert said == []
    assert [row.description for row in rows if "kino_activate" in row.commands] == ["Kino mode"]


def test_mode_named_nav_shows_friendly_names_in_the_legend():
    """The Genau nav rows surface the mode-named phrases under their friendly
    name ("genau next") — never the raw vosk sound-alike ("go now")."""
    rows = _all_rows()
    genau_next_row = next(r for r in rows if "genau_next_flick" in r.commands)
    assert {"genau next", "next genau"} <= set(genau_next_row.voice)
    # The raw sound-alikes must never leak into any Say column.
    for row in rows:
        for phrase in row.voice:
            assert "now mode" not in phrase and "go now" not in phrase, phrase
            assert "aura generator" not in phrase, phrase


def test_no_row_tells_you_to_say_genau_by_its_sound_alike():
    assert not [row.description for row in _all_rows() if '"go now' in row.description]


def test_the_main_funestra_shares_the_navigation_rows_under_its_own_key_column():
    """"main previous" and "main next" are the shared grid's "previous" and
    "next" said of the Main Funestra, so its M and period sit in the third key
    column of the same rows, and the note on the title says how to aim them."""
    rows = _funestras_section().rows
    prev = next(r for r in rows if "main_prev" in r.commands)
    nxt = next(r for r in rows if "main_next" in r.commands)
    flick = genau_flick_noun().one
    assert prev.voice == ("previous", "previous video", "genau previous", f"previous {flick}",
                          "previous genau")
    assert nxt.voice == ("next", "next video", "genau next", f"next {flick}", "next genau")
    assert (prev.key_columns[2], nxt.key_columns[2]) == (("M",), (".",))
    assert "“next main”" in _funestras_section().note


def test_the_navigation_rows_are_the_huds_transport_row_then_weird_lock_and_the_versions():
    """Previous, back and forward 10 seconds, next, in the order every HUD's
    transport row has them; the Main Funestra's - and + sit in its column and
    say "back" and "skip" like the satellites' own; then weird, lock / unlock
    and the one row that steps every Funestra through a clip's versions."""
    navigation = _group("Funestras", "Navigation")
    back, forward = navigation.rows[1], navigation.rows[2]

    assert [row.description for row in navigation.rows] == [
        "previous", "nudge back 10 seconds", "nudge forward 10 seconds", "next", "weird",
        "lock / unlock", "cycle enhancement level"]
    assert (back.voice, forward.voice) == (("back",), ("skip",))
    assert (back.key_columns, forward.key_columns) == (((), (), ("-",)), ((), (), ("+",)))
    assert {"portrait_nudge_prev", "satellites_nudge_prev", "active_nudge_prev", "main_nudge_prev"
            } <= set(back.commands)
    assert not [row for row in _all_rows() if row.description.startswith("skip / back")]
    assert not [row for row in _section("Main Funestra").rows if "nudge" in row.description]


def test_genaus_flick_steps_and_weird_ride_the_shared_rows_and_k_sits_under_main():
    weird = _the_row_of("genau_weird_flick")
    flick = genau_flick_noun().one

    assert weird.description == "weird"
    assert weird.key_columns == (("Up",), ("W",), ("K",))
    assert weird.voice == ("weird", "weird video", f"weird {flick}")
    assert weird.detail == (
        "in Kino mode it marks the clip weird and moves on, or unfavorites it instead "
        "when it is a favorite; in Origenerator mode it deletes the picture and moves on")
    assert _the_row_of("genau_prev_flick").description == "previous"
    assert _the_row_of("genau_next_flick").description == "next"


def test_one_row_steps_every_funestra_through_the_enhancement_levels():
    versions = _the_row_of("satellites_cycle_version")

    assert versions == _the_row_of("main_player_cycle_version_back")
    assert versions.description == "cycle enhancement level"
    assert versions.detail == "the upscale, the original it was made from, or an enhancement"
    assert versions.voice == ("version", "change version", "cycle version", "next version")
    assert versions.key_columns == (("Shift+Left", "Shift+Right"), ("Shift+A", "Shift+D"),
                                    ("Shift+M", "Shift+."))
    assert not [row for row in _all_rows() if row.description.startswith("step through")]


def test_the_playback_nudge_is_its_own_spoken_row_ahead_of_the_absolute_sets():
    """Two ways to move the rate, and the row order follows the Genau section's:
    the up/down nudge, then the line that sets it outright.  The nudge is spoken
    only — the keys say "speed up", which follows the OSR2's driver instead."""
    rows = _funestras_section().rows
    descs = [r.description for r in rows]
    nudge = next(r for r in rows if "main_player_speed_up" in r.commands)
    assert _keys(nudge) == ()
    assert nudge.voice == ("playback speed up", "playback slow down", "playback speed down")
    assert descs.index(nudge.description) + 1 == next(
        i for i, d in enumerate(descs) if d.startswith("set playback speed")
    )


def test_the_quit_row_no_longer_offers_the_exit_synonym():
    """"exit" quit the session until it made "exit VR" unsayable: the shorter
    phrase won every recognition, so leaving the headset ended the room instead
    (docs/entering-vr.md).  The legend must not keep offering a word the
    recognizer now reads as the crossing."""
    rows = _all_rows()
    quit_rows = [r for r in rows if "quit" in r.commands]
    assert quit_rows and set(quit_rows[0].voice) == {"quit"}


def test_reference_popup_row_shows_toggle_and_close_names():
    """One row documents both toggling ("help", "hotkeys", …) and closing
    ("close help", …) the popup, always under the friendly "hotkeys" name — never
    the raw vosk recognizer form "hot keys"."""
    rows = _all_rows()
    help_rows = [r for r in rows if "help_reference" in r.commands]
    assert len(help_rows) == 1, "expected exactly one reference-popup row"
    row = help_rows[0]
    assert "help_reference_close" in row.commands
    voice = set(row.voice)
    assert {"help", "hotkeys", "reference", "voice commands"} <= voice
    assert {"close help", "close hotkeys", "close reference", "close voice commands"} <= voice
    assert "hot keys" not in voice  # the OOV recognizer form is hidden under "hotkeys"


def test_sound_rows_are_voice_only_and_reach_every_funestra():
    """Mute and the volume steps are spoken-only, said of every Funestra like
    the rest of the shared grid, and each step surfaces both of its synonyms so
    the legend never implies one of them is the "real" phrase."""
    rows = _funestras_section().rows
    mute = next(r for r in rows if "audio_mute" in r.commands)
    assert _keys(mute) == ()
    # "unmute" is one word to the reader; only the recognizer hears "un mute".
    assert mute.voice == ("mute", "unmute")
    assert {"audio_unmute", "portrait_mute", "landscape_unmute", "satellites_mute",
            "active_unmute"} <= set(mute.commands)

    steps = [r for r in _all_rows() if "audio_volume_up" in r.commands]
    assert steps == [r for r in rows if "audio_volume_up" in r.commands]
    assert len(steps) == 1, "expected one volume row"
    assert {"audio_volume_down", "portrait_volume_up", "landscape_volume_down",
            "satellites_volume_up", "active_volume_down"} <= set(steps[0].commands)
    assert _keys(steps[0]) == ()
    assert set(steps[0].voice) == {"quiet", "quieter", "loud", "louder"}


def test_no_say_column_leaks_the_raw_un_mute_form():
    for row in _all_rows():
        for phrase in row.voice:
            assert "un mute" not in phrase, phrase


def test_latest_is_spoken_only_and_the_older_names_are_gone():
    """The newest-first refresh is branded "Latest", by voice alone: it lost the P
    key along with the global command that key used to send.

    Both older names are gone rather than kept as synonyms — "recents" especially,
    which the small vosk model has no word for and heard as "reset".
    """
    [row] = [r for r in _all_rows() if "satellites_latest" in r.commands]
    assert _keys(row) == ()
    # It sits in the shared grid like every other action, so its voice column
    # is the bare "latest" and "satellites latest" comes from the section's note.
    assert row.voice == ("latest",)
    assert {"portrait_latest", "landscape_latest", "active_latest", "main_latest"} <= set(row.commands)
    assert "premiere" not in VOICE_COMMANDS
    assert "recents" not in VOICE_COMMANDS


def test_end_loop_follows_the_funestra_last_spoken_to():
    """"end loop" is side-agnostic like every other bare command: it reaches whichever
    Funestra was last addressed, and means that Funestra's kind of loop — the Main Funestra's A-B loop
    on the Main Funestra, a satellite's group loop on portrait or landscape."""
    assert VOICE_COMMANDS["end loop"] == "active_no_loop"
    assert VOICE_COMMANDS["portrait end loop"] == "portrait_no_loop"
    assert VOICE_COMMANDS["end loop landscape"] == "landscape_no_loop"
    assert VOICE_COMMANDS["satellites end loop"] == "satellites_no_loop"


def test_latest_and_shuffle_reach_one_side_or_both():
    """Both orderings were global-only, so "portrait premiere" parsed as nothing.
    They join the side grid like every other satellite action — and shuffle has to
    come too, or a side put in latest order could never be shuffled back alone."""
    for word, action in (("latest", "latest"), ("shuffle", "shuffle")):
        assert VOICE_COMMANDS[word] == f"active_{action}"
        assert VOICE_COMMANDS[f"portrait {word}"] == f"portrait_{action}"
        assert VOICE_COMMANDS[f"{word} landscape"] == f"landscape_{action}"
        assert VOICE_COMMANDS[f"satellites {word}"] == f"satellites_{action}"


def test_voice_phrases_are_derived_from_voice_commands():
    """Each row's voice must include every phrase VOICE_COMMANDS assigns to its
    commands — except rows with an explicit voice_display alias."""
    inverse: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        inverse.setdefault(cmd, []).append(phrase)
    for section in _SECTIONS:
        for row in (row for group in section.groups for row in group.rows):
            if row.voice_display is not None:
                continue
            built = _display_voice(row, merge_scopes=section.merge_scopes)
            # Phrases are shown under their friendly mode name (sound-alikes rewritten),
            # and with the scope word folded away where the scopes share a row.
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
    assert genau_rows and genau_rows[0].voice == ("genau", "genau mode")


def test_the_reference_names_genaus_flicks_in_the_overlays_word():
    flick = genau_flick_noun()
    genau = _group("Main Funestra", "Genau")
    seconds = next(row for row in genau.rows if "genau_flick_seconds_up" in row.commands)

    assert [row.description for row in genau.rows] == [
        f"flip the Genau {flick.one} half a loop",
        f"seconds a {flick.one} holds the screen before Genau moves on"]
    assert seconds.voice == (f"{flick.one} seconds 1–60",)
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
    """G, X, V, T, P and Alt+Left / Alt+Right left the keyboard: the modes,
    the versions and the projection are still spoken, on a HUD or on a
    controller, and an id nothing but its key reached is gone with the key."""
    for key in ("g", "x", "v", "t", "p", "!Left", "!Right"):
        assert not re.search(rf"^{re.escape(key)}::", script_text(), re.MULTILINE), key
    retired = {"satellites_toggle", "main_player_toggle_length",
               "projection_narrower", "projection_wider"}
    assert not retired & _covered_commands()
    assert not retired & set(VOICE_COMMANDS.values())
    assert not retired & _ahk_hotkey_commands()
    modes = _group("Funestras", "Modes")
    assert [_keys(row) for row in modes.rows] == [(), (), ()]
    assert "X" not in _the_row_of("satellites_origenerator_activate").detail
    assert _keys(_the_row_of("main_player_length_clips")) == ()
    assert _keys(_the_row_of("projection_cycle")) == ()
    assert not [row for row in _all_rows() if row.description.startswith("narrow / widen")]


def test_the_sections_are_the_dashboard_vr_the_osr2_every_funestra_each_funestra_the_map_origenerator_and_the_rest():
    titles = [s.title for s in build_reference_sections()]
    assert titles == [
        "Dashboard",
        "VR",
        "OSR2",
        "Funestras",
        "Main Funestra",
        "Satellite Funestras",
        "Nav Map",
        "Origenerator",
        "Other",
    ]


def test_every_section_is_read_in_groups_of_a_few_rows_each():
    """Twenty rows under one heading is a wall; each section's rows come in
    groups, only the first of which may go untitled, and a short flat section
    is the one group of everything allowed."""
    for section in build_reference_sections():
        assert section.groups, section.title
        assert all(group.rows for group in section.groups), section.title
        assert all(group.title for group in section.groups[1:]), section.title
        assert len(section.groups) > 1 or len(section.rows) <= 6, section.title
        assert section.rows == tuple(row for group in section.groups for row in group.rows)


def test_what_a_row_explains_is_its_detail_and_what_it_names_is_its_description():
    """He reads the action, and the explanation after the dash waits in a
    tooltip, so the popup is a list of actions rather than paragraphs."""
    relief = next(row for row in _all_rows() if "relief_omnipause" in row.commands)
    quit_ = next(row for row in _all_rows() if "quit" in row.commands)

    assert relief.description == "relief OmniPause"
    assert relief.detail.startswith("OmniPause and retract together")
    assert quit_.description == "quit"
    assert quit_.detail == "close everything"
    for row in _all_rows():
        assert " — " not in row.description, row.description
        assert not row.description.endswith(":"), row.description


def test_the_genau_groups_rows_take_the_overlays_word(monkeypatch):
    monkeypatch.setattr(content, "genau_flick_noun", lambda: Noun(one="bloop", many="bloops"))
    try:
        main = next(section for section in importlib.reload(command_reference).build_reference_sections()
                    if section.title == "Main Funestra")
        genau = main.groups[-1]
        assert (genau.title, genau.rows[0].description) == ("Genau", "flip the Genau bloop half a loop")
    finally:
        monkeypatch.undo()
        importlib.reload(command_reference)



def test_genaus_flick_commands_are_the_genau_group_or_ride_a_shared_navigation_row():
    """The dispatch ignores a flick command in Kino mode, so the rows it owns
    are the Genau group's -- except the steps and the weird, which ride the
    shared previous, next and weird rows, where the Main Funestra's own keys
    reach whatever is on it."""
    genau = _group("Main Funestra", "Genau")
    in_genau = {cmd for row in genau.rows for cmd in row.commands}
    elsewhere = {cmd for row in _all_rows() if row not in genau.rows for cmd in row.commands
                 if _about_genaus_flick(cmd)}

    assert in_genau and all(_about_genaus_flick(cmd) for cmd in in_genau)
    assert elsewhere == {"genau_prev_flick", "genau_next_flick", "genau_weird_flick"}
    assert all(_the_row_of(cmd) in _group("Funestras", "Navigation").rows for cmd in elsewhere)


def test_what_only_kino_answers_is_the_kino_group_and_nothing_else_is():
    """The dispatch ignores these in Genau mode -- the video's own lock, loop,
    jumps and the clip save -- so their rows are the Kino group's alone.  The
    versions are the one exception: every Funestra steps through a clip's
    versions, so the Main Funestra's pair rides that shared row."""
    versions = {"main_player_cycle_version", "main_player_cycle_version_back"}
    kino_only = (set(_MAIN_FUNESTRA_CMD_MAP) - versions) | {"genaumacher_save"}
    kino = _group("Main Funestra", "Kino")
    in_kino = {cmd for row in kino.rows for cmd in row.commands}
    elsewhere = {cmd for row in _all_rows() if row not in kino.rows for cmd in row.commands}

    assert kino_only <= in_kino
    assert not kino_only & elsewhere
    assert versions <= set(_the_row_of("satellites_cycle_version").commands)


def test_a_satellites_command_has_one_row_whichever_mode_the_side_is_in():
    """What a satellite's key does changes with the side's mode -- a clip in
    Kino mode, a slide of the slideshow in Origenerator mode -- and the key
    itself does not, so one row carries it and its detail says both."""
    for cmd in ("landscape_trash", "portrait_nav_up", "landscape_loop", "satellites_cycle_version"):
        assert _keys(_the_row_of(cmd)), cmd
    assert "in Origenerator mode" in _the_row_of("portrait_trash").detail
    assert "enhancement" in _the_row_of("satellites_cycle_version").detail


def test_the_main_funestras_step_and_hold_keys_reach_whatever_is_on_it():
    """M, the period and the comma step and hold what is on the Main Funestra
    in either mode, so they sit under its column of the shared grid; the
    brackets and the apostrophe, which stepped and held the video alone, are
    gone, and + is the nudge forward beside -: the key itself, no Shift, shown
    as the + it is thought of as."""
    assert _ahk_binding_for("main_prev") == "m"
    assert _ahk_binding_for("main_next") == "SC034"
    assert _ahk_binding_for("main_lock") == "SC033"
    assert _ahk_bindings_for("main_nudge_next") == ["=", "+="]
    for retired in ("main_kino_activate", "browse_library", "robot_hand_toggle_learned",
                    "genau_lock", "main_player_lock"):
        assert _ahk_binding_for(retired) is None, retired
    keyed = {cmd: row.key_columns[2] for row in _funestras_section().rows
             for cmd in row.commands if row.key_columns[2]}
    assert keyed["main_prev"] == ("M",)
    assert keyed["main_next"] == (".",)
    assert keyed["main_lock"] == (",",)
    assert _keys(_the_row_of("main_nudge_prev")) == ("-",)
    assert _keys(_the_row_of("main_nudge_next")) == ("+",)


def test_the_shifted_and_ctrl_pairs_followed_m_and_the_period():
    """The video's other versions and the Main Funestra's HUD walk on Shift
    and Ctrl with the same two keys the bare step uses, as the satellites'
    shifted and Ctrl pairs ride their own step keys."""
    assert _ahk_bindings_for("main_player_cycle_version_back") == ["+m"]
    assert _ahk_bindings_for("main_player_cycle_version") == ["+SC034"]
    assert _ahk_bindings_for("main_hud_counterclockwise") == ["^m"]
    assert _ahk_bindings_for("main_hud_clockwise") == ["^SC034"]
    assert not re.search(r"^[+^]?(\[|\]|SC01A|SC01B)::", script_text(), re.MULTILINE)
    assert _the_row_of("main_player_cycle_version_back").key_columns[2] == ("Shift+M", "Shift+.")
    hud = next(r for r in _funestras_section().rows if "main_hud_clockwise" in r.commands)
    assert hud.key_columns == (("Ctrl+Left", "Ctrl+Right", "Ctrl+Up", "Ctrl+Down"),
                               ("Ctrl+A", "Ctrl+D", "Ctrl+W", "Ctrl+S"),
                               ("Ctrl+M", "Ctrl+."))


def test_the_backslash_key_offsets_the_hand_and_the_browser_has_its_own_key():
    """The backslash offsets the Robot Hand's motion in either mode — it used to
    open the Main Funestra's library browser in kino mode, and that browser now has the key
    the retired mode had."""
    hand_backslash = [r for r in _section("OSR2").rows if "\\" in _keys(r)]
    assert [r.commands for r in hand_backslash] == [("quarter_button",)]
    assert not [r for r in _all_rows() if "\\" in _keys(r) and "quarter_button" not in r.commands]
    browser = [r for r in _section("Main Funestra").rows if "browse_library" in r.commands]
    assert len(browser) == 1 and _keys(browser[0]) == ()
    assert "browse" in browser[0].voice


def test_kino_mode_is_spoken_as_written_of_either_side_or_of_both():
    # Every word of these is in the vosk vocabulary, so they are heard as the
    # reference shows them.
    assert VOICE_COMMANDS["kino mode"] == "kino_activate"
    assert VOICE_COMMANDS["main kino mode"] == "main_kino_activate"
    assert VOICE_COMMANDS["satellite kino mode"] == "satellites_kino_activate"
    for retired in ("now now", "now mode", "hybrid", "hybrid mode", "player mode", "video mode"):
        assert retired not in VOICE_COMMANDS, retired
    [kino] = [r for r in _all_rows() if "kino_activate" in r.commands]
    assert {"main_kino_activate", "satellites_kino_activate"} <= set(kino.commands)
    assert kino.voice == ("kino", "kino mode")
    genau_rows = [r for r in _all_rows() if "genau_activate" in r.commands]
    assert genau_rows and genau_rows[0].voice == ("genau", "genau mode")
    [origenerator] = [r for r in _all_rows() if "satellites_origenerator_activate" in r.commands]
    assert origenerator.voice == ("origenerator", "origenerator mode")


def test_loop_control_row_consolidates_record_and_cancel():
    rows = _all_rows()
    loop_rows = [r for r in rows if "main_player_record_down" in r.commands]
    assert loop_rows, "expected a loop control row"
    row = loop_rows[0]
    assert "R" in _keys(row)
    assert "record" in row.voice
    assert "loop" in row.voice
    # Record and cancel are one row.  The cancel's phrase, "end loop", is no longer
    # this row's own: it is the side-agnostic phrase, and reaches the Main Funestra's loop through
    # the active-side resolution whenever the Main Funestra is the Funestra last addressed.
    assert "main_player_loop_cancel" in row.commands


def test_previous_shape_is_a_separate_keyless_line():
    rows = _all_rows()
    next_rows = [r for r in rows if "robot_hand_cycle_shape" in r.commands]
    prev_rows = [r for r in rows if "robot_hand_cycle_shape_prev" in r.commands]
    assert next_rows and _keys(next_rows[0]) == ("I",)
    # The "I" key does next only — it must not claim previous.
    assert "robot_hand_cycle_shape_prev" not in next_rows[0].commands
    assert prev_rows and _keys(prev_rows[0]) == ()
    assert "previous shape" in prev_rows[0].voice


def test_min_max_value_live_on_their_own_consecutive_set_lines():
    rows = _all_rows()
    amp_updown = next(r for r in rows if "robot_hand_amplitude_up" in r.commands)
    # The up/down line must NOT carry min/max/value phrases.
    assert not any(("min" in v or "max" in v or "0–100" in v) for v in amp_updown.voice)

    osr2_rows = _section("OSR2").rows
    descs = [r.description for r in osr2_rows]
    for updown, setname in (
        ("amplitude up / down", "set amplitude"),
        ("center up / down", "set center"),
        ("speed up / down", "set speed"),
    ):
        # Matched by prefix: a row may carry an explanation after its name (speed
        # says which engine the nudge reaches in kino mode).
        at = next(i for i, d in enumerate(descs) if d.startswith(updown))
        assert descs.index(setname) == at + 1, "Set line must follow its up/down line"
    set_amp = next(r for r in osr2_rows if r.description == "set amplitude")
    assert "min amp" in set_amp.voice and "max amp" in set_amp.voice


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


def test_offset_voice_on_genau_backslash_row():
    rows = _all_rows()
    offset_rows = [r for r in rows if "\\" in _keys(r) and "quarter_button" in r.commands]
    assert offset_rows and "offset" in offset_rows[0].voice


def test_the_clip_navigation_row_quotes_only_what_voice_answers():
    row = next(r for r in _all_rows() if "main_player_clip_jump" in r.commands)
    spoken = {phrase.lower() for phrase in VOICE_COMMANDS} | set(row.voice)

    quoted = set(re.findall(r'"([^"]+)"', row.detail))
    assert quoted and quoted <= spoken


def test_corrected_descriptions():
    """Rows say what the key does to the room, not just what it is called."""
    descs = {r.description for r in _all_rows()}
    assert {"disable voice control", "start / stop the Broker"} <= descs
    assert any(d.startswith("enable / disable human-inspired motion") for d in descs)
    assert "Mute voice control" not in descs
    assert "Broker start / stop" not in descs


_NAMES_AN_ACTION_MAY_OPEN_WITH = frozenset(
    {"OmniPause", "F-mode", "VR", "Kino", "Genau", "Origenerator", "OSR2"})


def test_an_action_opens_lowercase_unless_it_opens_with_a_name():
    for row in _all_rows():
        first = row.description.split()[0]
        assert first[0].islower() or first in _NAMES_AN_ACTION_MAY_OPEN_WITH, row.description


def test_the_dashboard_section_lists_its_buttons_in_the_bars_order():
    """Left to right along the bar: the session's own, the pair that reaches
    every Funestra, then the crossing into VR."""
    dashboard = _section("Dashboard")

    assert [(group.title, [row.description for row in group.rows]) for group in dashboard.groups] == [
        ("General", ["quit", "OmniPause / resume", "OmniPause", "relief OmniPause",
                     "open / close this hotkeys & voice reference", "disable voice control"]),
        ("Funestra", ["F-mode all", "reset all"]),
        ("VR", ["enter / exit VR"]),
    ]
    assert dashboard.rows[-1].detail == ""


def test_the_osr2s_control_is_one_row_park_and_its_undoing_word():
    """The switch's own words lead the row, park and unpark are their synonyms,
    and "OSR2 off" and "OSR2 resume", which he struck, are heard no more."""
    osr2 = _section("OSR2")
    [control] = osr2.groups[0].rows

    assert [group.title for group in osr2.groups] == ["Basic", "Robot Hand", "Broker"]
    assert (control.description, control.voice) == (
        "control on / off", ("OSR2 control off", "park", "park it", "OSR2 control on", "unpark"))
    assert set(control.commands) == {"robot_hand_park", "robot_hand_release"}
    assert "osr2_control_off" not in control.commands
    assert _keys(control) == ()


def test_one_row_relocates_the_hud_by_key_round_the_picture_and_by_name_to_a_corner():
    row = next(r for r in _funestras_section().rows if "main_hud_upper_left" in r.commands)

    assert row.description == "relocate the HUD"
    assert row.voice == ("hud top left", "hud upper left", "hud top right", "hud upper right",
                         "hud lower left", "hud bottom left", "hud lower right", "hud bottom right")
    assert "\"bottom\" the same as \"lower\"" in row.detail
    assert {"portrait_hud_lower_right", "landscape_hud_upper_right", "satellites_hud_lower_left",
            "active_hud_upper_right", "main_hud_lower_left", "portrait_hud_left",
            "landscape_hud_down", "main_hud_clockwise", "main_hud_counterclockwise"} <= set(row.commands)
    assert row.key_columns == (("Ctrl+Left", "Ctrl+Right", "Ctrl+Up", "Ctrl+Down"),
                               ("Ctrl+A", "Ctrl+D", "Ctrl+W", "Ctrl+S"),
                               ("Ctrl+M", "Ctrl+."))


def test_the_origenerator_section_speaks_origenerators_own_words():
    origenerator = _section("Origenerator")

    assert [group.title for group in origenerator.groups] == [""]
    assert [row.description for row in origenerator.rows] == [
        "play the experiments or requests", "play, pause or stop the slideshow",
        "fix the picture on screen", f"animate the picture as a Genau {genau_flick_noun().one}",
        "show only the enhanced pictures",
    ]
    assert not [row for row in _all_rows()
                if "show" in row.description.split() and row not in origenerator.rows]


def test_the_origenerator_column_folds_the_side_word_and_its_note_says_the_side_comes_first():
    """Origenerator's phrases are heard with the side in front and nowhere
    else -- no bare phrase, no "satellites", no side after -- so the column
    shows the phrase alone and the note on the title says how to say it."""
    origenerator = _section("Origenerator")
    flick = genau_flick_noun().one

    assert [row.voice for row in origenerator.rows] == [
        ("experiments", "requests"),
        ("play slideshow", "start slideshow", "pause slideshow", "stop slideshow"),
        ("fix face", "fix hands", "fix teeth", "fix eyes"),
        ("genau",),
        ("enhanced only", "filter enhanced"),
    ]
    assert origenerator.note == (
        "These are heard with the side first, every time: “portrait experiments”, "
        "“landscape fix hands”.  Said alone, with the side after, or of “satellites”, "
        "they are not heard."
    )
    assert VOICE_COMMANDS["portrait fix hands"] == "portrait_say_fix_hands"
    for not_a_phrase in ("fix hands", "fix hands portrait", "satellites fix hands", f"main {flick}"):
        assert not_a_phrase not in VOICE_COMMANDS, not_a_phrase


def test_the_nav_map_keeps_its_loop_cycle_beside_navigating_and_takes_the_filters():
    nav_map = _section("Nav Map")
    cycle = _the_row_of("portrait_loop")

    assert [group.title for group in nav_map.groups] == ["", "Loops", "Filter"]
    assert [row.description for row in nav_map.groups[0].rows] == [
        "cycle action", "cycle seed", "wrong action", "navigate the map", "cycle loop mode"]
    assert cycle.detail == "seeds, then actions, then off"
    assert [row.description for row in nav_map.groups[1].rows] == [
        "more seeds", "loop the actions", "loop the seeds", "stop looping"]
    assert [row.description for row in nav_map.groups[2].rows] == [
        "filter by act", "filter to the act on screen", "drop the filter"]
    assert [(group.title, [row.description for row in group.rows])
            for group in _satellite_section().groups] == [("", ["favorites"])]


def test_the_main_funestras_mode_groups_follow_its_consoles_mode_buttons():
    main = _section("Main Funestra")

    assert [group.title for group in main.groups] == ["", *(label for _, label, _ in MODE_BUTTONS)]
    assert [row.description for row in main.groups[0].rows] == [
        "open the browser", "included video types", "included video types (projection)"]


def test_the_included_lengths_are_said_as_the_included_shapes_are():
    """The console's two pairs of buttons, clips / full and flat / VR, are
    the two "included video types" rows, each said as what it includes."""
    [lengths] = [row for row in _all_rows() if "main_player_length_clips" in row.commands]
    [shapes] = [row for row in _all_rows() if "main_projection_flat" in row.commands]

    assert lengths.description == "included video types"
    assert lengths.voice == ("clips only", "full only", "clips and full", "full and clips")
    assert "in Kino mode" in lengths.detail
    assert shapes.description == "included video types (projection)"
    assert set(shapes.voice) == {"flat only", "2D only", "VR only", "flat and VR", "VR and flat"}
    assert lengths not in _group("Main Funestra", "Kino").rows


def test_the_column_heads_say_hotkey_and_voice_command_and_a_forked_hotkey_head_spans_its_funestras():
    html = render_reference_html(1000)

    assert (HOTKEY_HEADER, VOICE_HEADER) == ("Hotkey", "Voice command")
    assert ">Say<" not in html and ">Key<" not in html
    assert re.search(r'colspan="3"[^>]*>\s*<b[^>]*>Hotkey</b>', html)
    assert re.search(r'colspan="2"[^>]*>\s*<b[^>]*>Hotkey</b>', html)
    forked = re.search(r'<tr[^>]*>(?:<td[^>]*><b[^>]*>(Portrait|Landscape|Main)</b></td>){3}</tr>', html)
    assert forked, "the Funestras' names sit under the spanning Hotkey head"


def _section_html(title: str) -> str:
    [part] = [part for part in re.split(r"(?=<h3 )", render_reference_html(1000))
              if re.match(rf"<h3[^>]*>(?:<a[^>]*>)?{re.escape(title)}</", part)]
    return part


def _group_htmls(section_title: str) -> dict[str, str]:
    """Each group's HTML by its title, an untitled group under ""."""
    untitled, *titled = re.split(r"<h4[^>]*>", _section_html(section_title))
    groups = dict(chunk.split("</h4>", 1) for chunk in titled)
    if "<table" in untitled:
        groups[""] = untitled
    return groups


def test_a_group_whose_rows_hold_no_key_renders_no_hotkey_column():
    origenerator = _section_html("Origenerator")
    assert re.findall(r"<b[^>]*>([^<]+)</b>", origenerator) == ["Action", "Voice command"]
    assert origenerator.count("<td") == 2 * origenerator.count("<tr")
    vr = _section_html("VR")
    assert "Hotkey" not in vr
    assert vr.count("<td") == 3 * vr.count("<tr")
    funestras = _group_htmls("Funestras")
    assert "Hotkey" in funestras["Navigation"] and "Portrait" in funestras["Navigation"]
    assert "Hotkey" not in funestras["Playlist"] and "Portrait" not in funestras["Playlist"]
    assert "Hotkey" in _section_html("Other")


def _tables(html: str) -> list[str]:
    return re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL)


def _body_widths(table: str) -> list[int]:
    """Each column's pixel width, read off the table's last row."""
    last_row = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)[-1]
    return [int(width) for width in re.findall(r'<td width="(\d+)"', last_row)]


def test_every_table_is_the_page_wide_with_its_voice_column_and_hotkey_block_in_the_same_pixels():
    """Qt lays a percentage-width table out a few pixels past the viewport, and
    the columns of one table land at an x of their own, so each table is told
    its width in pixels and each cell its own: the voice column at the right and
    the hotkey block -- one column, or a column per Funestra -- the same width on
    every table, the controller column its own where a table has one, and the
    action column taking whatever the page leaves."""
    html = render_reference_html(1000)
    tables = _tables(html)

    assert tables
    assert {re.match(r'<table width="(\d+)"', table).group(1) for table in tables} == {"1000"}
    for table in tables:
        widths = _body_widths(table)
        assert sum(widths) == 1000, table[:120]
        assert widths[-1] == VOICE_COLUMN_PX, table[:120]
        between = widths[1:-1]
        if CONTROLLER_HEADER in table:
            assert between[-1] == CONTROLLER_COLUMN_PX, table[:120]
            between = between[:-1]
        assert sum(between) in (0, HOTKEY_BLOCK_PX), table[:120]
    per_funestra = _body_widths(next(table for table in tables if "Portrait" in table))
    assert per_funestra[1:4] == [HOTKEY_BLOCK_PX // 3] * 3
    assert _body_widths(_tables(_section_html("Dashboard"))[0])[1:-1] == [HOTKEY_BLOCK_PX]
    assert len(_body_widths(_tables(_section_html("Origenerator"))[0])) == 2
    assert _body_widths(_tables(render_reference_html(1400))[0])[0] == 1400 - VOICE_COLUMN_PX - HOTKEY_BLOCK_PX


def test_a_table_shows_the_controller_column_only_where_a_row_has_a_controller_input():
    """Between the hotkey block and the voice column, headed Controller, in
    plain words; the action column gives up its width, so the voice column
    stays at the same x as on the tables without it."""
    vr, navigation = _section_html("VR"), _group_htmls("Funestras")["Navigation"]
    playlist, origenerator = _group_htmls("Funestras")["Playlist"], _section_html("Origenerator")

    assert re.findall(r"<b[^>]*>([^<]+)</b>", _tables(vr)[0])[:3] == ["Action", CONTROLLER_HEADER, VOICE_HEADER]
    assert re.findall(r"<b[^>]*>([^<]+)</b>", navigation) == [
        "Action", HOTKEY_HEADER, CONTROLLER_HEADER, VOICE_HEADER, "Portrait", "Landscape", "Main"]
    assert re.search(rf'<td[^>]*rowspan="2"[^>]*>\s*<b[^>]*>{CONTROLLER_HEADER}</b>', navigation)
    assert CONTROLLER_HEADER not in playlist and CONTROLLER_HEADER not in origenerator
    assert f">{B_OR_Y}<" in navigation and f">{STICK_SIDEWAYS}<" in vr


def test_the_controller_column_says_what_the_headsets_controllers_do_on_each_row():
    """Read off the headset's code: A or X and B or Y skip the Main Funestra 10
    seconds, and jump between its video's scenes with the trigger held; the
    stick pushed sideways steps the projection; a squeeze beside a side of a
    Funestra relocates its HUD; a squeeze held while the controller moves turns
    and tilts the screens; a squeeze on a screen's handle carries or resizes it
    and the stick sizes the Main Funestra; in the browser a squeeze opens a tile
    and the stick scrolls."""
    by_input = {}
    for row in _all_rows():
        for word in row.controller:
            by_input.setdefault(word, []).append(row.description)

    assert by_input == {
        A_OR_X: ["nudge forward 10 seconds"],
        B_OR_Y: ["nudge back 10 seconds"],
        STICK_SIDEWAYS: ["cycle the video's VR projection"],
        STICK_UP_DOWN: ["move or size a VR screen", "open the browser"],
        SQUEEZE_AND_MOVE: ["tilt the VR screens up / down and level them again"],
        TRIGGER: ["move or size a VR screen", "relocate the HUD", "open the browser"],
    }
    assert "scene" in _the_row_of("main_nudge_next").detail
    assert "handle" in next(row for row in _section("VR").rows
                            if row.description == "move or size a VR screen").detail
    assert "squeeze" in _the_row_of("browse_library").detail


def test_the_forked_hotkey_head_is_centered_over_its_funestras():
    html = render_reference_html(1000)
    for columns in (3, 2):
        assert re.search(
            rf'<td(?=[^>]*colspan="{columns}")(?=[^>]*align="center")[^>]*>\s*<b[^>]*>Hotkey</b>',
            html), columns


def test_every_column_head_sits_in_the_middle_of_its_cell_and_the_forked_one_is_underlined():
    """The forked Hotkey head is a line under which its Funestras' names sit, so
    a thin rule runs under it across their columns; every head is centered both
    ways, since a two-line head beside a one-line head otherwise reads top-left."""
    html = render_reference_html(1000)
    heads = re.findall(r"<td[^>]*>\s*<b[^>]*>(?:Action|Hotkey|Voice command|Portrait|Landscape|Main)</b></td>",
                       html)
    forked = [head for head in heads if "colspan" in head]

    assert heads and forked
    for head in heads:
        assert 'align="center"' in head and 'valign="middle"' in head, head
        assert (f"border-bottom:1px solid {_BORDER}" in head) == (head in forked), head
    assert "border-top" not in html


def test_the_funestras_note_aims_a_phrase_and_says_genaus_word_for_a_video():
    flick = genau_flick_noun().one

    assert _funestras_section().note == (
        "Alone, a phrase reaches whatever you last navigated.  Add “portrait”, "
        "“landscape”, “main”, or “satellites”, before it or after, to aim it: "
        f"“portrait next”, “next main”.  In Genau mode say “{flick}” in place of “video”."
    )


def test_an_untitled_group_renders_no_heading_and_a_flat_section_is_its_table():
    for title in ("VR", "Origenerator", "Other"):
        assert "<h4" not in _section_html(title), title
    main = _section_html("Main Funestra")
    assert main.index("<table") < main.index("<h4")
    assert set(_group_htmls("Main Funestra")) == {"", "Kino", "Genau"}


def test_a_sections_note_is_the_tooltip_on_its_title():
    """The scoping rule no longer sits on the page under the title: the title
    is the anchor, and the note waits in its tooltip like a row's detail."""
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


def test_relief_omnipause_row_shows_shift_esc_and_the_single_word_name():
    """Shift+Esc is documented next to plain Esc, and its phrase reads as one
    word — vosk has no "omnipause" token, so the recognizer listens for the
    three-word "relief omni pause" while the legend shows "relief omnipause"."""
    assert VOICE_COMMANDS["relief omni pause"] == "relief_omnipause"
    assert "relief omnipause" not in VOICE_COMMANDS  # display-only, not a recognizer phrase

    rows = [r for r in _all_rows() if "relief_omnipause" in r.commands]
    assert len(rows) == 1, "expected exactly one relief row"
    row = rows[0]
    assert _keys(row) == ("Shift+Esc",)
    assert row.voice == ("relief omnipause", "stop")


def test_relief_answers_to_one_word_as_well_as_its_full_name():
    """Three words is a lot to get out in the moment the command is for, so the
    obvious single word reaches it too."""
    assert VOICE_COMMANDS["stop"] == "relief_omnipause"


def test_relief_survives_the_omnipause_suspension_on_both_input_paths():
    """Relief has to fire from inside omnipause — a paused session is exactly
    where the device may still be on the user — so Shift+Esc sits in the AHK
    #SuspendExempt block and its command is exempt from the voice freeze too.
    Either half missing leaves the emergency dead in the one state it is for."""
    assert "relief_omnipause" in _ahk_suspend_exempt_commands()
    assert "relief_omnipause" in SUSPEND_EXEMPT_COMMANDS


def test_a_paused_room_hears_what_he_ruled_and_nothing_else():
    """The whole set, so that widening or narrowing it fails here: the three
    ways out on 2026-09-28, and the headset's four on 2026-10-16.  See
    CLAUDE.md, "Standing rules"."""
    assert frozenset({
        "play", "quit", "relief_omnipause",
        "enter_vr", "exit_vr", "recenter_view", "vr_reset",
    }) == SUSPEND_EXEMPT_COMMANDS


def test_no_say_column_leaks_the_raw_omni_pause_form():
    for row in _all_rows():
        for phrase in row.voice:
            assert "omni pause" not in phrase, phrase


def test_funscript_row_shows_the_joined_word_the_recognizer_cannot_hear():
    """The small vosk model has no "funscript" token, so the recognizer listens
    for "fun script" — but nobody says it that way, and reading it in the popup
    would teach the wrong phrase."""
    assert VOICE_COMMANDS["jump to fun script"] == "main_player_funscript_jump"
    assert "jump to funscript" not in VOICE_COMMANDS  # display-only

    jump, next_scripted = _the_row_of("main_player_funscript_jump"), _the_row_of("main_player_next_funscripted")
    assert (jump.description, jump.voice) == ("jump to funscript", ("jump to funscript",))
    assert (next_scripted.description, next_scripted.voice) == ("next funscripted", ("next funscripted",))
    assert jump.detail and next_scripted.detail
    assert _keys(jump) == _keys(next_scripted) == ()
    for row in _all_rows():
        for phrase in row.voice:
            assert "fun script" not in phrase, phrase


def test_reference_does_not_import_voice_runtime():
    """Importing the reference must not drag in voice_control (and its vosk runtime).

    The dashboard process imports command_reference; it should stay free of the
    speech-recognition libraries, which only the orchestrator's VoiceController needs.
    """
    code = (
        "import fun_time.command_reference, sys; "
        "bad = [m for m in sys.modules if m in ('fun_time.voice_control', 'vosk', 'sounddevice')]; "
        "assert not bad, bad"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_render_reference_html_contains_key_content():
    html = render_reference_html(1000)
    assert isinstance(html, str)
    assert "<table" in html
    # A hotkey, a voice phrase, and a section title should all be present.
    assert "Esc" in html
    assert "genau" in html
    assert "Genau" in html
    # No raw template gaps — an unfilled {placeholder} surviving into the
    # output.  Only that shape is banned: a blanket brace ban would outlaw
    # any future <style> block or inline script for no reason of its own.
    assert not re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", html)


def test_render_reference_html_has_no_heading_or_subtitle():
    """The popup's name lives on the window chrome, so the rendered HTML carries
    neither an in-window heading nor the old subtitle — it opens straight into
    the first section."""
    html = render_reference_html(1000)
    assert "<h2" not in html
    assert "Hotkeys &amp; Voice Commands Reference" not in html
    assert "Global while Fun Time" not in html


def test_the_spoken_filters_are_two_rows_of_the_satellite_grid():
    """Filtering is a satellite action like any other, so it stops being its own
    section: one row sets a filter by act, one drops it.  Neither spells out
    "portrait"/"landscape" any more — the section's note aims them, the same way
    it aims "next"."""
    rows = _group("Nav Map", "Filter").rows
    set_row = next(r for r in rows if r.description.startswith("filter by act"))
    # The acts read under their real names, not the sound-alikes the grammar is
    # built from: an act whose word the speech model has no token for is *heard*
    # as something else, and printing that would teach the reader the wrong word.
    assert set(set_row.voice) == set(display_forms())
    spoken = {form for forms in load_acts().values() for form in forms}
    workarounds = spoken - set(display_forms())
    # No demand that this overlay HAS a workaround — that mechanic is pinned
    # on fixture data in test_filter_vocab — only that none it does have leaks.
    assert not workarounds & set(set_row.voice), workarounds
    # Clearing scopes like every other action now, so its phrases derive with the
    # rest and the side word never appears: "portrait clear filter", not "clear
    # portrait".
    drop_row = next(r for r in rows if r.description.startswith("drop the filter"))
    assert set(drop_row.voice) == {"no filter", "filter off", "clear filter", "show everything"}
    assert VOICE_COMMANDS["portrait clear filter"] == "portrait_no_filter"
    assert VOICE_COMMANDS["clear filter landscape"] == "landscape_no_filter"
    assert VOICE_COMMANDS["satellites show everything"] == "satellites_no_filter"
    assert VOICE_COMMANDS["clear filter"] == "active_no_filter"
    assert "clear portrait" not in VOICE_COMMANDS


def test_every_filter_voice_command_is_represented():
    covered = _covered_commands()
    missing = {cmd for cmd in filter_voice_commands().values() if cmd not in covered}
    assert not missing, f"filter commands missing from reference: {sorted(missing)}"
