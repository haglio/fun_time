"""The names and the calls in the code no suite runs.

These modules need a headset, a graphics context or a real window, so nothing
exercises the lines that matter and a sibling that renames a property or grows a
signature leaves a broken call with both suites green -- the first anyone sees
of it is a room missing a player. Two of those on 2026-09-28:
`MpvRenderPlayer.has_new_frame` became `has_picture_to_draw` while a branch
waited, and `Painter.paint` grew the window height. Read off the source, there
being nothing here to run.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

from player_core.render_player import MpvRenderPlayer

from tests.test_coverage import NOT_UNIT_TESTED

ROOT = Path(__file__).resolve().parent.parent

HELD_BY_THE_SHELLS: dict[str, dict[str, type]] = {
    "fun_time_vr/video_thread.py": {"player": MpvRenderPlayer},
    "fun_time_vr/player.py": {"player": MpvRenderPlayer},
}

CALLS_NOTHING_RUNS = ("main_player/app.py", "satellite/app.py",
                      "fun_time_vr/player.py", "fun_time_vr/video_thread.py")


def _called_on(module: Path, field: str) -> set[str]:
    """Every name the module reads off ``self.<field>``."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == field
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"}


def test_every_name_a_shell_calls_is_one_the_thing_it_holds_answers_to():
    gone = sorted(
        f"self.{field}.{name} in {module}"
        for module, held in HELD_BY_THE_SHELLS.items()
        for field, kind in held.items()
        for name in _called_on(ROOT / module, field)
        if not hasattr(kind, name)
    )
    assert not gone, (
        "these no longer exist on what the shell holds, and no suite runs the "
        f"module that calls them: {gone}")


def _classes_by_local_name(body: list[ast.stmt], namespace) -> dict[str, type]:
    """``name -> class`` for each ``name = Class(...)`` this scope makes."""
    built = {}
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            continue
        made = getattr(namespace, node.value.func.id, None)
        if inspect.isclass(made):
            built[node.targets[0].id] = made
    return built


def _unbindable_calls(source: Path, namespace) -> list[str]:
    """Calls on a locally built object that its class could not accept."""
    wrong = []
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        built = _classes_by_local_name(scope.body, namespace)
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in built):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(
                    k.arg is None for k in node.keywords):
                continue
            method = getattr(built[node.func.value.id], node.func.attr, None)
            if not callable(method):
                wrong.append(f"{source.name}:{node.lineno} "
                             f"{node.func.value.id}.{node.func.attr} is not there")
                continue
            try:
                inspect.signature(method).bind(
                    object(), *[object()] * len(node.args),
                    **{k.arg: object() for k in node.keywords})
            except TypeError as exc:
                wrong.append(f"{source.name}:{node.lineno} "
                             f"{node.func.value.id}.{node.func.attr}: {exc}")
    return wrong


def test_every_call_in_the_unrun_code_is_one_its_class_accepts():
    wrong: list[str] = []
    for module in CALLS_NOTHING_RUNS:
        namespace = importlib.import_module(module[:-3].replace("/", "."))
        wrong += _unbindable_calls(ROOT / module, namespace)
    assert not wrong, (
        "no suite reaches these calls, and the classes they are made on would "
        f"refuse them: {sorted(wrong)}")


def test_the_shells_it_reads_are_ones_no_suite_runs():
    """The lists above are only worth keeping for code nothing exercises: a
    module a suite does run is held to its calls by that suite."""
    unrun = {entry.path for entry in NOT_UNIT_TESTED}
    assert set(HELD_BY_THE_SHELLS) <= unrun
