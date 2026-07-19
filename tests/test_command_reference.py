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
from fun_time.voice_control import SUSPEND_EXEMPT_COMMANDS, VOICE_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NUMERIC_RE = re.compile(r"^genau_(amp|center|speed)_\d+$")
_QUEUED_RE = re.compile(r'QueueCommand\("([^"]+)"\)')


def _ahk_script() -> str:
    return (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")


def _ahk_hotkey_commands() -> set[str]:
    """Every command bound to a key via QueueCommand() in the AHK hotkey script."""
    return set(_QUEUED_RE.findall(_ahk_script()))


def _ahk_suspend_exempt_commands() -> set[str]:
    """The commands bound inside the script's ``#SuspendExempt`` block.

    Omnipause suspends the hotkeys wholesale, so these are the only keys that
    still reach Python while the session is paused.
    """
    block = _ahk_script().split("#SuspendExempt true", 1)[1].split("#SuspendExempt false", 1)[0]
    return set(_QUEUED_RE.findall(block))


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
            assert row.hotkeys or row.voice, f"row {row.description!r} has no trigger"


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
        assert any(row.hotkeys for row in owning), (
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
        assert row.hotkeys == (), f"{row.description!r} must show no hotkey"


def test_cycle_action_and_seed_rows_have_keys_and_voice():
    """Del/End cycle the portrait's action/seed; E/Q do the same for landscape."""
    expected = {
        "portrait_cycle_action": ("Del", "portrait action"),
        "portrait_cycle_seed": ("End", "portrait seed"),
        "landscape_cycle_action": ("E", "landscape action"),
        "landscape_cycle_seed": ("Q", "landscape seed"),
    }
    rows = _all_rows()
    for cmd, (key, phrase) in expected.items():
        assert VOICE_COMMANDS[phrase] == cmd
        owning = [r for r in rows if cmd in r.commands]
        assert len(owning) == 1, f"expected exactly one row for {cmd}"
        assert owning[0].hotkeys == (key,)
        assert phrase in owning[0].voice


def test_both_section_lists_combined_satellite_commands():
    """A "Both" section drives Portrait + Landscape together, by voice only."""
    sections = {s.title: s for s in build_reference_sections()}
    assert "Both" in sections
    both = sections["Both"]
    cmds = {c for row in both.rows for c in row.commands}
    assert cmds == {
        "both_prev", "both_next", "both_trash",
        "both_lock_on", "both_lock_off",
        "both_cycle_action", "both_cycle_seed", "both_more_seeds",
        "both_action_loop", "both_seed_loop", "both_no_loop", "both_lock_action",
        "both_shuffle", "both_no_filter", "both_reset",
    }
    # Voice phrases are derived from VOICE_COMMANDS, so each row surfaces one —
    # side word first ("both next", "both lock"), matching every satellite row.
    next_row = next(r for r in both.rows if "both_next" in r.commands)
    assert "both next" in next_row.voice
    lock_row = next(r for r in both.rows if "both_lock_on" in r.commands)
    assert "both lock" in lock_row.voice and "both unlock" in lock_row.voice


def test_active_side_section_documents_the_bare_commands():
    """The side-agnostic voice commands live in their own 'Active side' section,
    each keyless (they are voice-only) and carrying its bare phrase."""
    sections = {s.title: s for s in build_reference_sections()}
    assert "Active side" in sections
    rows = sections["Active side"].rows
    by_command = {cmd: r for r in rows for cmd in r.commands}
    expected = {
        "active_lock_on": "lock",
        "active_lock_off": "unlock",
        "active_prev": "previous",
        "active_next": "next",
        "active_trash": "weird",
        "active_cycle_action": "action",
        "active_cycle_seed": "seed",
    }
    for cmd, phrase in expected.items():
        assert cmd in by_command, f"{cmd} missing from the Active side section"
        row = by_command[cmd]
        assert phrase in row.voice
        assert row.hotkeys == (), f"{cmd} is voice-only and must show no hotkey"


def test_mode_named_nav_shows_friendly_names_in_the_legend():
    """The Nau/Genau nav rows surface the mode-named phrases under their friendly
    names ("nau mode next", "genau next", "hybrid next") — never the raw vosk
    sound-alikes ("now mode", "go now")."""
    rows = _all_rows()
    primary_next_row = next(r for r in rows if "primary_next" in r.commands)
    assert {"nau mode next", "next nau mode", "hybrid next", "next hybrid"} <= set(primary_next_row.voice)
    genau_next_row = next(r for r in rows if "genau_next_clip" in r.commands)
    assert {"genau next", "next genau"} <= set(genau_next_row.voice)
    # The raw sound-alikes must never leak into any Say column.
    for row in rows:
        for phrase in row.voice:
            assert "now mode" not in phrase and "go now" not in phrase, phrase


def test_nau_video_rows_show_primary_nav_in_both_orders():
    """The Nau prev/next rows surface "primary previous"/"primary next" (and the
    reverse order, plus the "main" synonym) so the primary player's navigation
    is visible in the legend."""
    rows = _all_rows()
    prev = next(r for r in rows if "primary_prev" in r.commands)
    nxt = next(r for r in rows if "primary_next" in r.commands)
    assert {"primary previous", "previous primary", "main previous", "previous main"} <= set(prev.voice)
    assert {"primary next", "next primary", "main next", "next main"} <= set(nxt.voice)


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
    assert mute.hotkeys == ()
    # "unmute" is one word to the reader; only the recognizer hears "un mute".
    assert mute.voice == ("mute", "unmute")
    assert "audio_unmute" in mute.commands

    down = next(r for r in rows if "audio_volume_down" in r.commands)
    up = next(r for r in rows if "audio_volume_up" in r.commands)
    assert down is up, "one row documents the pair of volume steps"
    assert set(up.voice) == {"quiet", "quieter", "loud", "louder"}


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
    assert len(rows) == 1, "expected exactly one both-sides Latest row"
    row = rows[0]
    assert row.hotkeys == ()
    assert "both latest" in row.voice
    assert "premiere" not in VOICE_COMMANDS
    assert "recents" not in VOICE_COMMANDS


def test_end_loop_follows_the_player_last_spoken_to():
    """"end loop" is side-agnostic like every other bare command: it reaches whichever
    player was last addressed, and means that player's kind of loop — Nau's A-B loop
    on the primary, a satellite's group loop on portrait or landscape."""
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
    from fun_time.command_reference import _SECTIONS, _voice_for, friendly_voice

    inverse: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        inverse.setdefault(cmd, []).append(phrase)
    for _title, rows in _SECTIONS:
        for row in rows:
            if row.voice_display is not None:
                continue  # deliberate display alias (e.g. show "genau" not "go now")
            built = _voice_for(row.commands) + row.literal_voice
            # Phrases are shown under their friendly mode name (sound-alikes rewritten).
            derived = sorted(friendly_voice(p) for cmd in row.commands for p in inverse.get(cmd, []))
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
    assert any(key.lower() == "g" for key in row.hotkeys)


def test_section_titles_and_backslash_split():
    sections = build_reference_sections()
    titles = [s.title for s in sections]
    for expected in ("Global", "Nau", "Portrait", "Landscape", "Modes", "Genau"):
        assert expected in titles, f"missing section {expected!r}"
    assert "Genau control" not in titles  # renamed to "Genau"

    by_title = {s.title: s for s in sections}
    primary_backslash = [r for r in by_title["Nau"].rows if "\\" in r.hotkeys]
    genau_backslash = [r for r in by_title["Genau"].rows if "\\" in r.hotkeys]
    assert len(primary_backslash) == 1, "expected the file-dialog '\\' row in Nau"
    assert "browse" in primary_backslash[0].voice
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
    assert "R" in row.hotkeys
    assert "record" in row.voice
    assert "loop" in row.voice
    # Record and cancel are one row.  The cancel's phrase, "end loop", is no longer
    # this row's own: it is the side-agnostic phrase, and reaches Nau's loop through
    # the active-side resolution whenever the primary is the player last addressed.
    assert "nau_loop_cancel" in row.commands


def test_previous_shape_is_a_separate_keyless_line():
    rows = _all_rows()
    next_rows = [r for r in rows if "genau_cycle_shape" in r.commands]
    prev_rows = [r for r in rows if "genau_cycle_shape_prev" in r.commands]
    assert next_rows and next_rows[0].hotkeys == (",",)
    # The "," key does next only — it must not claim previous.
    assert "genau_cycle_shape_prev" not in next_rows[0].commands
    assert prev_rows and prev_rows[0].hotkeys == ()
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
        assert descs.index(setname) == descs.index(updown) + 1, "Set line must follow its up/down line"
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
    offset_rows = [r for r in rows if "\\" in r.hotkeys and "quarter_button" in r.commands]
    assert offset_rows and "offset" in offset_rows[0].voice


def test_corrected_descriptions():
    descs = {r.description for r in _all_rows()}
    assert {"Disable voice control", "Enable / disable cruise control", "Start / stop broker"} <= descs
    assert "Mute voice control" not in descs
    assert "Cruise control" not in descs
    assert "Broker start / stop" not in descs


def test_omnipause_row_uses_esc_and_pause_play_voice():
    rows = _all_rows()
    esc_rows = [r for r in rows if "Esc" in r.hotkeys]
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
    assert row.hotkeys == ("Shift+Esc",)
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
    assert "relief_omnipause" in _ahk_suspend_exempt_commands()
    assert "relief_omnipause" in SUSPEND_EXEMPT_COMMANDS


def test_no_say_column_leaks_the_raw_omni_pause_form():
    for row in _all_rows():
        for phrase in row.voice:
            assert "omni pause" not in phrase, phrase


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
    # No raw template gaps.
    assert "{" not in html and "}" not in html


def test_render_reference_html_has_no_heading_or_subtitle():
    """The popup's name lives on the window chrome, so the rendered HTML carries
    neither an in-window heading nor the old subtitle — it opens straight into
    the first section."""
    html = render_reference_html()
    assert "<h2" not in html
    assert "Hotkeys &amp; Voice Commands Reference" not in html
    assert "Global while Fun Time" not in html


def test_filters_section_documents_the_spoken_filters():
    from fun_time.filter_vocab import spoken_forms_for_both

    sections = {s.title: s for s in build_reference_sections()}
    assert "Filters (satellites)" in sections
    blob = " ".join(v for row in sections["Filters (satellites)"].rows for v in row.voice)
    assert any(form in blob for form in spoken_forms_for_both())  # a spoken act form
    assert "clear portrait" in blob  # a clear phrase


def test_every_filter_voice_command_is_represented():
    from fun_time.filter_vocab import filter_voice_commands

    covered = _covered_commands()
    missing = {cmd for cmd in filter_voice_commands().values() if cmd not in covered}
    assert not missing, f"filter commands missing from reference: {sorted(missing)}"
