from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from fun_time.command_reference import (
    CommandRef,
    ReferenceSection,
    build_reference_sections,
    render_reference_html,
)
from fun_time.voice_commands import VOICE_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NUMERIC_RE = re.compile(r"^genau_(amp|center|speed|clip_seconds)_\d+$")
_QUEUED_RE = re.compile(r'QueueCommand\("([^"]+)"\)')


def _ahk_script() -> str:
    return (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")


def _ahk_hotkey_commands() -> set[str]:
    """Every command bound to a key via QueueCommand() in the AHK hotkey script."""
    return set(_QUEUED_RE.findall(_ahk_script()))


def _ahk_binding_for(command: str) -> str | None:
    """The key the AHK script binds to ``command``, as the script spells it."""
    match = re.search(
        rf'^(\S+)::QueueCommand\("{re.escape(command)}"\)', _ahk_script(), re.MULTILINE
    )
    return match.group(1) if match else None


def _ahk_suspend_exempt_commands() -> set[str]:
    """The commands bound inside the script's ``#SuspendExempt`` block.

    Omnipause suspends the hotkeys wholesale, so these are the only keys that
    still reach Python while the session is paused.
    """
    block = _ahk_script().split("#SuspendExempt true", 1)[1].split("#SuspendExempt false", 1)[0]
    return set(_QUEUED_RE.findall(block))


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
    rows = _all_rows()
    queued = _ahk_hotkey_commands()
    for cmd, (phrase, shown) in expected.items():
        assert VOICE_COMMANDS[phrase] == cmd
        assert cmd not in queued, f"{cmd} is spoken-only and must hold no key"
        owning = [r for r in rows if cmd in r.commands]
        assert len(owning) == 1, f"expected exactly one row for {cmd}"
        assert _keys(owning[0]) == ()
        # The scopes share one row, so the Say column shows the folded phrase.
        assert shown in owning[0].voice


def test_the_group_loops_are_cycled_by_one_key_per_side():
    """Each satellite's whole loop lives on one key — Home for portrait, E for
    landscape — which steps it seeds → actions → off → seeds.

    That key started out as a plain way out of a loop, but ending one is only the
    last of the three stops, so it reaches the cycle command rather than no_loop.
    Ending a loop outright stays spoken ("portrait end loop"), and holds no key.
    """
    expected = {"portrait_loop": ("Home", 0), "landscape_loop": ("E", 1)}
    rows = _all_rows()
    for cmd, (key, column) in expected.items():
        bound = _ahk_binding_for(cmd)
        assert bound is not None, f"{cmd} must be bound in the AHK hotkey script"
        # AHK writes letter hotkeys lowercase; the reference labels them uppercase.
        assert bound.lower() == key.lower(), f"{cmd} is on {bound!r}, expected {key!r}"
        owning = [r for r in rows if cmd in r.commands]
        assert len(owning) == 1, f"expected exactly one row for {cmd}"
        # The sides share a row, each key under its own column.
        assert owning[0].key_columns[column] == (key,)
    for cmd in ("portrait_no_loop", "landscape_no_loop"):
        assert _ahk_binding_for(cmd) is None, f"{cmd} gave its key to the cycle"
        owning = [r for r in rows if cmd in r.commands]
        assert len(owning) == 1, f"expected exactly one row for {cmd}"
        assert _keys(owning[0]) == ()


def _satellite_section() -> ReferenceSection:
    return {s.title: s for s in build_reference_sections()}["Satellites"]


def test_every_scope_shares_one_grid_with_a_key_column_per_side():
    """Portrait, Landscape, Both, Active side and Filters ran the same list of
    actions, so they are one grid now: a row names every scope that has the
    action, and puts each side's key in its own column.  Five sections said it
    all five times and were free to drift apart."""
    section = _satellite_section()
    assert section.key_headers == ("Portrait", "Landscape")
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
        assert len(row.key_columns) == 2


def test_the_shared_grid_drops_the_scope_word_from_the_say_column():
    """A row cannot spell out "portrait next", "next landscape", "both next" and
    "next" without printing its own action four times, so the Say column keeps the
    action alone and the section's note explains how to aim it."""
    section = _satellite_section()
    for word in ("portrait", "landscape", "both"):
        assert word in section.note
    nxt = next(r for r in section.rows if "portrait_next" in r.commands)
    assert nxt.voice == ("next",)
    assert nxt.key_columns == (("Right",), ("D",))
    lock = next(r for r in section.rows if "portrait_lock_on" in r.commands)
    assert lock.voice == ("lock", "unlock")
    for row in section.rows:
        if row.description.startswith(("Filter by act", "Drop the filter")):
            continue  # the filter phrases scope themselves differently; see below
        for phrase in row.voice:
            assert not {"portrait", "landscape", "both"} & set(phrase.split()), (
                f"{phrase!r} still carries a scope word"
            )


def test_the_grid_carries_every_both_and_active_command():
    """Nothing was dropped in the consolidation: each command the retired "Both",
    "Active side" and "Filters" sections documented is on a row of the grid."""
    covered = {c for row in _satellite_section().rows for c in row.commands}
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
    rows = _satellite_section().rows
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


def test_mode_named_nav_shows_friendly_names_in_the_legend():
    """The Nau/Genau nav rows surface the mode-named phrases under their friendly
    names ("nau mode next", "genau next", "hybrid next") — never the raw vosk
    sound-alikes ("now mode", "go now")."""
    rows = _all_rows()
    main_next_row = next(r for r in rows if "main_next" in r.commands)
    assert {"nau mode next", "next nau mode", "hybrid next", "next hybrid"} <= set(main_next_row.voice)
    genau_next_row = next(r for r in rows if "genau_next_clip" in r.commands)
    assert {"genau next", "next genau"} <= set(genau_next_row.voice)
    # The raw sound-alikes must never leak into any Say column.
    for row in rows:
        for phrase in row.voice:
            assert "now mode" not in phrase and "go now" not in phrase, phrase


def test_nau_video_rows_show_main_nav_in_both_orders():
    """The Nau prev/next rows surface "main previous"/"main next" and the
    reverse order, so the main player's navigation is visible in the
    legend."""
    rows = _all_rows()
    prev = next(r for r in rows if "main_prev" in r.commands)
    nxt = next(r for r in rows if "main_next" in r.commands)
    assert {"main previous", "previous main"} <= set(prev.voice)
    assert {"main next", "next main"} <= set(nxt.voice)


def test_the_playback_nudge_is_its_own_spoken_row_ahead_of_the_absolute_sets():
    """Two ways to move the rate, and the row order follows the Genau section's:
    the up/down nudge, then the line that sets it outright.  The nudge is spoken
    only — the keys say "speed up", which follows the OSR2's driver instead."""
    nau_rows = {s.title: s for s in build_reference_sections()}["Nau"].rows
    descs = [r.description for r in nau_rows]
    nudge = next(r for r in nau_rows if "nau_speed_up" in r.commands)
    assert _keys(nudge) == ()
    assert nudge.voice == ("playback speed up", "playback slow down", "playback speed down")
    assert descs.index(nudge.description) + 1 == next(
        i for i, d in enumerate(descs) if d.startswith("Set video speed")
    )


def test_quit_row_lists_exit_synonym():
    """"exit" is a spoken synonym for "quit" and appears in the legend's Quit row."""
    rows = _all_rows()
    quit_rows = [r for r in rows if "quit" in r.commands]
    assert quit_rows and {"exit", "quit"} <= set(quit_rows[0].voice)


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
    assert "hot keys" not in voice  # the OOV recognizer form is hidden behind "hotkeys"


def test_sound_rows_are_voice_only_and_list_both_words_of_each_pair():
    """Mute and the volume steps are spoken-only, and each step surfaces both of
    its synonyms so the legend never implies one of them is the "real" phrase."""
    rows = _all_rows()
    mute = next(r for r in rows if "audio_mute" in r.commands)
    assert _keys(mute) == ()
    # "unmute" is one word to the reader; only the recognizer hears "un mute".
    assert mute.voice == ("mute", "unmute")
    assert "audio_unmute" in mute.commands

    # One sound level reaches both of the primary display's sinks, so the steps
    # are listed under each player that can own the display — Nau's video sound
    # and Genau's clip music are the same control from the speaker's side.
    by_title = {s.title: s for s in build_reference_sections()}
    for title in ("Genau", "Nau"):
        steps = [r for r in by_title[title].rows if "audio_volume_up" in r.commands]
        assert len(steps) == 1, f"expected one volume row in {title}"
        assert "audio_volume_down" in steps[0].commands, "one row documents the pair"
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
    rows = [r for r in _all_rows() if "both_latest" in r.commands]
    assert len(rows) == 1, "expected exactly one Latest row"
    row = rows[0]
    assert _keys(row) == ()
    # It sits in the satellite grid like every other action, so its Say column
    # is the bare "latest" and "both latest" comes from the section's note.
    assert row.voice == ("latest",)
    assert {"portrait_latest", "landscape_latest", "active_latest"} <= set(row.commands)
    assert "premiere" not in VOICE_COMMANDS
    assert "recents" not in VOICE_COMMANDS


def test_end_loop_follows_the_player_last_spoken_to():
    """"end loop" is side-agnostic like every other bare command: it reaches whichever
    player was last addressed, and means that player's kind of loop — Nau's A-B loop
    on the main player, a satellite's group loop on portrait or landscape."""
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
    from fun_time.command_reference import (
        _SECTIONS,
        _collapse_scopes,
        _display_voice,
        friendly_voice,
    )

    inverse: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        inverse.setdefault(cmd, []).append(phrase)
    for section in _SECTIONS:
        for row in section.rows:
            if row.voice_display is not None:
                continue  # deliberate display alias (e.g. show "genau" not "go now")
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


def test_genau_row_displays_genau_but_recognizer_uses_go_now():
    """The Genau mode row shows 'genau' while the recognizer phrase is 'go now'."""
    assert VOICE_COMMANDS["go now"] == "genau_activate"
    assert "genau" not in VOICE_COMMANDS  # display-only alias, not a recognizer phrase
    genau_rows = [r for r in _all_rows() if "genau_activate" in r.commands]
    assert genau_rows and genau_rows[0].voice == ("genau",)


def test_genau_mode_row_lists_genau_phrase_and_g_key():
    rows = _all_rows()
    genau_rows = [r for r in rows if "genau_activate" in r.commands]
    assert genau_rows, "expected a row for genau_activate"
    row = genau_rows[0]
    assert "genau" in row.voice
    assert "go now" not in row.voice
    assert any(key.lower() == "g" for key in _keys(row))


def test_section_titles_run_global_genau_nau_satellites():
    """Four sections, in the order the room is built: what governs everything,
    then the engine driving the OSR2, then the video it plays under, then the two
    side players.  Genau leads Nau because it owns the primary display in its own
    mode, and every satellite scope shares the last section."""
    titles = [s.title for s in build_reference_sections()]
    assert titles == ["Global", "Genau", "Nau", "Satellites"]
    for retired in ("Portrait", "Landscape", "Both", "Active side",
                    "Filters (satellites)", "Modes", "Genau control"):
        assert retired not in titles, f"{retired!r} should be folded in"


def test_the_backslash_key_is_split_between_its_two_meanings():
    sections = build_reference_sections()
    by_title = {s.title: s for s in sections}
    main_backslash = [r for r in by_title["Nau"].rows if "\\" in _keys(r)]
    genau_backslash = [r for r in by_title["Genau"].rows if "\\" in _keys(r)]
    assert len(main_backslash) == 1, "expected the file-dialog '\\' row in Nau"
    assert "browse" in main_backslash[0].voice
    assert len(genau_backslash) == 1, "expected a separate '\\' offset row in Genau"


def test_nau_mode_row_displays_nau_mode_but_recognizer_uses_sound_alikes():
    # "nau" isn't in the vosk vocabulary, so the recognizer listens for the
    # sound-alikes while the reference shows the friendly "nau mode".
    assert VOICE_COMMANDS["now mode"] == "nau_activate"
    assert "nau" not in VOICE_COMMANDS  # display-only alias, not a recognizer phrase
    nau_rows = [r for r in _all_rows() if "nau_activate" in r.commands]
    assert nau_rows and nau_rows[0].voice == ("nau mode",)


def test_loop_control_row_consolidates_record_and_cancel():
    rows = _all_rows()
    loop_rows = [r for r in rows if "nau_record_down" in r.commands]
    assert loop_rows, "expected a loop control row"
    row = loop_rows[0]
    assert "R" in _keys(row)
    assert "record" in row.voice
    assert "loop" in row.voice
    # Record and cancel are one row.  The cancel's phrase, "end loop", is no longer
    # this row's own: it is the side-agnostic phrase, and reaches Nau's loop through
    # the active-side resolution whenever the main player is the player last addressed.
    assert "nau_loop_cancel" in row.commands


def test_previous_shape_is_a_separate_keyless_line():
    rows = _all_rows()
    next_rows = [r for r in rows if "genau_cycle_shape" in r.commands]
    prev_rows = [r for r in rows if "genau_cycle_shape_prev" in r.commands]
    assert next_rows and _keys(next_rows[0]) == ("I",)
    # The "I" key does next only — it must not claim previous.
    assert "genau_cycle_shape_prev" not in next_rows[0].commands
    assert prev_rows and _keys(prev_rows[0]) == ()
    assert "previous shape" in prev_rows[0].voice


def test_min_max_value_live_on_their_own_consecutive_set_lines():
    rows = _all_rows()
    amp_updown = next(r for r in rows if "genau_amplitude_up" in r.commands)
    # The up/down line must NOT carry min/max/value phrases.
    assert not any(("min" in v or "max" in v or "0–100" in v) for v in amp_updown.voice)

    genau_rows = {s.title: s for s in build_reference_sections()}["Genau"].rows
    descs = [r.description for r in genau_rows]
    for updown, setname in (
        ("Amplitude up / down", "Set amplitude"),
        ("Center up / down", "Set center"),
        ("Speed up / down", "Set speed"),
    ):
        # Matched by prefix: a row may carry an explanation after its name (speed
        # says which engine the nudge reaches in hybrid).
        at = next(i for i, d in enumerate(descs) if d.startswith(updown))
        assert descs.index(setname) == at + 1, "Set line must follow its up/down line"
    set_amp = next(r for r in genau_rows if r.description == "Set amplitude")
    assert "min amp" in set_amp.voice and "max amp" in set_amp.voice


def test_cruise_voice_lists_on_before_off():
    cruise = next(r for r in _all_rows() if "genau_toggle_cruise" in r.commands)
    assert cruise.voice == ("cruise control", "cruise on", "cruise off")


def test_previous_next_pairs_are_ordered_previous_then_next():
    for section in build_reference_sections():
        descs = [r.description for r in section.rows]
        for d in descs:
            if d.startswith("Previous "):
                nxt = "Next " + d[len("Previous "):]
                if nxt in descs:
                    assert descs.index(d) < descs.index(nxt), f"{d!r} should precede {nxt!r}"


def test_offset_voice_on_genau_backslash_row():
    rows = _all_rows()
    offset_rows = [r for r in rows if "\\" in _keys(r) and "quarter_button" in r.commands]
    assert offset_rows and "offset" in offset_rows[0].voice


def test_corrected_descriptions():
    """Rows say what the key does to the room, not just what it is called."""
    descs = {r.description for r in _all_rows()}
    assert {"Disable voice control", "Start / stop broker"} <= descs
    assert any(d.startswith("Enable / disable cruise control") for d in descs)
    assert "Mute voice control" not in descs
    assert "Cruise control" not in descs
    assert "Broker start / stop" not in descs


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
    assert row.voice == ("relief omnipause", "retract", "stop")


def test_relief_answers_to_one_word_as_well_as_its_full_name():
    """Three words is a lot to get out in the moment the command is for, so the
    two obvious single words reach it too."""
    assert VOICE_COMMANDS["stop"] == "relief_omnipause"
    assert VOICE_COMMANDS["retract"] == "relief_omnipause"


def test_relief_survives_the_omnipause_suspension_on_both_input_paths():
    """Relief has to fire from inside omnipause — a paused session is exactly
    where the device may still be on the user — so Shift+Esc sits in the AHK
    #SuspendExempt block and its command is exempt from the voice freeze too.
    Either half missing leaves the emergency dead in the one state it is for."""
    # Imported here, not at module scope: this file is otherwise free of the
    # voice runtime module, the same property its subprocess test pins for the
    # production reference.
    from fun_time.voice_control import SUSPEND_EXEMPT_COMMANDS

    assert "relief_omnipause" in _ahk_suspend_exempt_commands()
    # The whole set, not one member: what a paused room may be heard to do is
    # the owner's call, so widening it has to fail here rather than depend on
    # somebody reading a comment.  See CLAUDE.md, "Standing rules".
    assert SUSPEND_EXEMPT_COMMANDS == frozenset({"play", "quit", "relief_omnipause"})


def test_no_say_column_leaks_the_raw_omni_pause_form():
    for row in _all_rows():
        for phrase in row.voice:
            assert "omni pause" not in phrase, phrase


def test_funscript_row_shows_the_joined_word_the_recognizer_cannot_hear():
    """The small vosk model has no "funscript" token, so the recognizer listens
    for "fun script" — but nobody says it that way, and reading it in the popup
    would teach the wrong phrase."""
    assert VOICE_COMMANDS["jump to fun script"] == "nau_funscript_jump"
    assert "jump to funscript" not in VOICE_COMMANDS  # display-only

    rows = [r for r in _all_rows() if "nau_funscript_jump" in r.commands]
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
    from fun_time.filter_vocab import FILTER_ACTS, display_forms

    rows = _satellite_section().rows
    set_row = next(r for r in rows if r.description.startswith("Filter by act"))
    # The acts read under their real names, not the sound-alikes the grammar is
    # built from: an act whose word the speech model has no token for is *heard*
    # as something else, and printing that would teach the reader the wrong word.
    assert set(set_row.voice) == set(display_forms())
    spoken = {form for forms in FILTER_ACTS.values() for form in forms}
    workarounds = spoken - set(display_forms())
    # No demand that this overlay HAS a workaround — that mechanic is pinned
    # on fixture data in test_filter_vocab — only that none it does have leaks.
    assert not workarounds & set(set_row.voice), workarounds
    # Clearing scopes like every other action now, so its phrases derive with the
    # rest and the side word never appears: "portrait clear filter", not "clear
    # portrait".
    drop_row = next(r for r in rows if r.description.startswith("Drop the filter"))
    assert set(drop_row.voice) == {"no filter", "filter off", "clear filter", "show everything"}
    assert VOICE_COMMANDS["portrait clear filter"] == "portrait_no_filter"
    assert VOICE_COMMANDS["clear filter landscape"] == "landscape_no_filter"
    assert VOICE_COMMANDS["both show everything"] == "both_no_filter"
    assert VOICE_COMMANDS["clear filter"] == "active_no_filter"
    assert "clear portrait" not in VOICE_COMMANDS


def test_every_filter_voice_command_is_represented():
    from fun_time.filter_vocab import filter_voice_commands

    covered = _covered_commands()
    missing = {cmd for cmd in filter_voice_commands().values() if cmd not in covered}
    assert not missing, f"filter commands missing from reference: {sorted(missing)}"
