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
from fun_time.voice_control import VOICE_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NUMERIC_RE = re.compile(r"^genau_(amp|center|speed)_\d+$")


def _ahk_hotkey_commands() -> set[str]:
    """Every command bound to a key via QueueCommand() in the AHK hotkey script."""
    text = (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")
    return set(re.findall(r'QueueCommand\("([^"]+)"\)', text))


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
    """A "Both VLC" section drives Portrait + Landscape together, by voice only."""
    sections = {s.title: s for s in build_reference_sections()}
    assert "Both VLC" in sections
    both = sections["Both VLC"]
    cmds = {c for row in both.rows for c in row.commands}
    assert cmds == {
        "both_prev", "both_next", "both_trash",
        "both_lock_on", "both_lock_off",
        "both_cycle_action", "both_cycle_seed",
    }
    # Voice phrases are derived from VOICE_COMMANDS, so each row surfaces one.
    next_row = next(r for r in both.rows if "both_next" in r.commands)
    assert "next both" in next_row.voice
    lock_row = next(r for r in both.rows if "both_lock_on" in r.commands)
    assert "lock both" in lock_row.voice and "unlock both" in lock_row.voice


def test_premiere_row_uses_p_key_and_premiere_voice():
    """The newest-first refresh is branded "Premiere": P key, spoken "premiere"."""
    assert VOICE_COMMANDS["premiere"] == "recency_order_refresh"
    rows = [r for r in _all_rows() if "recency_order_refresh" in r.commands]
    assert len(rows) == 1, "expected exactly one Premiere row"
    row = rows[0]
    assert row.hotkeys == ("P",)
    assert "premiere" in row.voice


def test_voice_phrases_are_derived_from_voice_commands():
    """Each row's voice must include every phrase VOICE_COMMANDS assigns to its
    commands — except rows with an explicit voice_display alias."""
    from fun_time.command_reference import _SECTIONS, _voice_for

    inverse: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        inverse.setdefault(cmd, []).append(phrase)
    for _title, rows in _SECTIONS:
        for row in rows:
            if row.voice_display is not None:
                continue  # deliberate display alias (e.g. show "genau" not "go now")
            built = _voice_for(row.commands) + row.literal_voice
            derived = sorted(p for cmd in row.commands for p in inverse.get(cmd, []))
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
    for expected in ("Global", "Nau", "Portrait VLC", "Landscape VLC", "Modes", "Genau"):
        assert expected in titles, f"missing section {expected!r}"
    assert "Genau control" not in titles  # renamed to "Genau"
    assert "Primary VLC" not in titles  # replaced by "Nau"

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
    # Record and cancel are one row now; cancel's phrase is "end loop".
    assert "nau_loop_cancel" in row.commands
    assert "end loop" in row.voice


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
