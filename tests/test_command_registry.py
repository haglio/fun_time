"""The command registry's completeness net over the five surfaces.

A command exists on up to five surfaces: the spoken vocabulary
(voice_commands), the AHK hotkey script, the in-app reference
(command_reference), the dispatch loop's pre-dispatch branches, and the
dispatcher's handler map.  They used to be tied by nothing but a shared
literal, and nothing failed when one was missed — a phrase mapped to a
misspelled id was a dead phrase, a handler no surface reached was dead code,
and a new command's forgotten reference row just never showed up.

The handler map (``_build_handlers`` in command_dispatch) is the definition
site: every command id is bound to its handler exactly once there.  These
tests close the loop in both directions across all five surfaces, so missing
any of them fails by name.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import fun_time.command_dispatch as command_dispatch
import fun_time.dashboard_actions as dashboard_actions
from fun_time.command_reference import build_reference_sections
from fun_time.voice_commands import VOICE_COMMANDS
from fun_time.windows_bridge_dispatch_loop import (
    _MAIN_EQUIVALENTS,
    expand_both_command,
    resolve_active_side_command,
)

from tests.test_command_id_snapshot import (
    HUD_ONLY_COMMAND_IDS,
    _ahk_ids,
    _expected_filter_ids,
    _expected_numeric_ids,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Commands posted straight off a player's own surface, as literal strings in
# player_core (the satellite HUDs and the main console) — the reachability the
# in-repo surfaces cannot show.  HUD_ONLY_COMMAND_IDS is the subset the
# reference does not list either; these three it does.
_CONSOLE_POSTED = frozenset(HUD_ONLY_COMMAND_IDS) | {
    "genau_clip_seconds_down",
    "genau_clip_seconds_up",
    "nau_record_tap",
}

# The nav ids are parsed, not exact keys, so the handler map does not list
# them; the AHK script and the reference both spell them out.
_NAV_IDS = frozenset(
    f"{side}_nav_{direction}"
    for side in ("portrait", "landscape")
    for direction in ("left", "right", "up", "down")
)


def _handler_ids() -> frozenset[str]:
    return frozenset(command_dispatch._HANDLERS)


def _loop_branch_ids() -> frozenset[str]:
    """The ids _handle_command branches on, read from its source.

    The loop's if/elif IS the definition of what it intercepts; a parallel
    hand-kept list would be one more surface to miss.  Every comparison in it
    is a literal (or the HELP_REFERENCE_COMMANDS constant), so the parse is
    exact — a new non-literal branch shape fails the assert below.
    """
    source = (_REPO_ROOT / "fun_time" / "windows_bridge_dispatch_loop.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    handle = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_command"
    )
    ids: set[str] = set()
    for node in ast.walk(handle):
        if not isinstance(node, ast.Compare):
            continue
        left, comparator = node.left, node.comparators[0]
        if not (isinstance(left, ast.Name) and left.id == "cmd"):
            continue
        if isinstance(node.ops[0], ast.NotIn):
            continue  # the omnipause suspend-exempt guard, not a dispatch branch
        if isinstance(comparator, ast.Constant):
            ids.add(comparator.value)
        elif isinstance(comparator, ast.Tuple):
            ids.update(element.value for element in comparator.elts)
        elif isinstance(comparator, ast.Name):
            assert comparator.id == "HELP_REFERENCE_COMMANDS", ast.dump(node)
            ids.update(dashboard_actions.HELP_REFERENCE_COMMANDS)
        else:  # pragma: no cover - a new branch shape must be classified here
            raise AssertionError(f"unrecognized _handle_command comparison: {ast.dump(node)}")
    return frozenset(ids)


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
            for command in expand_both_command(resolve_active_side_command(value, side)):
                (residues if command.startswith("active_") else targets).add(command)
    return frozenset(targets), frozenset(residues)


def _families() -> frozenset[str]:
    return frozenset(_expected_numeric_ids()) | frozenset(_expected_filter_ids())


def test_every_spoken_phrase_lands_on_a_handler():
    """Surface 1 → 5: a phrase mapped to an id nothing handles is a dead phrase."""
    targets, _ = _voice_resolutions()
    handled = _handler_ids() | _loop_branch_ids() | _families() | _NAV_IDS
    dead = targets - handled
    assert not dead, f"spoken commands with no handler: {sorted(dead)}"


def test_the_unresolvable_active_forms_are_exactly_the_satellite_only_actions():
    """A bare "weird" or "cycle seed" spoken while the main player holds the
    floor resolves to nothing on purpose — but only for actions whose sided
    forms ARE handled, or the residue would be hiding a genuinely dead phrase."""
    _, residues = _voice_resolutions()
    for residue in residues:
        action = residue[len("active_"):]
        assert action not in _MAIN_EQUIVALENTS, residue
        assert f"portrait_{action}" in _handler_ids(), residue
        assert f"landscape_{action}" in _handler_ids(), residue


def test_every_ahk_binding_lands_on_a_handler():
    """Surface 2 → 4/5: a key queued to a misspelled id is a dead key."""
    handled = _handler_ids() | _loop_branch_ids() | _NAV_IDS
    dead = _ahk_ids() - handled
    assert not dead, f"AHK bindings with no handler: {sorted(dead)}"


def test_every_handler_is_reachable_from_some_surface():
    """Surface 5 → 1/2/3/4: a handler no phrase, key, loop translation or
    player surface can reach is dead code wearing a command id."""
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
    for branch in _loop_branch_ids():
        assert branch in targets | _ahk_ids() | dashboard, (
            f"loop branch {branch!r} is reachable from no surface"
        )


def test_the_reference_and_the_handlers_agree():
    """Surface 3 ↔ 5: every reference row names real commands, and the only
    handled ids the reference omits are the two the console alone posts."""
    reference = _reference_ids()
    real = (
        _handler_ids()
        | _loop_branch_ids()
        | _families()
        | _NAV_IDS
        # The reference documents the side-agnostic and both forms as such.
        | frozenset(c for c in reference if c.startswith(("active_", "both_")))
    )
    ghosts = reference - real
    assert not ghosts, f"reference rows naming unhandled commands: {sorted(ghosts)}"

    undocumented = _handler_ids() - reference - {"genau_speed_down", "genau_speed_up"}
    assert not undocumented, f"handled commands the reference omits: {sorted(undocumented)}"


def test_the_origenerator_shadow_set_is_exactly_the_transport_and_latest_pair():
    """The routing guard runs ahead of the handler map, so the ids it may
    shadow are pinned: the five transport verbs per side, plus each side's
    "latest" (which the hosted app answers as its newest-first listing) and
    each side's "no filter" -- on a player it drops the act filter, and on a
    hosted show it drops both switches on the show's HUD (F-mode and
    enhanced-only), the same gesture in the same words, so one phrase serves
    both modes.  Anything else joining the routed set must be argued here
    first."""
    routed = set(command_dispatch._ORIGENERATOR_TRANSPORT) | set(
        command_dispatch._ORIGENERATOR_SPEECH
    )
    player_handled = {
        command
        for command in routed
        if command_dispatch._HANDLERS.get(command)
        is not command_dispatch._words_for_a_show_that_is_not_up
        and command in command_dispatch._HANDLERS
    }
    assert player_handled == set(command_dispatch._ORIGENERATOR_TRANSPORT) | {
        "portrait_latest",
        "landscape_latest",
        "portrait_no_filter",
        "landscape_no_filter",
    }, sorted(player_handled)
