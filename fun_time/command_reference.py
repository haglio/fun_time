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

from fun_time.filter_vocab import clear_command, set_commands_for_scope, spoken_forms_for_both
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
            _Row("Premiere — (re)load Portrait/Landscape newest-first", ("P",), ("recency_order_refresh",)),
            # The primary display's sound, in whichever mode owns it — Nau's
            # video in nau/hybrid, Genau's clip audio in genau.
            _Row("Mute / unmute the primary display", (), ("audio_mute", "audio_unmute")),
            _Row("Volume down / up, in tenths", (), ("audio_volume_down", "audio_volume_up")),
            _Row("Disable voice control", ("Backspace",), ("voice_toggle", "voice_off")),
            _Row("Start / stop broker", ("B",), ("broker_panel", "broker_start", "broker_stop")),
            _Row(
                "Open / close this hotkeys & voice reference",
                (),
                ("help_reference", "help_reference_close"),
            ),
        ),
    ),
    (
        "Nau",
        (
            _Row("Previous video", ("[",), ("primary_prev",)),
            _Row("Next video", ("]",), ("primary_next",)),
            _Row("Nudge back 10 seconds", ("-",), ("primary_nudge_prev",)),
            _Row("Nudge forward 10 seconds", ("=",), ("primary_nudge_next",)),
            _Row(
                "Set video speed (0.25×–2×; the funscript follows)",
                (),
                (
                    "speed_min", "speed_max",
                    "nau_speed_25", "nau_speed_50", "nau_speed_75", "nau_speed_100",
                    "nau_speed_125", "nau_speed_150", "nau_speed_175", "nau_speed_200",
                ),
                ("min speed", "max speed", "reset speed", "half speed", "double speed", "speed one point five ex"),
            ),
            _Row("Cycle through versions of the current video", ("V",), ("nau_cycle_version",)),
            _Row(
                "Toggle viewing full-length vs short content",
                ("T",),
                ("nau_toggle_length", "nau_length_shorts", "nau_length_full"),
            ),
            _Row(
                "Loop control: hold and release to set a loop, press to end loop",
                ("R",),
                ("nau_record_down", "nau_record_up", "nau_record_tap", "nau_loop_cancel"),
            ),
            _Row("Open file browser", ("\\",), ("backslash_key", "open_file_dialog")),
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
            _Row("Cycle action — same subject(s) & scene, another act", ("Del",), ("portrait_cycle_action",)),
            _Row("Cycle seed — same config, different subject", ("End",), ("portrait_cycle_seed",)),
            _Row("Loop the subject's actions — repeat that group", (), ("portrait_action_loop",)),
            _Row("Loop the act's other seeds — repeat that family", (), ("portrait_seed_loop",)),
            _Row("Filter portrait to the current clip's action", (), ("portrait_lock_action",)),
        ),
    ),
    (
        "Landscape VLC",
        (
            _Row("Previous landscape clip", ("A",), ("landscape_prev",)),
            _Row("Next landscape clip", ("D",), ("landscape_next",)),
            _Row("Mark landscape clip as weird", ("W",), ("landscape_trash",)),
            _Row("Lock / unlock landscape", ("S",), ("landscape_lock", "landscape_lock_on", "landscape_lock_off")),
            _Row("Cycle action — same subject(s) & scene, another act", ("E",), ("landscape_cycle_action",)),
            _Row("Cycle seed — same config, different subject", ("Q",), ("landscape_cycle_seed",)),
            _Row("Loop the subject's actions — repeat that group", (), ("landscape_action_loop",)),
            _Row("Loop the act's other seeds — repeat that family", (), ("landscape_seed_loop",)),
            _Row("Filter landscape to the current clip's action", (), ("landscape_lock_action",)),
        ),
    ),
    (
        "Both VLC",
        (
            _Row("Previous both clips", (), ("both_prev",)),
            _Row("Next both clips", (), ("both_next",)),
            _Row("Mark both clips as weird", (), ("both_trash",)),
            _Row("Lock / unlock both", (), ("both_lock_on", "both_lock_off")),
            _Row("Cycle both actions — same subject(s) & scene, another act", (), ("both_cycle_action",)),
            _Row("Cycle both seeds — same config, different subject", (), ("both_cycle_seed",)),
            _Row("Loop each subject's actions on both", (), ("both_action_loop",)),
            _Row("Loop each act's other seeds on both", (), ("both_seed_loop",)),
            _Row("Filter both to their current clip's action", (), ("both_lock_action",)),
        ),
    ),
    (
        "Active side",
        (
            _Row(
                "Lock / unlock the active side — the portrait or landscape "
                "player most recently addressed by voice or keyboard",
                (),
                ("active_lock_on", "active_lock_off"),
            ),
            _Row(
                "Previous clip on the active player — primary, portrait, or "
                "landscape, whichever you last navigated",
                (),
                ("active_prev",),
            ),
            _Row(
                "Next clip on the active player — primary, portrait, or "
                "landscape, whichever you last navigated",
                (),
                ("active_next",),
            ),
            _Row("Mark the active side's clip as weird", (), ("active_trash",)),
            _Row("Cycle action — same subject(s) & scene, another act", (), ("active_cycle_action",)),
            _Row("Cycle seed — same config, different subject", (), ("active_cycle_seed",)),
            _Row("Loop the subject's actions — repeat that group", (), ("active_action_loop",)),
            _Row("Loop the act's other seeds — repeat that family", (), ("active_seed_loop",)),
            _Row("Filter the active side to the current clip's action", (), ("active_lock_action",)),
        ),
    ),
    (
        "Filters (satellite VLCs)",
        (
            _Row(
                "Filter both VLCs by act — say the act alone",
                (),
                set_commands_for_scope("both"),
                voice_display=spoken_forms_for_both(),
            ),
            _Row(
                "Filter one VLC — prefix “portrait” or “landscape”",
                (),
                set_commands_for_scope("portrait") + set_commands_for_scope("landscape"),
                voice_display=("portrait <act>", "landscape <act>"),
            ),
            _Row(
                "Clear a filter",
                (),
                (clear_command("both"), clear_command("portrait"), clear_command("landscape")),
                voice_display=("clear filter", "show everything", "clear portrait", "clear landscape"),
            ),
        ),
    ),
    (
        "Modes",
        (
            _Row("Genau mode", ("G",), ("genau_activate",), voice_display=("genau",)),
            _Row("Nau mode", ("N",), ("nau_activate",), voice_display=("nau mode",)),
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
            _Row("Set speed", (), (), ("speed 0–100",)),
            _Row("Previous waveform shape", (), ("genau_cycle_shape_prev",)),
            _Row("Next waveform shape", (",",), ("genau_cycle_shape",)),
            _Row("Previous Genau clip", ("M",), ("genau_prev_clip",)),
            _Row("Next Genau clip", (".",), ("genau_next_clip",)),
            _Row("Allow / suppress Genau takeover (OSR2 auto)", ("/",), ("genau_toggle_auto",)),
            _Row("Enable / disable cruise control", ("C",), ("genau_toggle_cruise", "genau_cruise_on", "genau_cruise_off")),
            _Row("Offset ¼ cycle", ("\\",), ("backslash_key", "quarter_button")),
        ),
    ),
)


# vosk can't hear "nau"/"genau", so mode-named phrases use the mode-activation
# sound-alikes as their recognizer form.  Show the friendly mode name in the
# reference instead of the raw sound-alike (e.g. "nau mode next", not "now mode
# next").  The sound-alikes only appear inside these derived nav phrases — the
# mode-activation rows themselves render via voice_display — so a plain replace
# is safe.
_VOICE_DISPLAY_ALIASES: tuple[tuple[str, str], ...] = (
    ("go now", "genau"),
    ("now mode", "nau mode"),
    # vosk has no "hotkeys" token, so the recognizer listens for "hot keys";
    # the reference shows the single-word "hotkeys".
    ("hot keys", "hotkeys"),
    # Likewise no "unmute" token — but there is "un", so the recognizer hears
    # the two-word "un mute" and the reference shows "unmute".
    ("un mute", "unmute"),
)


def friendly_voice(phrase: str) -> str:
    """Rewrite a recognizer phrase's vosk sound-alikes to the friendly names."""
    for raw, nice in _VOICE_DISPLAY_ALIASES:
        phrase = phrase.replace(raw, nice)
    return phrase


def _voice_for(commands: tuple[str, ...]) -> tuple[str, ...]:
    """Spoken phrases for *commands*, listed in command order.

    Phrases follow the order their commands appear in the row — so the Say
    column tracks the label (e.g. on before off, up before down) — with each
    command's own synonyms sorted among themselves.  Sound-alike phrases are
    shown under their friendly mode name (see :func:`friendly_voice`).
    """
    by_command: dict[str, list[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        if cmd in commands:
            by_command.setdefault(cmd, []).append(friendly_voice(phrase))
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
    # No heading or subtitle — the popup's title lives on the window chrome
    # ("Hotkeys & Voice Commands Reference"), so it opens straight into the first section.
    parts: list[str] = [
        f'<body style="background:{_BG};color:{_TEXT};'
        'font-family:\'Segoe UI\',sans-serif;font-size:10pt">',
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
