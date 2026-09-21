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
from pathlib import Path

from player_core.modes import SatellitesMode

from fun_time import command_dispatch, dashboard_actions, windows_bridge_dispatch_loop
from fun_time.command_reference import build_reference_sections
from fun_time.satellite_buttons import player_rows
from fun_time.voice_commands import VOICE_COMMANDS
from fun_time.windows_bridge_dispatch_loop import (
    _MAIN_EQUIVALENTS,
    expand_group_command,
    resolve_active_player_command,
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
    # Neither shape of video, and neither length: the browse with nothing in
    # it.  The reference names both, but no phrase asks for either -- each takes
    # a second, deliberate press on the button that is already the only one lit.
    "main_projection_none",
    "main_player_length_none",
    "main_player_record_tap",
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
    hand-kept list would be one more surface to miss.  Every comparison in it is
    a literal or a named table of ids the loop imports (HELP_REFERENCE_COMMANDS,
    HANDOFF_COMMANDS), so the parse is exact — a new branch shape, or a name
    that is not a collection of ids, fails an assert below.
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
            # A branch that reaches for a constant rather than repeating a
            # literal — one spelling, two files.  dashboard_actions holds the
            # ids the bar posts; the loop's own module holds the tables that
            # group several of them under one branch.
            named = getattr(dashboard_actions, comparator.id,
                            getattr(windows_bridge_dispatch_loop, comparator.id, None))
            assert named is not None, ast.dump(node)
            ids.update({named} if isinstance(named, str) else named)
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
            for command in expand_group_command(resolve_active_player_command(value, side)):
                (residues if command.startswith("active_") else targets).add(command)
    return frozenset(targets), frozenset(residues)


def _families() -> frozenset[str]:
    return frozenset(_expected_numeric_ids()) | frozenset(_expected_filter_ids())


def _console_verbs() -> frozenset[str]:
    """Every verb a console button Fun Time declares can post, read off the
    source of the declaration.  Not only a bare literal: a button whose verb
    depends on what it is showing picks among them there -- a lit length button
    asks for mixed, a dark one for its own length -- and the projection pair,
    with four states to reach, nests the choice."""
    source = (_REPO_ROOT / "fun_time" / "console_buttons.py").read_text(encoding="utf-8")

    def literals(node):
        if isinstance(node, ast.IfExp):
            return literals(node.body) | literals(node.orelse)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
            return {node.value}
        return set()

    posted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Button" and node.args:
            posted.update(literals(node.args[0]))
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "MODE_BUTTONS" for t in node.targets):
            posted.update(entry.elts[0].value for entry in node.value.elts)
    return frozenset(posted)


def _satellite_verbs() -> frozenset[str]:
    """Every verb a satellite HUD button Fun Time declares can post, over both
    sides and every state the declaration takes."""
    return frozenset(
        button.command
        for player in ("portrait", "landscape")
        for latest in (None, False, True)
        for satellites_mode in (None, *SatellitesMode)
        for row in player_rows(player, locked=True, favorites_filter=True, latest=latest,
                               satellites_mode=satellites_mode)
        for button in row
    )


def test_every_verb_the_console_posts_lands_on_a_handler():
    """The sixth surface: the console the players draw for the main slot posts
    the verbs of the buttons Fun Time declares into this dispatcher.  Nothing
    held the two together once, which is how the clip-seconds pair came to
    post a verb this table had renamed away -- both buttons inert in genau
    mode, and no test to say so (bug 19).  The enhanced-filter button was the
    last one carried here as dormant, unanswered because no session had a
    filter to narrow (bug 90)."""
    # Minimize is answered before the handler map, by name; browse and the
    # broker panel are the loop's own branches.
    answered = _handler_ids() | _loop_branch_ids() | {command_dispatch.MAIN_MINIMIZE}
    posted = _console_verbs()
    assert posted, "no console button posts anything"
    assert answered >= posted, sorted(posted - answered)


def test_every_verb_a_satellite_hud_posts_lands_on_a_handler():
    """The same surface, for the buttons Fun Time declares on each satellite's
    HUD: each side's own verbs, the side-less mode pair, and the minimize the
    loop answers by name."""
    answered = (_handler_ids() | _loop_branch_ids()
                | frozenset(command_dispatch._MINIMIZE_ROLES))
    posted = _satellite_verbs()
    assert posted, "no satellite button posts anything"
    assert answered >= posted, sorted(posted - answered)


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
    handled ids the reference omits are the ones a console alone posts —
    HUD_ONLY_COMMAND_IDS, which is what "no phrase, key or row names it" means."""
    reference = _reference_ids()
    real = (
        _handler_ids()
        | _loop_branch_ids()
        | _families()
        | _NAV_IDS
        # The reference documents the side-agnostic and group forms as such.
        | frozenset(c for c in reference
                    if c.startswith(("active_", *windows_bridge_dispatch_loop._PLAYER_GROUPS)))
    )
    ghosts = reference - real
    assert not ghosts, f"reference rows naming unhandled commands: {sorted(ghosts)}"

    undocumented = _handler_ids() - reference - frozenset(HUD_ONLY_COMMAND_IDS)
    assert not undocumented, f"handled commands the reference omits: {sorted(undocumented)}"


def test_the_session_keeps_only_the_players_own_controls_in_origenerator_mode():
    """The routing guard runs ahead of the handler map, so what it may shadow is
    pinned by rule: every side command about what the player plays goes to the
    hosted app, and the session keeps only what is about the player itself --
    parking its window, and the rate it plays at.  A side command the session
    must go on answering while the app has the player has to be argued here."""
    side_commands = {command for command in command_dispatch._HANDLERS
                     if command.startswith(("portrait_", "landscape_"))}
    kept = {command for command in side_commands
            if not command_dispatch._about_a_satellite(command)}

    assert kept == {
        f"{side}_{own}"
        for side in ("portrait", "landscape")
        for own in ("minimize", "speed_up", "speed_down", "speed_reset",
                    "speed_min", "speed_max")
    } & side_commands, sorted(kept)
