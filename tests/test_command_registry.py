from __future__ import annotations

import ast
import inspect
import itertools
import textwrap
from functools import partial
from types import SimpleNamespace

import pytest
from funestra.drive_layout import MAX_INTENSITY
from funestra.main_hud import osr2_controls
from funestra.modes import LengthMode, MainMode

from fun_time import (
    command_dispatch,
    dashboard_actions,
    satellite_commands,
    windows_bridge_dispatch_loop,
)
from fun_time.command_dispatch import ROUTES, handles
from fun_time.command_reference import build_reference_sections
from fun_time.command_value import MAIN_EQUIVALENTS, Scope, parse_command
from fun_time.filter_vocab import filter_voice_commands
from fun_time.inclusion_pairs import LENGTH, ORIENTATION, PROJECTION
from fun_time.main_hud_buttons import MainFunestraFacts, main_hud_rows
from fun_time.satellite_buttons import HudState, funestra_rows
from fun_time.voice_commands import build_voice_commands
from fun_time.windows_bridge_dispatch_loop import REQUEST_NEWS
from fun_time_vr.thumbs import PROJECTION_BACK
from tests.test_command_id_snapshot import (
    IDS_POSTED_OFF_ONE_SURFACE_ALONE,
    PREFIXED_COMMAND_FORMS,
    _ahk_ids,
    _expected_numeric_ids,
)

_A_VR_STICK_PUSHED_LEFT_POSTS = frozenset({PROJECTION_BACK})

_HUD_POSTED_WITH_A_VALUE = frozenset(form.rstrip("|") for form in PREFIXED_COMMAND_FORMS) | {
    MAX_INTENSITY,
    "portrait_hud_restore_at", "landscape_hud_restore_at", "main_hud_restore_at",
}

_TOLD_BY_THE_HOSTED_APP = frozenset(
    f"{funestra}_{satellite_commands.LOCK_HELD}" for funestra in ("portrait", "landscape"))


def _loop_branch_ids() -> frozenset[str]:
    return (frozenset(windows_bridge_dispatch_loop._LOOP_COMMANDS)
            | dashboard_actions.HEADSET_SIGNALS)


def _answered(text: str) -> bool:
    return handles(text) or text in _loop_branch_ids()


def _keys(texts) -> frozenset[tuple[Scope, str]]:
    return frozenset(parse_command(text).key for text in texts)


_TOLD_ACTIONS = {"alpha": ("alpha",)}


def _reference_ids() -> frozenset[str]:
    return frozenset(
        command
        for section in build_reference_sections(_TOLD_ACTIONS)
        for row in section.rows
        for command in row.commands
    )


def _where_a_session_sends_every_phrase() -> tuple[frozenset[str], frozenset[str]]:
    targets: set[str] = set()
    residues: set[str] = set()
    told = build_voice_commands(filter_commands=filter_voice_commands(_TOLD_ACTIONS))
    for value in set(told.values()):
        for funestra in (1, 2, 3):
            for command in parse_command(value).aimed_at_the_active(funestra).each():
                (residues if command.said_to is Scope.ACTIVE else targets).add(command.text)
    return frozenset(targets), frozenset(residues)


def _dashboard_ids() -> frozenset[str]:
    return frozenset(
        getattr(dashboard_actions, name)
        for name in dir(dashboard_actions)
        if isinstance(getattr(dashboard_actions, name), str) and not name.startswith("_")
    )


def _console_verbs_in_every_state() -> frozenset[str]:
    declared = (
        button.command
        for (mode, plays_vr, plays_flat, plays_portrait, plays_landscape, length_mode, compilation,
             jump_to, has_osr2, in_vr)
        in itertools.product(MainMode, (False, True), (False, True), (False, True), (False, True),
                             LengthMode, ("", "Volume One"), ("", "scene", "clip"), (False, True),
                             (False, True))
        for row in main_hud_rows(
            MainFunestraFacts(main_mode=mode, latest=False, plays_vr=plays_vr, plays_flat=plays_flat,
                     plays_portrait=plays_portrait, plays_landscape=plays_landscape,
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
        for row in funestra_rows(funestra, HudState(locked=True, favorites_filter=True, latest=latest))
        for button in row
    )


def test_every_verb_the_main_hud_posts_lands_on_a_handler():
    posted = _console_verbs_in_every_state()
    assert posted, "no console button posts anything"
    unanswered = {command for command in posted if not _answered(command)}
    assert not unanswered, sorted(unanswered)


def test_every_verb_a_satellite_hud_posts_lands_on_a_handler():
    posted = _satellite_verbs()
    assert posted, "no satellite button posts anything"
    unanswered = {command for command in posted if not _answered(command)}
    assert not unanswered, sorted(unanswered)


def test_every_spoken_phrase_lands_on_a_handler():
    targets, _ = _where_a_session_sends_every_phrase()
    dead = {command for command in targets if not _answered(command)}
    assert not dead, f"spoken commands with no handler: {sorted(dead)}"


def test_the_unresolvable_active_forms_are_exactly_the_satellite_only_actions():
    _, residues = _where_a_session_sends_every_phrase()
    assert residues
    for residue in residues:
        action = parse_command(residue).action
        assert action not in MAIN_EQUIVALENTS, residue
        assert handles(f"portrait_{action}"), residue
        assert handles(f"landscape_{action}"), residue


def test_every_ahk_binding_lands_on_a_handler():
    dead = {command for command in _ahk_ids() if not _answered(command)}
    assert not dead, f"AHK bindings with no handler: {sorted(dead)}"


def test_every_route_is_reachable_from_some_surface():
    targets, _ = _where_a_session_sends_every_phrase()
    reachable = _keys(
        targets
        | _ahk_ids()
        | _dashboard_ids()
        | frozenset(IDS_POSTED_OFF_ONE_SURFACE_ALONE)
        | _console_verbs_in_every_state()
        | _A_VR_STICK_PUSHED_LEFT_POSTS
        | _HUD_POSTED_WITH_A_VALUE
        | _satellite_verbs()
        | {"portrait_lock", "landscape_lock"}
        | _TOLD_BY_THE_HOSTED_APP
    )
    unreachable = set(ROUTES) - reachable
    assert not unreachable, f"routes nothing can reach: {sorted(unreachable)}"


def test_including_neither_takes_a_deliberate_press_and_no_phrase_or_key():
    neither = {pair.commands[(False, False)] for pair in (PROJECTION, LENGTH, ORIENTATION)}
    spoken, _ = _where_a_session_sends_every_phrase()

    assert neither <= _console_verbs_in_every_state()
    assert not neither & (spoken | _ahk_ids())


def test_every_loop_branch_is_reachable_and_known():
    targets, _ = _where_a_session_sends_every_phrase()
    told_by_the_hosted_app = frozenset(REQUEST_NEWS)
    for branch in _loop_branch_ids():
        assert branch in targets | _ahk_ids() | _dashboard_ids() | told_by_the_hosted_app, (
            f"loop branch {branch!r} is reachable from no surface"
        )


_THE_LOOPS_LIVE_OBJECTS = frozenset({
    "windows", "voice", "ahk_cmd_file", "broker",
    "_flag_the_panel_for_the_headset", "_handle_browse_library", "_log_topmost_state",
    "_log_any_window_still_on_top_under_the_pause",
})


def _what_a_loop_command_reaches(handler, followed: frozenset[str] = frozenset()) -> set[str]:
    function = handler.func if isinstance(handler, partial) else handler
    reached: set[str] = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(function)))):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in ("runner", "broker"):
                reached.add(node.attr if node.value.id == "runner" else "broker")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id.startswith("_") and node.func.id not in followed):
            helper = getattr(windows_bridge_dispatch_loop, node.func.id, None)
            if callable(helper):
                reached |= _what_a_loop_command_reaches(helper, followed | {node.func.id})
    return reached


def test_each_of_the_loops_own_commands_needs_one_of_its_live_objects():
    stateless = sorted(
        command for command, handler in windows_bridge_dispatch_loop._LOOP_COMMANDS.items()
        if not _what_a_loop_command_reaches(handler) & _THE_LOOPS_LIVE_OBJECTS)

    assert stateless == [], (
        "these loop commands touch none of the loop's live objects, so they are"
        f" command_dispatch's, a state in and window ops out: {stateless}")


def test_the_reference_and_the_routes_agree():
    reference = _reference_ids()
    ghosts = {command for command in reference
              if not _answered(command) and parse_command(command).said_to is not Scope.ACTIVE}
    assert not ghosts, f"reference rows naming unhandled commands: {sorted(ghosts)}"

    documented = _keys(reference | frozenset(IDS_POSTED_OFF_ONE_SURFACE_ALONE) | _HUD_POSTED_WITH_A_VALUE
                       | _expected_numeric_ids() | _TOLD_BY_THE_HOSTED_APP)
    undocumented = set(ROUTES) - documented
    assert not undocumented, f"routed commands the reference omits: {sorted(undocumented)}"


def test_the_session_keeps_only_the_funestras_own_controls_on_a_satellite_the_app_holds():
    own = {
        "minimize", "crown", "take_osr2",
        "lock", "lock_on", "lock_off", "lock_held",
        "speed", "speed_up", "speed_down", "speed_min", "speed_max",
        "hud_minimize", "hud_restore", "hud_restore_at", "hud_left", "hud_right", "hud_up", "hud_down",
        "hud_upper_left", "hud_upper_right", "hud_lower_left", "hud_lower_right",
        "mute", "unmute", "volume_down", "volume_up",
        "enhancement_enhanced", "enhancement_original", "enhancement_both",
    }

    assert own == satellite_commands.THE_FUNESTRAS_OWN


def test_a_command_bound_in_two_domains_is_refused_at_import():
    def first(state, _config, _target_path):
        return state, []

    with pytest.raises(ValueError, match=r"bound twice.*portrait_next"):
        command_dispatch._bound_once([SimpleNamespace(HANDLERS={"portrait_next": first}),
                                      SimpleNamespace(SATELLITE_ACTIONS={"next": first})])

