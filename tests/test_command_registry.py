"""The command registry's completeness net over the five surfaces.

A command exists on up to five surfaces: the spoken vocabulary
(voice_commands), the AHK hotkey script, the in-app reference
(command_reference), the dispatch loop's pre-dispatch branches, and the
dispatcher's handler map.  They used to be tied by nothing but a shared
literal, and nothing failed when one was missed — a phrase mapped to a
misspelled id was a dead phrase, a handler no surface reached was dead code,
and a new command's forgotten reference row just never showed up.

Each domain's ``HANDLERS`` table is the definition site, and command_dispatch
merges them once, refusing an id bound in two.  These tests close the loop in
both directions across all five surfaces, so missing any of them fails by name.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from funestra.console import osr2_controls
from funestra.modes import LengthMode, MainMode, SatellitesMode

from fun_time import command_dispatch, dashboard_actions, hud_moves, windows_bridge_dispatch_loop
from fun_time.aiming import MAIN_EQUIVALENTS, resolve_active_funestra_command
from fun_time.command_reference import build_reference_sections
from fun_time.console_buttons import MainSlot, console_rows
from fun_time.satellite_buttons import funestra_rows
from fun_time.voice_commands import VOICE_COMMANDS
from fun_time.windows_bridge_dispatch_loop import REQUEST_NEWS, expand_group_command
from tests.test_command_id_snapshot import (
    HUD_ONLY_COMMAND_IDS,
    _ahk_ids,
    _expected_filter_ids,
    _expected_numeric_ids,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Commands posted straight off a Funestra's own surface, as literal strings in
# funestra (the satellite HUDs and the main console) — the reachability the
# in-repo surfaces cannot show.  HUD_ONLY_COMMAND_IDS is the subset the
# reference does not list either; the rest it does.
_CONSOLE_POSTED = frozenset(HUD_ONLY_COMMAND_IDS) | {
    "genau_flick_seconds_down",
    "genau_flick_seconds_up",
    # Neither shape of video, and neither length: the browse with nothing in
    # it.  The reference names both, but no phrase asks for either -- each takes
    # a second, deliberate press on the button that is already the only one lit.
    "main_projection_none",
    "main_player_length_none",
    "main_orientation_none",
    "main_player_record_tap",
    # A VR controller's stick pushed left; pushed right it posts the P key's own id.
    "projection_cycle_back",
}

# The nav ids are parsed, not exact keys, so the handler map does not list
# them; the AHK script and the reference both spell them out.
_NAV_IDS = frozenset(
    f"{side}_nav_{direction}"
    for side in ("portrait", "landscape")
    for direction in ("left", "right", "up", "down")
)

#: Where each HUD sits, and whether it is collapsed: read off the dispatch's own
#: tables, so a side or a verb added there needs nothing added here.
_HUD_PLACE_IDS = (frozenset(hud_moves._HUD_MOVES)
                  | frozenset(hud_moves.MAIN_HUD_TURNS)
                  | frozenset(hud_moves._HUD_COLLAPSES))


def _handler_ids() -> frozenset[str]:
    return frozenset(command_dispatch._HANDLERS)


def _loop_branch_ids() -> frozenset[str]:
    """The ids the loop answers itself: its own command table, and the headset's
    two signals, which it takes ahead of the table."""
    return (frozenset(windows_bridge_dispatch_loop._LOOP_COMMANDS)
            | dashboard_actions.HEADSET_SIGNALS)


def _reference_ids() -> frozenset[str]:
    return frozenset(
        command
        for section in build_reference_sections()
        for row in section.rows
        for command in row.commands
    )


def _voice_resolutions() -> tuple[frozenset[str], frozenset[str]]:
    """(resolved targets, unresolvable ``active_*`` residues) of every phrase.

    Resolution uses the loop's own resolver and expander, per possible active
    side, so this can never drift from what a session actually does.
    """
    targets: set[str] = set()
    residues: set[str] = set()
    for value in set(VOICE_COMMANDS.values()):
        for side in (1, 2, 3):
            for command in expand_group_command(resolve_active_funestra_command(value, side)):
                (residues if command.startswith("active_") else targets).add(command)
    return frozenset(targets), frozenset(residues)


def _families() -> frozenset[str]:
    return frozenset(_expected_numeric_ids()) | frozenset(_expected_filter_ids())


def _console_verbs() -> frozenset[str]:
    """Every verb a console button Fun Time declares can post, over every state
    that picks among them: a lit length button asks for mixed where a dark one
    asks for its own length, and the projection pair has four states to reach."""
    declared = (
        button.command
        for mode, plays_vr, plays_flat, length_mode, compilation, jump_to, has_osr2, in_vr
        in itertools.product(MainMode, (False, True), (False, True), LengthMode,
                             ("", "Volume One"), ("", "scene", "clip"), (False, True),
                             (False, True))
        for row in console_rows(
            MainSlot(main_mode=mode, latest=False, plays_vr=plays_vr, plays_flat=plays_flat,
                     length_mode=length_mode, compilation=compilation, has_compilation=True,
                     jump_to=jump_to, has_osr2=has_osr2),
            in_vr=in_vr)
        for button in row
    )
    brokers = (button.command for broker in (False, True) for button in osr2_controls(broker_running=broker))
    return frozenset(command for command in (*declared, *brokers) if command)


def _satellite_verbs() -> frozenset[str]:
    """Every verb a satellite HUD button Fun Time declares can post, over both
    sides and every state the declaration takes."""
    return frozenset(
        button.command
        for funestra in ("portrait", "landscape")
        for latest in (None, False, True)
        for mode in (None, *SatellitesMode)
        for row in funestra_rows(funestra, locked=True, favorites_filter=True, latest=latest,
                               mode=mode)
        for button in row
    )


def test_every_verb_the_console_posts_lands_on_a_handler():
    """The sixth surface: the console the Funestras draw for the main slot posts
    the verbs of the buttons Fun Time declares into this dispatcher.  Nothing
    held the two together once, which is how the flick-seconds pair came to
    post a verb this table had renamed away -- both buttons inert in genau
    mode, and no test to say so (bug 19)."""
    # Minimize is answered before the handler map, by name; browse and the
    # broker panel are the loop's own branches.
    answered = (_handler_ids() | _loop_branch_ids()
                | frozenset(hud_moves._MINIMIZE_ROLES))
    posted = _console_verbs()
    assert posted, "no console button posts anything"
    assert answered >= posted, sorted(posted - answered)


def test_every_verb_a_satellite_hud_posts_lands_on_a_handler():
    """The same surface, for the buttons Fun Time declares on each satellite's
    HUD: each side's own verbs, its mode pair among them, and the minimize the
    loop answers by name."""
    answered = (_handler_ids() | _loop_branch_ids()
                | frozenset(hud_moves._MINIMIZE_ROLES))
    posted = _satellite_verbs()
    assert posted, "no satellite button posts anything"
    assert answered >= posted, sorted(posted - answered)


def test_every_spoken_phrase_lands_on_a_handler():
    """Surface 1 → 5: a phrase mapped to an id nothing handles is a dead phrase."""
    targets, _ = _voice_resolutions()
    handled = (_handler_ids() | _loop_branch_ids() | _families() | _NAV_IDS
               | _HUD_PLACE_IDS)
    dead = targets - handled
    assert not dead, f"spoken commands with no handler: {sorted(dead)}"


def test_the_unresolvable_active_forms_are_exactly_the_satellite_only_actions():
    """A bare "weird" or "cycle seed" spoken while the Main Funestra holds the
    floor resolves to nothing on purpose — but only for actions whose sided
    forms ARE handled, or the residue would be hiding a genuinely dead phrase."""
    _, residues = _voice_resolutions()
    for residue in residues:
        action = residue[len("active_"):]
        assert action not in MAIN_EQUIVALENTS, residue
        assert f"portrait_{action}" in _handler_ids(), residue
        assert f"landscape_{action}" in _handler_ids(), residue


def test_every_ahk_binding_lands_on_a_handler():
    """Surface 2 → 4/5: a key queued to a misspelled id is a dead key."""
    handled = _handler_ids() | _loop_branch_ids() | _NAV_IDS | _HUD_PLACE_IDS
    dead = _ahk_ids() - handled
    assert not dead, f"AHK bindings with no handler: {sorted(dead)}"


def test_every_handler_is_reachable_from_some_surface():
    """Surface 5 → 1/2/3/4: a handler no phrase, key, loop translation or
    Funestra surface can reach is dead code wearing a command id."""
    targets, _ = _voice_resolutions()
    reachable = (
        targets
        | _ahk_ids()
        | frozenset(
            getattr(dashboard_actions, name)
            for name in dir(dashboard_actions)
            if isinstance(getattr(dashboard_actions, name), str) and not name.startswith("_")
        )
        | _CONSOLE_POSTED
        | _satellite_verbs()
        # The loop translates the idempotent lock forms onto the bare toggles.
        | {"portrait_lock", "landscape_lock"}
    )
    unreachable = _handler_ids() - reachable
    assert not unreachable, f"handlers nothing can reach: {sorted(unreachable)}"


def test_every_loop_branch_is_reachable_and_known():
    """Surface 4: the loop's own branch set, gated against how each id arrives."""
    targets, _ = _voice_resolutions()
    dashboard = frozenset(
        getattr(dashboard_actions, name)
        for name in dir(dashboard_actions)
        if isinstance(getattr(dashboard_actions, name), str) and not name.startswith("_")
    )
    told_by_the_hosted_app = frozenset(REQUEST_NEWS)
    for branch in _loop_branch_ids():
        assert branch in targets | _ahk_ids() | dashboard | told_by_the_hosted_app, (
            f"loop branch {branch!r} is reachable from no surface"
        )


def test_the_reference_and_the_handlers_agree():
    """Surface 3 ↔ 5: every reference row names real commands, and the only
    handled ids the reference omits are the ones a console alone posts —
    HUD_ONLY_COMMAND_IDS, which is what "no phrase, key or row names it" means."""
    reference = _reference_ids()
    real = (
        _handler_ids()
        | _loop_branch_ids()
        | _families()
        | _NAV_IDS
        | _HUD_PLACE_IDS
        # The reference documents the side-agnostic and group forms as such.
        | frozenset(c for c in reference
                    if c.startswith(("active_", *windows_bridge_dispatch_loop._FUNESTRA_GROUPS)))
    )
    ghosts = reference - real
    assert not ghosts, f"reference rows naming unhandled commands: {sorted(ghosts)}"

    undocumented = _handler_ids() - reference - frozenset(HUD_ONLY_COMMAND_IDS)
    assert not undocumented, f"handled commands the reference omits: {sorted(undocumented)}"


def test_the_session_keeps_only_the_funestras_own_controls_in_origenerator_mode():
    """The routing guard runs ahead of the handler map, so what it may shadow is
    pinned by rule: every side command about what the Funestra plays goes to the
    hosted app, and the session keeps only what is about the Funestra itself --
    parking its window, the rate it plays at, and its mode, which is how it is
    taken back -- and its lock, which locks the Funestra the moment it is asked
    for, since a slideshow moves on while the app has yet to read a press.  A side
    command the session must go on answering while the app has the Funestra has
    to be argued here."""
    side_commands = {command for command in command_dispatch._HANDLERS
                     if command.startswith(("portrait_", "landscape_"))}
    kept = {command for command in side_commands
            if not command_dispatch._about_a_satellite(command)}

    assert kept == {
        f"{side}_{own}"
        for side in ("portrait", "landscape")
        for own in ("minimize", "crown", "speed_up", "speed_down", "speed_reset",
                    "speed_min", "speed_max", "take_osr2", "kino_activate",
                    "origenerator_activate", "lock", "lock_on", "lock_off")
    } & side_commands, sorted(kept)


def test_a_command_bound_in_two_domains_is_refused_at_import():
    """One id, one handler: a domain that binds an id another already has would
    silently win or lose by the order the tables are merged in."""
    def first(state, _config, _target_path):
        return state, []

    with pytest.raises(ValueError, match="bound twice.*portrait_next"):
        command_dispatch._bound_once([{"portrait_next": first}, {"portrait_next": first}])

