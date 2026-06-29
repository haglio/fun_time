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

from fun_time.voice_control import VOICE_COMMANDS


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


# Authored reference, grouped by the part of the dashboard each row drives.
# ``commands`` are dispatch ids — voice phrases are looked up from them.
_SECTIONS: tuple[tuple[str, tuple[_Row, ...]], ...] = (
    (
        "Global",
        (
            _Row("Quit — close everything", ("Ctrl+Alt+Q",), ("quit",)),
            _Row("Pause / resume everything (OmniPause)", ("Esc",), ("omnipause_toggle", "pause", "play")),
            _Row("Pause everything (enter OmniPause)", ("Space",), ("enter_omnipause",)),
            _Row("Toggle F-Mode", ("F",), ("fmode_toggle", "fmode_on", "fmode_off")),
            _Row("Mute / unmute voice control", (), ("voice_toggle", "voice_off")),
            _Row("Start broker", (), ("broker_start",)),
            _Row("Stop broker", (), ("broker_stop",)),
        ),
    ),
    (
        "Primary",
        (
            _Row("Previous primary clip", ("[",), ("primary_prev",)),
            _Row("Next primary clip", ("]",), ("primary_next",)),
            _Row("Nudge primary back", ("-",), ("vlc_nudge_prev",)),
            _Row("Nudge primary forward", ("=",), ("vlc_nudge_next",)),
            _Row("Open file dialog (VLC) / offset ¼ cycle (Genau)", ("\\",), ("backslash_key",)),
            _Row("Save clip (Clipper)", ("'",), ("clipper_save",)),
        ),
    ),
    (
        "Portrait",
        (
            _Row("Previous portrait clip", ("Left",), ("portrait_prev",)),
            _Row("Next portrait clip", ("Right",), ("portrait_next",)),
            _Row("Discard portrait clip", ("Up",), ("portrait_trash",)),
            _Row("Lock / unlock portrait", ("Down",), ("portrait_lock", "portrait_lock_on")),
        ),
    ),
    (
        "Landscape",
        (
            _Row("Previous landscape clip", ("A",), ("landscape_prev",)),
            _Row("Next landscape clip", ("D",), ("landscape_next",)),
            _Row("Discard landscape clip", ("W",), ("landscape_trash",)),
            _Row("Lock / unlock landscape", ("S",), ("landscape_lock", "landscape_lock_on")),
        ),
    ),
    (
        "Modes",
        (
            _Row("Genau mode", ("G",), ("genau_activate",)),
            _Row("VLC mode", ("V",), ("vlc_activate",)),
            _Row("Hybrid mode", ("H",), ("hybrid_activate",)),
            _Row("Stop Genau (back to VLC)", (), ("genau_deactivate",)),
        ),
    ),
    (
        "Genau control",
        (
            _Row("Amplitude up", ("I",), ("genau_amplitude_up",)),
            _Row("Amplitude down", ("K",), ("genau_amplitude_down",)),
            _Row("Center up", ("O",), ("genau_center_up",)),
            _Row("Center down", ("U",), ("genau_center_down",)),
            _Row("Speed up", ("L",), ("genau_speed_up",)),
            _Row("Speed down", ("J",), ("genau_speed_down",)),
            _Row(
                "Set amplitude / center / speed to a value (spoken in tens)",
                (),
                (),
                ("amp 0–100", "center 0–100", "speed 0–100"),
            ),
            _Row("Cycle waveform shape", (",",), ("genau_cycle_shape",)),
            _Row("Previous Genau clip", ("M",), ("genau_prev_clip",)),
            _Row("Next Genau clip", (".",), ("genau_next_clip",)),
            _Row("Toggle Genau auto-takeover", ("/",), ("genau_toggle_auto",)),
            _Row("Cruise control", (), ("genau_toggle_cruise", "genau_cruise_on", "genau_cruise_off")),
        ),
    ),
)


def _voice_for(commands: tuple[str, ...]) -> tuple[str, ...]:
    """All spoken phrases that map to any of *commands*, in sorted order."""
    return tuple(sorted(
        phrase for phrase, cmd in VOICE_COMMANDS.items() if cmd in commands
    ))


def build_reference_sections() -> tuple[ReferenceSection, ...]:
    """Build the display reference, deriving voice phrases from VOICE_COMMANDS."""
    sections: list[ReferenceSection] = []
    for title, rows in _SECTIONS:
        refs = tuple(
            CommandRef(
                description=row.description,
                hotkeys=row.hotkeys,
                voice=_voice_for(row.commands) + row.literal_voice,
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
