from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_check

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "fun_time", ROOT / "main_funestra", ROOT / "satellite", ROOT / "fun_time_vr")


def _package_sources():
    return sorted(p for pkg in PACKAGES for p in pkg.rglob("*.py"))


_REACHES_AROUND_THE_GUARDED_WIN32_LAYER = {
    "main_funestra/window.py": 3,
    # Deliberate: the stand-in raises Win32Unavailable (a RuntimeError) where
    # these want the AttributeError ctypes.windll gives off Windows, and every
    # caller catches it to fall back rather than to fail.  monitors.py is the
    # module the rest of the family asks for a monitor rather than measuring
    # one, which is why its count is the only one that ever goes up.
    "fun_time/monitors.py": 7,
}

_WIN32_BINDING_NAMES = frozenset(
    {"windll", "oledll", "WinDLL", "OleDLL", "CDLL", "PyDLL", "WINFUNCTYPE"})


def _win32_reaches(tree) -> int:
    ctypes_names = {"ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ctypes" and alias.asname:
                    ctypes_names.add(alias.asname)
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                and node.value.id in ctypes_names):
            ctypes_names.update(
                t.id for t in node.targets if isinstance(t, ast.Name))

    def names_the_surface(node) -> bool:
        if isinstance(node, ast.Attribute):
            if node.attr in _WIN32_BINDING_NAMES and isinstance(node.value, ast.Name):
                return node.value.id in ctypes_names
            return names_the_surface(node.value)      # ctypes.windll.user32
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in ctypes_names
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in _WIN32_BINDING_NAMES):
                return True
            return names_the_surface(node.func)       # ctypes.WinDLL("user32")
        if isinstance(node, ast.Name):
            return node.id in bound_to_surface
        return False

    bound_to_surface: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and names_the_surface(node.value):
            bound_to_surface.update(
                t.id for t in node.targets if isinstance(t, ast.Name))

    reaches = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ctypes":
            reaches += sum(1 for a in node.names if a.name in _WIN32_BINDING_NAMES)
        elif (isinstance(node, ast.Call) and names_the_surface(node.func)) or (isinstance(node, ast.Attribute) and node.attr in _WIN32_BINDING_NAMES
              and isinstance(node.value, ast.Name) and node.value.id in ctypes_names):
            reaches += 1
    return reaches


def test_every_call_through_one_hoisted_binding_is_a_reach_of_its_own():
    hoisted_once = ast.parse(
        "import ctypes\n"
        "u = ctypes.windll.user32\n"
        "u.SetWindowPos()\n"
        "u.ShowWindow()\n")

    assert _win32_reaches(hoisted_once) == 3


def test_nothing_new_binds_win32_under_the_layer_that_exists_to():
    counted = {}
    for path in _package_sources():
        reaches = _win32_reaches(ast.parse(path.read_text(encoding="utf-8")))
        if reaches:
            counted[path.relative_to(ROOT).as_posix()] = reaches

    assert counted == _REACHES_AROUND_THE_GUARDED_WIN32_LAYER, (
        "the direct-binding count moved.  A file that is new here reaches "
        "ctypes.windll instead of asking fun_time.win32 or fun_time.monitors; "
        "a count that came down is one to lower here in the same commit."
    )


MAX_DISPATCHER_COMPLEXITY = 57
MAX_DISPATCH_COMMAND_COMPLEXITY = 9


def _decision_points_radon_counts(function: ast.AST) -> int:
    count = 0
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, (ast.If, ast.IfExp, ast.Assert)):
            count += 1
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            count += 1 + bool(node.orelse)
        elif isinstance(node, ast.Try):
            count += len(node.handlers) + bool(node.orelse)
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            count += 1 + len(node.ifs)
        pending.extend(ast.iter_child_nodes(node))
    return count


def _cyclomatic_complexity_as_radon_counts_it(tree: ast.AST) -> dict[str, int]:
    return {node.name: 1 + _decision_points_radon_counts(node) for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_command_registry_holds_the_registry_and_the_guards():
    complexity = _cyclomatic_complexity_as_radon_counts_it(ast.parse(
        (ROOT / "fun_time" / "command_dispatch.py").read_text(encoding="utf-8")))
    total = sum(complexity.values())
    assert total <= MAX_DISPATCHER_COMPLEXITY, (
        f"command_dispatch.py's functions sum to a complexity of {total} (ceiling "
        f"{MAX_DISPATCHER_COMPLEXITY}).  A command's handler belongs in its domain's "
        "module, registered from there; lower the ceiling when it comes down."
    )
    assert complexity["dispatch_command"] <= MAX_DISPATCH_COMMAND_COMPLEXITY

