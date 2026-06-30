"""Canonical reference of Fun Time hotkeys and voice commands.

This module is the single presentation surface for "what can I press and what
can I say."  The factual mappings live in two authoritative sources:

* ``windows_bridge_hotkeys.ahk`` — physical key -> dispatch command
* :data:`fun_time.voice_control.VOICE_COMMANDS` — spoken phrase -> dispatch command

Here we attach a human-readable description and key label to each dispatch
command and group the rows into sections.  Voice phrases are *derived* from
``VOICE_COMMANDS`` so the spoken column can never drift out of sync.  A test
(``tests/test_command_reference.py``) parses the AHK script and cross-checks
``VOICE_COMMANDS`` to guarantee every real trigger is represented here.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from fun_time.voice_commands import VOICE_COMMANDS


@dataclass(frozen=True)
class CommandRef:
    """One reference row: an action and every way to trigger it."""

    description: str
    hotkeys: tuple[str, ...]
    voice: tuple[str, ...]
    commands: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceSection:
    title: str
    rows: tuple[CommandRef, ...]


@dataclass(frozen=True)
class _Row:
    """Authored row data; voice phrases are derived from ``commands``."""

    description: str
    hotkeys: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    literal_voice: tuple[str, ...] = ()
    # When set, the Say column shows exactly these phrases instead of the ones
    # derived from ``commands`` — for presenting a friendlier label than the
    # recognizer's actual phrase (e.g. show "genau" while it listens for "go now").
    voice_display: tuple[str, ...] | None = None


# Authored reference, grouped by the part of the dashboard each row drives.
# ``commands`` are dispatch ids — voice phrases are looked up from them.
_SECTIONS: tuple[tuple[str, tuple[_Row, ...]], ...] = (
    (
        "Global",
        (
            _Row("Quit — close everything", ("Ctrl+Alt+Q",), ("quit",)),
            _Row("Omnipause / resume", ("Esc",), ("omnipause_toggle", "pause", "play")),
            # Space and "pause" both enter Omnipause; "pause" is shown for parity.
            _Row("Omnipause", ("Space",), ("enter_omnipause",), ("pause",)),
            _Row("Toggle F-Mode", ("F",), ("fmode_toggle", "fmode_on", "fmode_off")),
            _Row("Disable voice control", ("Backspace",), ("voice_toggle", "voice_off")),
            _Row("Start / stop broker", ("B",), ("broker_panel", "broker_start", "broker_stop")),
        ),
    ),
    (
        "Primary VLC",
        (
            _Row("Previous primary clip", ("[",), ("primary_prev",)),
            _Row("Next primary clip", ("]",), ("primary_next",)),
            _Row("Nudge primary back 10 seconds", ("-",), ("vlc_nudge_prev",)),
            _Row("Nudge primary forward 10 seconds", ("=",), ("vlc_nudge_next",)),
            _Row("Open file dialog", ("\\",), ("backslash_key", "open_file_dialog")),
            _Row("Save clip (Clipper)", ("'",), ("clipper_save",)),
        ),
    ),
    (
        "Portrait VLC",
        (
            _Row("Previous portrait clip", ("Left",), ("portrait_prev",)),
            _Row("Next portrait clip", ("Right",), ("portrait_next",)),
            _Row("Mark portrait clip as weird", ("Up",), ("portrait_trash",)),
            _Row("Lock / unlock portrait", ("Down",), ("portrait_lock", "portrait_lock_on", "portrait_lock_off")),
        ),
    ),
    (
        "Landscape VLC",
        (
            _Row("Previous landscape clip", ("A",), ("landscape_prev",)),
            _Row("Next landscape clip", ("D",), ("landscape_next",)),
            _Row("Mark landscape clip as weird", ("W",), ("landscape_trash",)),
            _Row("Lock / unlock landscape", ("S",), ("landscape_lock", "landscape_lock_on", "landscape_lock_off")),
        ),
    ),
    (
        "Modes",
        (
            _Row("Genau mode", ("G",), ("genau_activate",), voice_display=("genau",)),
            _Row("VLC mode", ("V",), ("vlc_activate",), voice_display=("VLC",)),
            _Row("Hybrid mode", ("H",), ("hybrid_activate",)),
        ),
    ),
    (
        "Genau",
        (
            _Row("Amplitude up / down", ("I", "K"), ("genau_amplitude_up", "genau_amplitude_down")),
            _Row("Set amplitude", (), (), ("min amp", "max amp", "amp 0–100")),
            _Row("Center up / down", ("O", "U"), ("genau_center_up", "genau_center_down")),
            _Row("Set center", (), (), ("min center", "max center", "center 0–100")),
            _Row("Speed up / down", ("L", "J"), ("genau_speed_up", "genau_speed_down")),
            _Row("Set speed", (), (), ("min speed", "max speed", "speed 0–100")),
            _Row("Previous waveform shape", (), ("genau_cycle_shape_prev",)),
            _Row("Next waveform shape", (",",), ("genau_cycle_shape",)),
            _Row("Previous Genau clip", ("M",), ("genau_prev_clip",)),
            _Row("Next Genau clip", (".",), ("genau_next_clip",)),
            _Row("Toggle Genau auto-takeover", ("/",), ("genau_toggle_auto",)),
            _Row("Enable / disable cruise control", ("C",), ("genau_toggle_cruise", "genau_cruise_on", "genau_cruise_off")),
            _Row("Offset ¼ cycle", ("\\",), ("backslash_key", "quarter_button")),
        ),
    ),
)


def _voice_for(commands: tuple[str, ...]) -> tuple[str, ...]:
    """Spoken phrases for *commands*, listed in command order.

    Phrases follow the order their commands appear in the row — so the Say
    column tracks the label (e.g. on before off, up before down) — with each
    command's own synonyms sorted among themselves.
    """
    by_command: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        if cmd in commands:
            by_command.setdefault(cmd, []).append(phrase)
    result: list[str] = []
    for cmd in commands:
        result.extend(sorted(by_command.get(cmd, [])))
    return tuple(result)


def build_reference_sections() -> tuple[ReferenceSection, ...]:
    """Build the display reference, deriving voice phrases from VOICE_COMMANDS."""
    sections: list[ReferenceSection] = []
    for title, rows in _SECTIONS:
        refs = tuple(
            CommandRef(
                description=row.description,
                hotkeys=row.hotkeys,
                voice=(
                    row.voice_display
                    if row.voice_display is not None
                    else _voice_for(row.commands) + row.literal_voice
                ),
                commands=row.commands,
            )
            for row in rows
        )
        sections.append(ReferenceSection(title=title, rows=refs))
    return tuple(sections)


# --- HTML rendering ---------------------------------------------------------
# Colors mirror the shared_ui dark palette (kept as literals so this module
# stays Qt-free and unit-testable without a QApplication).
_BG = "#181818"
_HEADER_BG = "#282828"
_TEXT = "#f0f0f0"
_MUTED = "#787878"
_ACCENT = "#3080e0"
_KEYCAP_BG = "#484848"
_BORDER = "#5f5f5f"


def _keycap(label: str) -> str:
    return (
        f'<span style="background:{_KEYCAP_BG};border:1px solid {_BORDER};'
        f'padding:1px 5px;color:{_TEXT};font-family:Consolas,monospace">'
        f"{html.escape(label)}</span>"
    )


def _cell(content: str, *, color: str = _TEXT, width: str = "") -> str:
    width_attr = f' width="{width}"' if width else ""
    return (
        f'<td valign="top"{width_attr} '
        f'style="padding:3px 8px;color:{color}">{content}</td>'
    )


def render_reference_html() -> str:
    """Render the reference as a self-contained HTML document for QTextBrowser.

    Uses only inline style attributes (no ``<style>`` block) so the markup
    works with Qt's limited rich-text engine.
    """
    parts: list[str] = [
        f'<body style="background:{_BG};color:{_TEXT};'
        'font-family:\'Segoe UI\',sans-serif;font-size:10pt">',
        f'<h2 style="color:{_TEXT};margin:0 0 4px 0">Hotkeys &amp; Voice Commands</h2>',
        f'<p style="color:{_MUTED};margin:0 0 12px 0">'
        "Global while Fun Time is running and not OmniPaused.</p>",
    ]
    for section in build_reference_sections():
        parts.append(
            f'<h3 style="color:{_ACCENT};margin:14px 0 2px 0;'
            f'border-bottom:1px solid {_BORDER}">{html.escape(section.title)}</h3>'
        )
        parts.append('<table width="100%" cellspacing="0" cellpadding="0">')
        parts.append(
            f'<tr style="background:{_HEADER_BG}">'
            + _cell(f'<b style="color:{_MUTED}">Action</b>')
            + _cell(f'<b style="color:{_MUTED}">Key</b>', width="22%")
            + _cell(f'<b style="color:{_MUTED}">Say</b>', width="34%")
            + "</tr>"
        )
        for row in section.rows:
            keys = " ".join(_keycap(k) for k in row.hotkeys) or f'<span style="color:{_MUTED}">—</span>'
            phrases = (
                ", ".join(f'“{html.escape(p)}”' for p in row.voice)
                or f'<span style="color:{_MUTED}">—</span>'
            )
            parts.append(
                "<tr>"
                + _cell(html.escape(row.description))
                + _cell(keys, width="22%")
                + _cell(phrases, color=_TEXT, width="34%")
                + "</tr>"
            )
        parts.append("</table>")
    parts.append("</body>")
    return "".join(parts)
