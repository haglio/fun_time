from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from funestra.main_hud import osr2_controls
from funestra.modes import LengthMode, MainMode, SatellitesMode

from fun_time import command_dispatch, dashboard_actions, hud_moves, windows_bridge_dispatch_loop
from fun_time.aiming import MAIN_EQUIVALENTS, resolve_active_funestra_command
from fun_time.command_reference import build_reference_sections
from fun_time.main_hud_buttons import MainFunestraFacts, main_hud_rows
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

_MAIN_HUD_POSTED = frozenset(HUD_ONLY_COMMAND_IDS) | {
    "genau_flick_seconds_down",
    "genau_flick_seconds_up",
    "main_projection_none",
    "main_funestra_length_none",
    "main_orientation_none",
    "main_funestra_record_tap",
    "projection_cycle_back",
}

_NAV_IDS = frozenset(
    f"{satellite}_nav_{direction}"
    for satellite in ("portrait", "landscape")
    for direction in ("left", "right", "up", "down")
)

_HUD_PLACE_IDS = (frozenset(hud_moves._HUD_MOVES)
                  | frozenset(hud_moves.MAIN_HUD_TURNS)
                  | frozenset(hud_moves._HUD_COLLAPSES))


def _handler_ids() -> frozenset[str]:
    return frozenset(command_dispatch._HANDLERS)


def _loop_branch_ids() -> frozenset[str]:
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
    targets: set[str] = set()
    residues: set[str] = set()
    for value in set(VOICE_COMMANDS.values()):
        for satellite in (1, 2, 3):
            for command in expand_group_command(resolve_active_funestra_command(value, satellite)):
                (residues if command.startswith("active_") else targets).add(command)
    return frozenset(targets), frozenset(residues)


def _families() -> frozenset[str]:
    return frozenset(_expected_numeric_ids()) | frozenset(_expected_filter_ids())


def _console_verbs() -> frozenset[str]:
    declared = (
        button.command
        for mode, plays_vr, plays_flat, length_mode, compilation, jump_to, has_osr2, in_vr
        in itertools.product(MainMode, (False, True), (False, True), LengthMode,
                             ("", "Volume One"), ("", "scene", "clip"), (False, True),
                             (False, True))
        for row in main_hud_rows(
            MainFunestraFacts(main_mode=mode, latest=False, plays_vr=plays_vr, plays_flat=plays_flat,
                     length_mode=length_mode, compilation=compilation, has_compilation=True,
                     jump_to=jump_to, has_osr2=has_osr2),
            in_vr=in_vr)
        for button in row
    )
    brokers = (button.command for broker in (False, True) for button in osr2_controls(broker_running=broker))
    return frozenset(command for command in (*declared, *brokers) if command)


def _satellite_verbs() -> frozenset[str]:
    return frozenset(
        button.command
        for funestra in ("portrait", "landscape")
        for latest in (None, False, True)
        for mode in (None, *SatellitesMode)
        for row in funestra_rows(funestra, locked=True, favorites_filter=True, latest=latest,
                               mode=mode)
        for button in row
    )


def test_every_verb_the_main_hud_posts_lands_on_a_handler():
    answered = (_handler_ids() | _loop_branch_ids()
                | frozenset(hud_moves._MINIMIZE_WINDOWS))
    posted = _console_verbs()
    assert posted, "no console button posts anything"
    assert answered >= posted, sorted(posted - answered)


def test_every_verb_a_satellite_hud_posts_lands_on_a_handler():
    answered = (_handler_ids() | _loop_branch_ids()
                | frozenset(hud_moves._MINIMIZE_WINDOWS))
    posted = _satellite_verbs()
    assert posted, "no satellite button posts anything"
    assert answered >= posted, sorted(posted - answered)


def test_every_spoken_phrase_lands_on_a_handler():
    targets, _ = _voice_resolutions()
    handled = (_handler_ids() | _loop_branch_ids() | _families() | _NAV_IDS
               | _HUD_PLACE_IDS)
    dead = targets - handled
    assert not dead, f"spoken commands with no handler: {sorted(dead)}"


def test_the_unresolvable_active_forms_are_exactly_the_satellite_only_actions():
    _, residues = _voice_resolutions()
    for residue in residues:
        action = residue[len("active_"):]
        assert action not in MAIN_EQUIVALENTS, residue
        assert f"portrait_{action}" in _handler_ids(), residue
        assert f"landscape_{action}" in _handler_ids(), residue


def test_every_ahk_binding_lands_on_a_handler():
    handled = _handler_ids() | _loop_branch_ids() | _NAV_IDS | _HUD_PLACE_IDS
    dead = _ahk_ids() - handled
    assert not dead, f"AHK bindings with no handler: {sorted(dead)}"


def test_every_handler_is_reachable_from_some_surface():
    targets, _ = _voice_resolutions()
    reachable = (
        targets
        | _ahk_ids()
        | frozenset(
            getattr(dashboard_actions, name)
            for name in dir(dashboard_actions)
            if isinstance(getattr(dashboard_actions, name), str) and not name.startswith("_")
        )
        | _MAIN_HUD_POSTED
        | _satellite_verbs()
        | {"portrait_lock", "landscape_lock"}
    )
    unreachable = _handler_ids() - reachable
    assert not unreachable, f"handlers nothing can reach: {sorted(unreachable)}"


def test_every_loop_branch_is_reachable_and_known():
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
    reference = _reference_ids()
    real = (
        _handler_ids()
        | _loop_branch_ids()
        | _families()
        | _NAV_IDS
        | _HUD_PLACE_IDS
        | frozenset(c for c in reference
                    if c.startswith(("active_", *windows_bridge_dispatch_loop._FUNESTRA_GROUPS)))
    )
    ghosts = reference - real
    assert not ghosts, f"reference rows naming unhandled commands: {sorted(ghosts)}"

    undocumented = _handler_ids() - reference - frozenset(HUD_ONLY_COMMAND_IDS)
    assert not undocumented, f"handled commands the reference omits: {sorted(undocumented)}"


def test_the_session_keeps_only_the_funestras_own_controls_in_origenerator_mode():
    satellite_commands = {command for command in command_dispatch._HANDLERS
                     if command.startswith(("portrait_", "landscape_"))}
    kept = {command for command in satellite_commands
            if not command_dispatch._about_a_satellite(command)}

    assert kept == {
        f"{satellite}_{own}"
        for satellite in ("portrait", "landscape")
        for own in ("minimize", "crown", "speed_up", "speed_down", "speed_reset",
                    "speed_min", "speed_max", "take_osr2", "kino_activate",
                    "origenerator_activate", "lock", "lock_on", "lock_off")
    } & satellite_commands, sorted(kept)


def test_a_command_bound_in_two_domains_is_refused_at_import():
    def first(state, _config, _target_path):
        return state, []

    with pytest.raises(ValueError, match=r"bound twice.*portrait_next"):
        command_dispatch._bound_once([{"portrait_next": first}, {"portrait_next": first}])

