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

The satellites get ONE section: every satellite action answers to the same
phrase four ways over — bare, or with "portrait", "landscape" or "both" before
or after it — so the grid is authored once, with a key column per side, a note
carrying the scoping rule, and the Say column showing the action alone (see
:func:`_collapse_scopes`).
"""
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass

from fun_time.filter_vocab import display_forms, set_commands_for_scope
from fun_time.voice_commands import ORIGENERATOR_PHRASES, VOICE_COMMANDS, friendly_voice


@dataclass(frozen=True)
class CommandRef:
    """One reference row: an action and every way to trigger it."""

    description: str
    voice: tuple[str, ...]
    commands: tuple[str, ...]
    # ``hotkeys`` split across the section's key columns — one entry per column,
    # so a two-sided section can show the portrait key beside the landscape one.
    key_columns: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ReferenceSection:
    title: str
    rows: tuple[CommandRef, ...]
    key_headers: tuple[str, ...] = ("Key",)
    # Optional line under the section title, explaining something that would
    # otherwise have to be repeated on every row.
    note: str = ""


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
    # Keys for a second key column, in a section that declares two.
    hotkeys_alt: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Section:
    """Authored section data."""

    title: str
    rows: tuple[_Row, ...]
    key_headers: tuple[str, ...] = ("Key",)
    note: str = ""
    # Collapse the scope words out of the Say column (see :func:`_collapse_scopes`).
    merge_scopes: bool = False


#: The four ways to address a satellite action: name a side, name both, or name
#: none and reach whatever was last navigated.
_SCOPES = ("portrait", "landscape", "both", "active")

_SPOKEN_COMMANDS = frozenset(VOICE_COMMANDS.values())


def _sided(*actions: str) -> tuple[str, ...]:
    """Every scope's dispatch id for *actions*: ``"prev"`` -> all four ``_prev``.

    Authoring the shared grid by the action alone is what keeps the scopes from
    drifting apart again — a row cannot name one and forget another.  Ids no
    phrase reaches (a key-only toggle, say) are not generated, and belong in the
    row explicitly.
    """
    return tuple(
        command
        for action in actions
        for scope in _SCOPES
        if (command := f"{scope}_{action}") in _SPOKEN_COMMANDS
    )


# Authored reference, grouped by the part of the dashboard each row drives.
# ``commands`` are dispatch ids — voice phrases are looked up from them.
_SECTIONS: tuple[_Section, ...] = (
    _Section(
        "Global",
        (
            _Row("Quit — close everything", ("Ctrl+Alt+Q",), ("quit",)),
            _Row("Omnipause / resume", ("Esc",), ("omnipause_toggle", "pause", "play")),
            # Space and "pause" both enter Omnipause; "pause" is shown for parity.
            _Row("Omnipause", ("Space",), ("enter_omnipause",), ("pause",)),
            _Row(
                "Relief Omnipause — pause everything and retract the OSR2 away "
                "from you, rather than parking it",
                ("Shift+Esc",),
                ("relief_omnipause",),
            ),
            # The three modes are three rows, not a section of their own: each is
            # one key and one word, like everything else here that reshapes the room.
            _Row("Genau mode", ("G",), ("genau_activate",), voice_display=("genau",)),
            _Row("Nau mode", ("N",), ("nau_activate",), voice_display=("nau mode",)),
            _Row("Hybrid mode", ("H",), ("hybrid_activate",)),
            _Row(
                "Origenerator mode / player mode — the satellite side's own "
                "switch: Origenerator over the Random Favs Browser, its "
                "slideshows over the players, and back",
                ("X",),
                ("satellites_toggle", "origenerator_activate", "players_activate"),
                voice_display=("origenerator mode", "player mode"),
            ),
            # The hosted app's own vocabulary, said to one of its regions.  The
            # session owns the room's microphone, so these are heard here and
            # posted there as the words themselves; one row, because they are
            # one idea — say the side, then what you would have said to
            # Origenerator.
            _Row(
                "Speak to a hosted Origenerator region — the side, then its own "
                "words: a shelf to play (\"portrait favorites\", "
                "\"landscape experiments\"), the show's controls "
                "(\"landscape play slideshow\", \"portrait stop slideshow\"), "
                "a targeted fix (\"portrait fix teeth\"), \"go now\" to "
                "animate the picture as a Genau clip, or \"enhanced only\" to "
                "keep just the pictures the show has enhanced.  In this mode the "
                "side's own \"latest\", \"trash\" and \"no filter\" reach the "
                "show too",
                (),
                tuple(
                    f"{side}_say_{phrase.replace(' ', '_')}"
                    for side in ("portrait", "landscape")
                    for phrase in ORIGENERATOR_PHRASES
                ),
                voice_display=("landscape favorites", "portrait fix teeth"),
            ),
            _Row(
                "Toggle F-Mode on every player at once — spoken it needs the "
                "word \"all\", since bare \"f mode\" reaches the active player; "
                "each player also has its own, on its own HUD (see the sections "
                "below)",
                ("F",),
                ("fmode_toggle", "fmode_on", "fmode_off"),
            ),
            # The main player's sound, in whichever mode owns it — Nau's
            # video in nau/hybrid, Genau's clip audio in genau.  Its volume steps
            # sit with Nau's other playback controls.
            _Row("Mute / unmute the main player", (), ("audio_mute", "audio_unmute")),
            _Row("Disable voice control", (), ("voice_toggle", "voice_off")),
            _Row("Start / stop broker", ("B",), ("broker_panel", "broker_start", "broker_stop")),
            _Row(
                "Open / close this hotkeys & voice reference",
                (),
                ("help_reference", "help_reference_close"),
            ),
        ),
    ),
    _Section(
        "Genau",
        (
            _Row("Amplitude up / down", ("9", "7"), ("genau_amplitude_up", "genau_amplitude_down")),
            _Row("Set amplitude", (), (), ("min amp", "max amp", "amp 0–100")),
            _Row("Center up / down", ("O", "U"), ("genau_center_up", "genau_center_down")),
            _Row("Set center", (), (), ("min center", "max center", "center 0–100")),
            # Neither the keys nor the words name an engine, so in hybrid they
            # follow the OSR2's driver; the console's own ± marks, which sit on
            # one readout or the other, stay with the engine they sit on.
            _Row(
                "Speed up / down — the stroke's rate, or the video's playback "
                "rate while a funscript is driving the OSR2 (the script scales "
                "with it)",
                ("L", "J"),
                ("speed_up", "speed_down"),
            ),
            _Row("Set speed", (), (), ("speed 0–100",)),
            _Row("Previous waveform shape", (), ("genau_cycle_shape_prev",)),
            _Row("Next waveform shape", ("I",), ("genau_cycle_shape",)),
            _Row("Previous Genau clip", ("M",), ("genau_prev_clip",)),
            _Row("Next Genau clip", (".",), ("genau_next_clip",)),
            _Row("Mark the Genau clip weird — skip it, and out of rotation", ("K",), ("genau_weird_clip",)),
            # The same two commands Nau's section carries: one sound level reaches
            # both sinks, and which is audible is which mode owns the display.
            _Row("Volume down / up, in tenths — the clip music", (), ("audio_volume_down", "audio_volume_up")),
            _Row("Allow / suppress Genau takeover (OSR2 auto)", ("/",), ("genau_toggle_auto",)),
            _Row("Enable / disable cruise control (varies the stroke)", ("C",), ("genau_toggle_cruise", "genau_cruise_on", "genau_cruise_off")),
            _Row(
                "Hold the stroke still — cruise off, no amplitude, and the "
                "center at one end: park settles the OSR2 home, retract sends "
                "it to the far end, away from you.  Unlike OmniPause the room "
                "plays on",
                (),
                ("genau_park", "genau_retract"),
            ),
            _Row(
                "Off the hold — put the stroke back to whatever it was doing "
                "before the park or retract, cruise included.  Any of the three "
                "words undoes either hold",
                (),
                ("genau_release",),
            ),
            _Row(
                "Seconds a clip holds the screen before Genau moves on — only "
                "while it is unlocked (the ' key in Nau holds it)",
                (),
                ("genau_clip_seconds_down", "genau_clip_seconds_up"),
                ("clip seconds 1–60",),
            ),
            _Row("Offset ¼ cycle", ("\\",), ("backslash_key", "quarter_button")),
        ),
    ),
    _Section(
        "Nau",
        (
            _Row("Previous video", ("[",), ("main_prev",)),
            _Row("Next video", ("]",), ("main_next",)),
            _Row(
                "Lock / unlock the main player — locked (the default) what is on "
                "screen repeats; unlocked it moves on and the list runs around. "
                "Reaches whichever player is showing: Nau's video here, Genau's "
                "clip in Genau mode",
                ("'",),
                ("main_lock", "main_lock_on", "main_lock_off"),
            ),
            _Row("Nudge back 10 seconds", ("-",), ("main_nudge_prev",)),
            _Row("Nudge forward 10 seconds", ("=",), ("main_nudge_next",)),
            _Row("Volume down / up, in tenths — the video's sound", (), ("audio_volume_down", "audio_volume_up")),
            _Row(
                "Cycle the video's VR projection — flat screen, 180° SBS, "
                "fisheye 190, MKX200, 360 — remembered per video (FunTimeVR)",
                ("P",),
                ("projection_cycle",),
            ),
            _Row(
                "Recenter the VR scene onto wherever the headset faces now "
                "(FunTimeVR)",
                ("Z",),
                ("recenter_view",),
            ),
            # Named for the playback, so it reaches the video whoever holds the
            # OSR2 — the way to nudge the rate through a Genau-driven stretch in
            # hybrid, where the bare "speed up" goes to the stroke instead.
            _Row(
                "Nudge the video's playback rate up / down",
                (),
                ("nau_speed_up", "nau_speed_down"),
            ),
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
            _Row("Latest main — reload it newest-first", (), ("main_latest",)),
            _Row("Shuffle main — reshuffle it (cancels Latest; keeps F-mode)", (),
                 ("main_shuffle",)),
            _Row(
                "Length of what plays: \"mixed\" (the default, everything), "
                "\"shorts\", or \"full length\" — \"mixed\" leaves any "
                "compilation with it",
                ("T",),
                ("nau_toggle_length", "nau_length_shorts", "nau_length_full",
                 "nau_length_mixed"),
            ),
            _Row(
                "Reset the main player — back to the mixed library (leaving any "
                "compilation) with F-Mode off",
                (),
                ("main_reset",),
            ),
            _Row(
                "Clip navigation: \"compilation\" plays the clip's compilation "
                "in order and \"end compilation\" leaves it for the length mode "
                "you were in; \"full video\" jumps to its source scene; \"money "
                "jump\" returns to the clip",
                (),
                ("nau_compilation", "nau_end_compilation", "nau_full_vid",
                 "nau_clip_jump"),
            ),
            _Row(
                "Funscript navigation: \"jump to funscript\" skips ahead to "
                "where this video's scripting starts up again; \"next "
                "funscripted\" leaves for the next scripted video in the "
                "playlist, landing where its action begins",
                (),
                ("nau_funscript_jump", "nau_next_funscripted"),
            ),
            _Row(
                "Loop control: hold and release to set a loop, press to end loop",
                ("R",),
                ("nau_record_down", "nau_record_up", "nau_record_tap", "nau_loop_cancel"),
            ),
            _Row(
                "F-Mode on the main player alone — play only the videos that "
                "have a funscript",
                (),
                ("main_fmode", "main_fmode_on", "main_fmode_off"),
            ),
            _Row("Open file browser", ("\\",), ("backslash_key", "browse_library")),
            _Row("Save clip (Clipper)", (";",), ("clipper_save",)),
        ),
    ),
    _Section(
        "Satellites",
        (
            _Row("Previous clip", ("Left",), _sided("prev"), hotkeys_alt=("A",)),
            _Row("Next clip", ("Right",), _sided("next"), hotkeys_alt=("D",)),
            _Row(
                "Unfavorite the clip — or mark it weird when it is not a favorite",
                ("Up",),
                _sided("trash"),
                hotkeys_alt=("W",),
            ),
            # portrait_lock / landscape_lock are the keys' own toggle, which no
            # phrase reaches — so _sided cannot find them, and they are named here.
            _Row(
                "Lock / unlock",
                ("Down",),
                ("portrait_lock", "landscape_lock") + _sided("lock_on", "lock_off"),
                hotkeys_alt=("S",),
            ),
            _Row("Cycle action — same subject(s) & scene, another act", (), _sided("cycle_action")),
            _Row("Cycle seed — same config, different subject", (), _sided("cycle_seed")),
            _Row(
                "Wrong action — the clip's act is mislabeled; strike it so "
                "Evolver's backfill tool asks about it again",
                (),
                _sided("wrong_action"),
            ),
            _Row(
                # Both key columns run left, right, up, down, so the two sides'
                # keycaps line up with each other rather than reading as WASD.
                "Navigate the map — move a selection (left, right, up, down), "
                "switching to that clip",
                ("Shift+Left", "Shift+Right", "Shift+Up", "Shift+Down"),
                (
                    "portrait_nav_left", "portrait_nav_right",
                    "portrait_nav_up", "portrait_nav_down",
                    "landscape_nav_left", "landscape_nav_right",
                    "landscape_nav_up", "landscape_nav_down",
                ),
                hotkeys_alt=("Shift+A", "Shift+D", "Shift+W", "Shift+S"),
            ),
            _Row("More seeds — widen to same-scene near-matches", (), _sided("more_seeds")),
            _Row("Loop the subject's actions — repeat that group", (), _sided("action_loop")),
            _Row("Loop the act's other seeds — repeat that family", (), _sided("seed_loop")),
            # The loop cycle is the keys' own command, like the lock toggle above.
            _Row(
                "Step the loop on — seeds, then actions, then off",
                ("Home",),
                ("portrait_loop", "landscape_loop"),
                hotkeys_alt=("E",),
            ),
            _Row("Stop looping — back to browse, keep the filter", (), _sided("no_loop")),
            _Row(
                "Filter by act — say the act on its own",
                (),
                set_commands_for_scope("both")
                + set_commands_for_scope("portrait")
                + set_commands_for_scope("landscape"),
                # The acts under their real names, not the sound-alikes the
                # recognizer's grammar is built from (see filter_vocab).
                voice_display=display_forms(),
            ),
            _Row("Filter to the current clip's action", (), _sided("lock_action")),
            _Row("Drop the filter — keep the order and everything else", (), _sided("no_filter")),
            _Row("Latest — reload newest-first", (), _sided("latest")),
            _Row("Shuffle — reshuffle (cancels Latest; keeps the filter)", (), _sided("shuffle")),
            _Row("F-Mode — browse only the favorites", (), _sided("fmode", "fmode_on", "fmode_off")),
            _Row("Reset — back to every default: no filter, no lock, no loop, no F-Mode, shuffled from the top", (), _sided("reset")),
        ),
        key_headers=("Portrait", "Landscape"),
        note="Alone, a phrase reaches whatever you last navigated — a side, or "
        "the main player.  Add “portrait”, “landscape” or “both”, before it or "
        "after, to aim it: “portrait next”, “next both”.",
        merge_scopes=True,
    ),
)


#: The words that aim a satellite phrase at a scope.  "active" is not among them:
#: it is the scope you get by saying none of these.
_SCOPE_WORDS = frozenset(("portrait", "landscape", "both"))


def _collapse_scopes(phrase: str) -> str:
    """Strip the scope word out of *phrase*, leaving the action alone.

    Every satellite action answers to the same phrase eight ways — bare, or with
    any of three scope words, each in either order — so a row that listed them
    all would print its own action eight times over.  The section's note carries
    the rule instead, and dropping the scope word folds every reading onto one:
    "portrait next", "next both" and "next" all become "next".
    """
    return " ".join(word for word in phrase.split() if word not in _SCOPE_WORDS)


def _voice_for(
    commands: tuple[str, ...], *, fold: Callable[[str], str] | None = None
) -> tuple[str, ...]:
    """Spoken phrases for *commands*, listed in command order.

    Phrases follow the order their commands appear in the row — so the Say
    column tracks the label (e.g. on before off, up before down) — with each
    command's own synonyms sorted among themselves.  Sound-alike phrases are
    shown under their friendly mode name (see :func:`friendly_voice`).

    *fold* rewrites each phrase before any of that, so what is sorted and
    deduplicated is what the reader will actually see.
    """
    by_command: dict[str, set[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        if cmd in commands:
            shown = friendly_voice(phrase)
            if fold is not None:
                shown = fold(shown)
            if shown:
                by_command.setdefault(cmd, set()).add(shown)
    result: list[str] = []
    for cmd in commands:
        for phrase in sorted(by_command.get(cmd, ())):
            if phrase not in result:
                result.append(phrase)
    return tuple(result)


def _display_voice(row: _Row, *, merge_scopes: bool) -> tuple[str, ...]:
    """The Say column for *row* — derived from its commands unless aliased."""
    if row.voice_display is not None:
        return row.voice_display
    fold = _collapse_scopes if merge_scopes else None
    return _voice_for(row.commands, fold=fold) + row.literal_voice


def build_reference_sections() -> tuple[ReferenceSection, ...]:
    """Build the display reference, deriving voice phrases from VOICE_COMMANDS."""
    sections: list[ReferenceSection] = []
    for section in _SECTIONS:
        two_column = len(section.key_headers) == 2
        refs = tuple(
            CommandRef(
                description=row.description,
                voice=_display_voice(row, merge_scopes=section.merge_scopes),
                commands=row.commands,
                key_columns=(
                    (row.hotkeys, row.hotkeys_alt) if two_column else (row.hotkeys,)
                ),
            )
            for row in section.rows
        )
        sections.append(
            ReferenceSection(
                title=section.title,
                rows=refs,
                key_headers=section.key_headers,
                note=section.note,
            )
        )
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


def _cell(content: str, *, width: str = "") -> str:
    width_attr = f' width="{width}"' if width else ""
    return (
        f'<td valign="top"{width_attr} '
        f'style="padding:3px 8px;color:{_TEXT}">{content}</td>'
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
        if section.note:
            parts.append(
                f'<div style="color:{_MUTED};margin:0 0 4px 0">'
                f"{html.escape(section.note)}</div>"
            )
        # A two-sided section spends its width on a key column per side, so the
        # remaining columns give some back.
        two_column = len(section.key_headers) == 2
        key_width = "15%" if two_column else "22%"
        say_width = "32%" if two_column else "34%"
        parts.append('<table width="100%" cellspacing="0" cellpadding="0">')
        headers = "".join(
            _cell(f'<b style="color:{_MUTED}">{html.escape(header)}</b>', width=key_width)
            for header in section.key_headers
        )
        parts.append(
            f'<tr style="background:{_HEADER_BG}">'
            + _cell(f'<b style="color:{_MUTED}">Action</b>')
            + headers
            + _cell(f'<b style="color:{_MUTED}">Say</b>', width=say_width)
            + "</tr>"
        )
        for row in section.rows:
            keys = "".join(
                _cell(
                    " ".join(_keycap(k) for k in column)
                    or f'<span style="color:{_MUTED}">—</span>',
                    width=key_width,
                )
                for column in row.key_columns
            )
            phrases = (
                ", ".join(f'“{html.escape(p)}”' for p in row.voice)
                or f'<span style="color:{_MUTED}">—</span>'
            )
            parts.append(
                "<tr>"
                + _cell(html.escape(row.description))
                + keys
                + _cell(phrases, width=say_width)
                + "</tr>"
            )
        parts.append("</table>")
    parts.append("</body>")
    return "".join(parts)
