from __future__ import annotations

import importlib
import re
import subprocess
import sys

from fun_time import command_reference, content
from fun_time.command_dispatch import _MAIN_FUNESTRA_CMD_MAP, _about_genaus_flick
from fun_time.command_reference import (
    _SECTIONS,
    HOTKEY_HEADER,
    VOICE_HEADER,
    CommandRef,
    ReferenceSection,
    _collapse_scopes,
    _display_voice,
    build_reference_sections,
    reference_details,
    render_reference_html,
)
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
            assert _keys(row) or row.voice, f"row {row.description!r} has no trigger"


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
        for row in _one_row_per_satellite_mode(cmd):
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
        for row in _one_row_per_satellite_mode(cmd):
            # The sides share a row, each key under its own column.
            assert row.key_columns[column] == (key,)
    for cmd in ("portrait_no_loop", "landscape_no_loop"):
        assert _ahk_binding_for(cmd) is None, f"{cmd} gave its key to the cycle"
        for row in _one_row_per_satellite_mode(cmd):
            assert _keys(row) == ()


def _section(title: str) -> ReferenceSection:
    return {s.title: s for s in build_reference_sections()}[title]


def _satellite_sections() -> tuple[ReferenceSection, ...]:
    return tuple(s for s in build_reference_sections() if s.title.startswith("Satellite Funestras"))


def _satellite_mode_sections() -> tuple[ReferenceSection, ...]:
    return tuple(s for s in _satellite_sections() if not s.title.endswith("General"))


def _satellite_section() -> ReferenceSection:
    return _section("Satellite Funestras - Kino")


def _funestras_section() -> ReferenceSection:
    """What every Funestra answers, a key column per Funestra."""
    return _section("Funestras")


def _shared_sections() -> tuple[ReferenceSection, ...]:
    return (_funestras_section(), *_satellite_sections())


def _one_row_per_satellite_mode(cmd: str) -> list[CommandRef]:
    """What a satellite's keys and words do differs by its mode, so a command
    of theirs has one row in each mode's section and none anywhere else."""
    owning = [row for row in _all_rows() if cmd in row.commands]
    per_section = [[row for row in section.rows if cmd in row.commands]
                   for section in _satellite_mode_sections()]
    assert [len(rows) for rows in per_section] == [1] * len(per_section), cmd
    assert len(owning) == len(per_section), f"{cmd} has a row outside the mode sections"
    return owning


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
                for scope in ("portrait", "landscape", "both", "active")
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
    """A row cannot spell out "portrait next", "next landscape", "both next" and
    "next" without printing its own action four times, so the Say column keeps the
    action alone and the section's note explains how to aim it."""
    section = _funestras_section()
    nxt = next(r for r in section.rows if "portrait_next" in r.commands)
    assert nxt.voice == ("next",)
    assert nxt.key_columns == (("Right",), ("D",), (".",))
    lock = next(r for r in section.rows if "portrait_lock_on" in r.commands)
    assert lock.voice == ("lock", "unlock")
    assert "main_lock_on" in lock.commands
    sayable_bare = {friendly_voice(phrase) for phrase in VOICE_COMMANDS}
    for section in _shared_sections():
        for word in ("portrait", "landscape", "both"):
            assert word in section.note
        for group in section.groups:
            if group.title == "Slideshow":
                continue  # said with the side on purpose: bare "genau" is Genau mode
            for row in group.rows:
                if row.description.startswith(("filter by act", "drop ")):
                    continue  # the filter phrases scope themselves differently; see below
                for phrase in row.voice:
                    if _collapse_scopes(phrase) not in sayable_bare:
                        continue
                    assert not {"portrait", "landscape", "both", "main"} & set(phrase.split()), (
                        f"{phrase!r} still carries a scope word"
                    )


def test_the_grid_carries_every_both_and_active_command():
    """Nothing was dropped in the consolidation: each command the retired "Both",
    "Active side" and "Filters" sections documented is on a row of the grid."""
    covered = {c for section in _shared_sections() for row in section.rows for c in row.commands}
    assert {
        "both_prev", "both_next", "both_trash",
        "both_lock_on", "both_lock_off",
        "both_cycle_action", "both_cycle_seed", "both_more_seeds", "both_wrong_action",
        "both_action_loop", "both_seed_loop", "both_no_loop", "both_lock_action",
        "both_latest", "both_shuffle", "both_no_filter", "both_reset",
        "both_fmode", "both_fmode_on", "both_fmode_off",
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
    rows = _funestras_section().rows + _satellite_section().rows
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


def test_the_f_mode_row_offers_favorites_only_with_a_side_named():
    f_mode = next(r for r in _funestras_section().rows if "active_fmode" in r.commands)

    assert {"portrait favorites", "landscape favorites"} <= set(f_mode.voice)
    assert "favorites" not in f_mode.voice


def test_the_shared_grid_carries_every_funestras_own_playback_speed():
    rows = _funestras_section().rows
    nudge = next(r for r in rows if "portrait_speed_up" in r.commands)
    rate = next(r for r in rows if "portrait_speed_50" in r.commands)

    assert nudge.voice == ("playback speed up", "playback slow down", "playback speed down")
    assert {"active_speed_up", "both_speed_down", "landscape_speed_up",
            "main_player_speed_up", "main_player_speed_down"} <= set(nudge.commands)
    assert {"active_speed_min", "both_speed_max", "active_speed_150", "both_speed_200",
            "landscape_speed_25", "main_player_speed_50"} <= set(rate.commands)


def test_the_reference_calls_the_mode_kino_and_never_video():
    rows = _all_rows()
    said = [(row.description, phrase) for row in rows for phrase in (row.description, *row.voice)
            if "video mode" in phrase.lower()]

    assert said == []
    assert [row.description for row in rows if "kino_activate" in row.commands] == ["Kino mode"]


def test_mode_named_nav_shows_friendly_names_in_the_legend():
    """The Kino/Genau nav rows surface the mode-named phrases under their friendly
    names ("kino next", "genau next") — never the raw vosk sound-alike ("go
    now")."""
    rows = _all_rows()
    kino_next_row = next(r for r in rows if "kino_next" in r.commands)
    assert {"kino next", "next kino"} <= set(kino_next_row.voice)
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
    assert (prev.voice, nxt.voice) == (("previous",), ("next",))
    assert (prev.key_columns[2], nxt.key_columns[2]) == (("M",), (".",))
    assert "“next main”" in _funestras_section().note


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
    assert {"audio_unmute", "portrait_mute", "landscape_unmute", "both_mute",
            "active_unmute"} <= set(mute.commands)

    steps = [r for r in _all_rows() if "audio_volume_up" in r.commands]
    assert steps == [r for r in rows if "audio_volume_up" in r.commands]
    assert len(steps) == 1, "expected one volume row"
    assert {"audio_volume_down", "portrait_volume_up", "landscape_volume_down",
            "both_volume_up", "active_volume_down"} <= set(steps[0].commands)
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
    [row] = [r for r in _all_rows() if "both_latest" in r.commands]
    assert _keys(row) == ()
    # It sits in the shared grid like every other action, so its voice column
    # is the bare "latest" and "both latest" comes from the section's note.
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
    assert VOICE_COMMANDS["both end loop"] == "both_no_loop"


def test_latest_and_shuffle_reach_one_side_or_both():
    """Both orderings were global-only, so "portrait premiere" parsed as nothing.
    They join the side grid like every other satellite action — and shuffle has to
    come too, or a side put in latest order could never be shuffled back alone."""
    for word, action in (("latest", "latest"), ("shuffle", "shuffle")):
        assert VOICE_COMMANDS[word] == f"active_{action}"
        assert VOICE_COMMANDS[f"portrait {word}"] == f"portrait_{action}"
        assert VOICE_COMMANDS[f"{word} landscape"] == f"landscape_{action}"
        assert VOICE_COMMANDS[f"both {word}"] == f"both_{action}"


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
    genau = _section("Main Funestra - Genau")
    seconds = next(row for row in genau.rows if "genau_flick_seconds_up" in row.commands)

    assert [row.description for row in genau.rows[:2]] == [
        f"previous Genau {flick.one}", f"next Genau {flick.one}"]
    assert seconds.voice == (f"{flick.one} seconds 1–60",)
    assert [row.description for row in genau.rows if re.search(r"\bclips?\b", row.description)] == []
    assert [row.description for row in _all_rows()
            if re.search(r"Genau(?:'s)? clips?\b", row.description)] == []


def test_genau_mode_row_lists_genau_phrase_and_g_key():
    rows = _all_rows()
    genau_rows = [r for r in rows if "genau_activate" in r.commands]
    assert genau_rows, "expected a row for genau_activate"
    row = genau_rows[0]
    assert "genau" in row.voice
    assert "genau mode" in row.voice
    assert "go now" not in row.voice
    assert any(key.lower() == "g" for key in _keys(row))


def test_the_sections_are_the_dashboard_the_room_the_osr2_every_funestra_and_each_with_its_modes():
    """The dashboard's buttons in their order, what is left of the room, VR, the
    OSR2, what every Funestra answers, then the Main Funestra and the two
    Satellite Funestras, each with the rows that hold in both of its modes
    first and one section per mode after."""
    titles = [s.title for s in build_reference_sections()]
    assert titles == [
        "Dashboard",
        "General",
        "VR",
        "OSR2",
        "Funestras",
        "Main Funestra - General",
        "Main Funestra - Kino",
        "Main Funestra - Genau",
        "Satellite Funestras - General",
        "Satellite Funestras - Kino",
        "Satellite Funestras - Origenerator",
    ]


def test_every_section_is_read_in_groups_of_a_few_rows_each():
    """Twenty rows under one heading is a wall; each section's rows come in
    titled groups, and no section is one group of everything."""
    for section in build_reference_sections():
        assert section.groups, section.title
        assert all(group.title and group.rows for group in section.groups), section.title
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


def test_the_genau_groups_title_takes_the_overlays_word_like_its_rows(monkeypatch):
    monkeypatch.setattr(content, "genau_flick_noun", lambda: Noun(one="bloop", many="bloops"))
    try:
        genau = next(section for section in importlib.reload(command_reference).build_reference_sections()
                     if section.title == "Main Funestra - Genau")
        assert (genau.groups[0].title, genau.groups[0].rows[0].description) == ("Bloops", "previous Genau bloop")
    finally:
        monkeypatch.undo()
        importlib.reload(command_reference)



def test_genaus_flick_commands_are_the_genau_mode_section_and_nothing_else_is():
    """The dispatch ignores a flick command in Kino mode, so the rows it owns
    are the Genau-mode section's, and nothing that holds in both modes or in
    Kino mode is there."""
    in_genau = {cmd for row in _section("Main Funestra - Genau").rows for cmd in row.commands}
    elsewhere = {cmd for section in build_reference_sections()
                 if section.title != "Main Funestra - Genau"
                 for row in section.rows for cmd in row.commands}

    assert in_genau and all(_about_genaus_flick(cmd) for cmd in in_genau)
    assert not [cmd for cmd in elsewhere if _about_genaus_flick(cmd)]


def test_what_only_kino_answers_is_the_kino_mode_section_and_nothing_else_is():
    """The dispatch ignores these in Genau mode -- the video's own lock, loop,
    versions, length and jumps, and the clip save -- so their rows are the
    Kino-mode section's alone."""
    kino_only = set(_MAIN_FUNESTRA_CMD_MAP) | {"genaumacher_save", "kino_prev", "kino_next"}
    in_kino = {cmd for row in _section("Main Funestra - Kino").rows for cmd in row.commands}
    elsewhere = {cmd for section in build_reference_sections()
                 if section.title != "Main Funestra - Kino"
                 for row in section.rows for cmd in row.commands}

    assert kino_only <= in_kino
    assert not kino_only & elsewhere


def test_a_satellite_key_means_something_in_each_mode_and_sits_on_the_same_key():
    """What a satellite's key does changes with the side's mode -- a clip in
    Kino mode, a slide of the slideshow in Origenerator mode -- and the key itself
    does not, so each mode's section carries the key under the same command."""
    keyed = {}
    for section in _satellite_mode_sections():
        keyed[section.title] = {cmd: row.key_columns for row in section.rows
                                for cmd in row.commands if _keys(row)}
    kino, origenerator = keyed.values()

    assert kino == origenerator
    assert {"landscape_trash", "portrait_nav_up", "landscape_loop", "both_cycle_version"} <= set(kino)


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
                    "genau_lock", "main_player_lock", "kino_prev", "kino_next"):
        assert _ahk_binding_for(retired) is None, retired
    keyed = {cmd: row.key_columns[2] for row in _funestras_section().rows
             for cmd in row.commands if row.key_columns[2]}
    assert keyed["main_prev"] == ("M",)
    assert keyed["main_next"] == (".",)
    assert keyed["main_lock"] == (",",)
    assert keyed["main_nudge_prev"] == ("-",)
    assert keyed["main_nudge_next"] == ("+",)


def test_the_shifted_and_ctrl_pairs_followed_m_and_the_period():
    """The video's other versions and the Main Funestra's HUD walk on Shift
    and Ctrl with the same two keys the bare step uses, as the satellites'
    shifted and Ctrl pairs ride their own step keys."""
    assert _ahk_bindings_for("main_player_cycle_version_back") == ["+m"]
    assert _ahk_bindings_for("main_player_cycle_version") == ["v", "+SC034"]
    assert _ahk_bindings_for("main_hud_counterclockwise") == ["^m"]
    assert _ahk_bindings_for("main_hud_clockwise") == ["^SC034"]
    assert not re.search(r"^[+^]?(\[|\]|SC01A|SC01B)::", script_text(), re.MULTILINE)
    versions = next(r for r in _section("Main Funestra - Kino").rows
                    if "main_player_cycle_version_back" in r.commands)
    assert _keys(versions) == ("Shift+M", "Shift+.", "V")
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
    browser = [r for r in _section("Main Funestra - General").rows if "browse_library" in r.commands]
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
    main_rows = [r for r in _all_rows() if "main_kino_activate" in r.commands]
    assert main_rows and main_rows[0].voice == ("main kino mode",)
    both_rows = [r for r in _all_rows() if "kino_activate" in r.commands]
    assert both_rows and both_rows[0].voice == ("kino mode",)
    genau_rows = [r for r in _all_rows() if "genau_activate" in r.commands]
    assert genau_rows and genau_rows[0].voice == ("genau", "genau mode")


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


def test_cruise_voice_lists_on_before_off():
    cruise = next(r for r in _all_rows() if "robot_hand_toggle_cruise" in r.commands)
    assert cruise.voice == ("cruise control", "CC on", "cruise on", "CC off", "cruise off")


def test_learned_motion_follows_cruise_with_the_same_shape_of_voice():
    rows = _all_rows()
    cruise = next(i for i, r in enumerate(rows) if "robot_hand_toggle_cruise" in r.commands)
    learned = rows[cruise + 1]
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
    assert any(d.startswith("enable / disable cruise control") for d in descs)
    assert "Mute voice control" not in descs
    assert "Cruise control" not in descs
    assert "Broker start / stop" not in descs


_NAMES_AN_ACTION_MAY_OPEN_WITH = frozenset({"OmniPause", "F-mode", "VR", "Kino", "Genau"})


def test_an_action_opens_lowercase_unless_it_opens_with_a_name():
    for row in _all_rows():
        first = row.description.split()[0]
        assert first[0].islower() or first in _NAMES_AN_ACTION_MAY_OPEN_WITH, row.description


def test_the_dashboard_section_lists_its_buttons_in_the_bars_order():
    """Left to right along the bar: the session's own, the pair that reaches
    every Funestra, then the crossing into VR."""
    dashboard = _section("Dashboard")

    assert [(group.title, [row.description for row in group.rows]) for group in dashboard.groups] == [
        ("Session", ["quit", "OmniPause / resume", "OmniPause", "relief OmniPause",
                     "open / close this hotkeys & voice reference", "disable voice control"]),
        ("Funestra", ["F-mode all", "reset all"]),
        ("VR", ["enter VR / exit VR"]),
    ]
    assert dashboard.rows[-1].detail == ""


def test_the_osr2_holds_are_rows_of_their_own_each_with_its_undoing_word():
    osr2 = _section("OSR2")
    basic = osr2.groups[0]

    assert [group.title for group in osr2.groups] == ["Basic", "Robot Hand", "Broker"]
    assert [(row.description, row.voice) for row in basic.rows] == [
        ("park", ("park", "park it", "unpark")),
        ("retract", ("retract", "unretract")),
        ("resume", ("OSR2 resume",)),
        ("control off", ("OSR2 off",)),
    ]
    assert all("robot_hand_release" in row.commands for row in basic.rows[:3])


def test_a_hud_corner_row_names_the_four_corners_for_every_funestra():
    row = next(r for r in _funestras_section().rows if "main_hud_upper_left" in r.commands)

    assert row.voice == ("hud top left", "hud top right", "hud lower left", "hud lower right")
    assert {"portrait_hud_lower_right", "landscape_hud_upper_right", "both_hud_lower_left",
            "active_hud_upper_right", "main_hud_lower_left"} <= set(row.commands)
    assert _keys(row) == ()


def test_the_slideshow_group_speaks_origenerators_own_words():
    origenerator = _section("Satellite Funestras - Origenerator")
    slideshow = origenerator.groups[-1]

    assert slideshow.title == "Slideshow"
    assert [row.description for row in slideshow.rows] == [
        "play a shelf", "play, pause or stop the slideshow", "fix the picture on screen",
        f"animate the picture as a Genau {genau_flick_noun().one}", "show only the enhanced pictures",
    ]
    assert not [row for section in build_reference_sections() for row in section.rows
                if "show" in row.description.split() and row not in slideshow.rows]


def test_the_column_heads_say_hotkey_and_voice_command_and_a_forked_hotkey_head_spans_its_funestras():
    html = render_reference_html()

    assert (HOTKEY_HEADER, VOICE_HEADER) == ("Hotkey", "Voice command")
    assert ">Say<" not in html and ">Key<" not in html
    assert re.search(r'colspan="3"[^>]*>\s*<b[^>]*>Hotkey</b>', html)
    assert re.search(r'colspan="2"[^>]*>\s*<b[^>]*>Hotkey</b>', html)
    forked = re.search(r'<tr[^>]*>(?:<td[^>]*><b[^>]*>(Portrait|Landscape|Main)</b></td>){3}</tr>', html)
    assert forked, "the Funestras' names sit under the spanning Hotkey head"


def test_a_sections_note_is_the_tooltip_on_its_title():
    """The scoping rule no longer sits on the page under the title: the title
    is the anchor, and the note waits in its tooltip like a row's detail."""
    html = render_reference_html()
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

    rows = [r for r in _all_rows() if "main_player_funscript_jump" in r.commands]
    assert len(rows) == 1, "expected exactly one funscript navigation row"
    assert rows[0].voice == ("jump to funscript", "next funscripted")
    assert _keys(rows[0]) == ()
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
    html = render_reference_html()
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
    html = render_reference_html()
    assert "<h2" not in html
    assert "Hotkeys &amp; Voice Commands Reference" not in html
    assert "Global while Fun Time" not in html


def test_the_spoken_filters_are_two_rows_of_the_satellite_grid():
    """Filtering is a satellite action like any other, so it stops being its own
    section: one row sets a filter by act, one drops it.  Neither spells out
    "portrait"/"landscape" any more — the section's note aims them, the same way
    it aims "next"."""
    rows = _satellite_section().rows
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
    assert VOICE_COMMANDS["both show everything"] == "both_no_filter"
    assert VOICE_COMMANDS["clear filter"] == "active_no_filter"
    assert "clear portrait" not in VOICE_COMMANDS


def test_every_filter_voice_command_is_represented():
    covered = _covered_commands()
    missing = {cmd for cmd in filter_voice_commands().values() if cmd not in covered}
    assert not missing, f"filter commands missing from reference: {sorted(missing)}"
