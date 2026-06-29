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


def test_voice_phrases_are_derived_from_voice_commands():
    """A row's voice list must exactly match the phrases VOICE_COMMANDS assigns to its commands."""
    inverse: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        inverse.setdefault(cmd, []).append(phrase)
    for row in _all_rows():
        derived = sorted(p for cmd in row.commands for p in inverse.get(cmd, []))
        for phrase in derived:
            assert phrase in row.voice, (
                f"row {row.description!r} should list derived phrase {phrase!r}"
            )


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
    for expected in ("Global", "Primary VLC", "Portrait VLC", "Landscape VLC", "Modes", "Genau"):
        assert expected in titles, f"missing section {expected!r}"
    assert "Genau control" not in titles  # renamed to "Genau"
    assert "Primary" not in titles  # renamed to "Primary VLC"

    by_title = {s.title: s for s in sections}
    primary_backslash = [r for r in by_title["Primary VLC"].rows if "\\" in r.hotkeys]
    genau_backslash = [r for r in by_title["Genau"].rows if "\\" in r.hotkeys]
    assert len(primary_backslash) == 1, "expected the file-dialog '\\' row in Primary VLC"
    assert "browse" in primary_backslash[0].voice
    assert len(genau_backslash) == 1, "expected a separate '\\' offset row in Genau"


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
