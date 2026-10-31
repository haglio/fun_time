"""The three gates that are this repo's own: no module reaches into another's
privates, prose does not outgrow the code it explains, and nothing new binds
Win32 under the layer that exists to. The dead-code checks that sat beside them
are app_support's gates, which every repo names once in its pytest options.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_check

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "fun_time", ROOT / "main_funestra", ROOT / "satellite", ROOT / "fun_time_vr")


def _package_sources():
    return sorted(p for pkg in PACKAGES for p in pkg.rglob("*.py"))


def test_no_module_reaches_into_another_ones_privates():
    """A leading underscore is the only boundary marker these packages have.

    Six imports crossed it — two banding helpers, the event log, the HUD
    priming, and a voice-control error string read from two other modules —
    which means the name said "mine" while five call sites in three packages
    depended on it.  Either a name is part of a module's surface, in which case
    it says so and carries a docstring, or it is not and nobody outside reads
    it; this is what makes that a rule rather than an intention.
    """
    reaches = []
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    module = node.module or "." * node.level
                    reaches.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        f"from {module} import {alias.name}")

    assert reaches == [], (
        "a private name is imported across a module boundary:\n" + "\n".join(reaches)
        + "\nEither make it public with a docstring saying what it promises, "
          "or stop reading it from outside."
    )


MAX_PROSE_LINES = 5593


def _prose_and_code(path: Path) -> tuple[int, int]:
    """(prose lines, code lines) in one module.

    A line is prose when its only content is a comment or a docstring, so a
    trailing `# why` on a real statement costs nothing -- the ratio is about
    paragraphs, not annotations.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    kind = ["blank" if not line.strip() else "code" for line in lines]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - syntax is CI's job
        return 0, 0

    def mark(token, *, only_if_alone: bool):
        for n in range(token.start[0] - 1, token.end[0]):
            if kind[n] != "code":
                continue
            if only_if_alone and lines[n].split("#")[0].strip():
                continue  # a trailing `# why` on a statement is not prose
            kind[n] = "prose"

    opens_a_statement = tokenize.INDENT
    for token in tokens:
        if token.type == tokenize.COMMENT:
            mark(token, only_if_alone=True)
        elif token.type == tokenize.STRING and opens_a_statement in (
            tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL,
        ):
            mark(token, only_if_alone=False)
        if token.type not in (tokenize.COMMENT, tokenize.NL):
            opens_a_statement = token.type
    return kind.count("prose"), kind.count("code")


def test_prose_does_not_outgrow_the_code_it_explains():
    """A ceiling that can only come down.

    Design that lives in prose has to be kept in step by hand, and is not kept
    in step by hand. Where a paragraph states a rule, the way to spend it is a
    name or a test, not another paragraph -- and this fails until one of those
    is what carries it.
    """
    prose = code = 0
    for path in _package_sources() + sorted((ROOT / "tools").rglob("*.py")):
        module_prose, module_code = _prose_and_code(path)
        prose += module_prose
        code += module_code

    assert prose <= MAX_PROSE_LINES, (
        f"{prose} prose lines (ceiling {MAX_PROSE_LINES}), against {code} of code "
        f"-- {prose / code:.4f} per line. Delete a stale block, or move what it "
        "says into a name or a test; lower MAX_PROSE_LINES when you do."
    )


# Every module that reaches ``ctypes``' Windows binding surface directly rather
# than through ``fun_time.win32_loader``, and how many times.  Held as an
# EQUALITY per file: a count that has come down means a reach was consolidated
# and this table was not lowered with it, and a file that is absent must have
# none at all.  This is the number no length or coverage gate can see — the
# dashboard carried eleven of these inside a QMainWindow constructor, with
# GWL_STYLE written as a bare -16 and the style bits as inline hex, while
# fun_time/win32.py existed to be the place that names them.
_WIN32_REACHES = {
    # The loader itself: this IS the binding, and the thing it exists to be
    # asked instead.
    "fun_time/win32_loader.py": 5,
    # Deliberate: the stand-in raises Win32Unavailable (a RuntimeError) where
    # these want the AttributeError ctypes.windll gives off Windows, and every
    # caller catches it to fall back rather than to fail.  monitors.py is the
    # module the rest of the family asks for a monitor rather than measuring
    # one, which is why its count is the only one that ever goes up.
    "fun_time/monitors.py": 7,
}

# What counts as reaching it.
_WIN32_BINDING_NAMES = frozenset(
    {"windll", "oledll", "WinDLL", "OleDLL", "CDLL", "PyDLL", "WINFUNCTYPE"})


def _win32_reaches(tree) -> int:
    """How many CALLS this module makes past the layer, not how often it names it.

    A count of names is walked around by one hoist: ``u = ctypes.windll.user32``
    and then eleven ``u.X()`` scores 1, with none of the guard, none of the named
    constants and no trip through fun_time.win32 -- which is exactly the shape
    this exists to catch. So a local bound to the binding surface is followed,
    and every call THROUGH it counts.

    The spellings that mean the same thing all count too: ``ctypes.windll``,
    ``import ctypes as c`` then ``c.windll``, ``c = ctypes`` then ``c.windll``,
    ``from ctypes import windll``, and ``getattr(ctypes, "windll")``.
    """
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
        """Whether this expression IS ctypes' binding surface, however spelled."""
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

    # Locals holding a DLL taken off that surface: the hoist.
    bound_to_surface: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and names_the_surface(node.value):
            bound_to_surface.update(
                t.id for t in node.targets if isinstance(t, ast.Name))

    reaches = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ctypes":
            reaches += sum(1 for a in node.names if a.name in _WIN32_BINDING_NAMES)
        elif isinstance(node, ast.Call) and names_the_surface(node.func):
            reaches += 1                               # a call through it
        elif isinstance(node, ast.Attribute) and node.attr in _WIN32_BINDING_NAMES:
            if isinstance(node.value, ast.Name) and node.value.id in ctypes_names:
                reaches += 1                           # named, not yet called
    return reaches


def test_nothing_new_binds_win32_under_the_layer_that_exists_to():
    """A ceiling on coupling, not on size, held per file and as an equality.

    ``fun_time/win32.py`` wraps every cross-process window call in a guard
    after a stalled Funestra once wedged a whole session, and names the constants
    the family reads to learn its Win32 conventions.  A module that goes to
    ``ctypes.windll`` instead gets neither, and nothing said so: the dashboard
    made eleven such calls, five of the thirteen constants it restated inline
    were already exported next door, and every length and coverage gate in this
    repo passed the whole time.
    """
    counted = {}
    for path in _package_sources():
        reaches = _win32_reaches(ast.parse(path.read_text(encoding="utf-8")))
        if reaches:
            counted[path.relative_to(ROOT).as_posix()] = reaches

    assert counted == _WIN32_REACHES, (
        "the direct-binding count moved.  A file that is new here reaches "
        "ctypes.windll instead of asking fun_time.win32 or fun_time.monitors; "
        "a count that came down is one to lower here in the same commit."
    )


# The dispatch loop's own methods, held as an equality: every feature since the
# first pass cut it to 26 landed as glue on the loop, each with a module of its
# own standing beside it.  A count that came down is one to lower here in the
# same commit; one that went up is glue to move onto its collaborator.
DISPATCH_LOOP_METHODS = 22


def test_the_dispatch_loops_glue_stays_on_its_collaborators():
    tree = ast.parse(
        (ROOT / "fun_time" / "windows_bridge_dispatch_loop.py").read_text(encoding="utf-8"))
    runner = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "DispatchLoopRunner")
    methods = [node.name for node in runner.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert len(methods) == DISPATCH_LOOP_METHODS, (
        f"DispatchLoopRunner has {len(methods)} methods: {methods}.  A feature's "
        "glue goes on the collaborator that holds it; a count that came down is "
        "one to lower here in the same commit."
    )


# The command registry's complexity, a ceiling that only comes down: every handler
# written into it since the first pass had a domain module of its own to go in.
MAX_DISPATCHER_COMPLEXITY = 59
MAX_DISPATCH_COMMAND_COMPLEXITY = 9


def _decisions(function: ast.AST) -> int:
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


def _complexity(tree: ast.AST) -> dict[str, int]:
    """Each function's cyclomatic complexity, as radon counts it: one, plus a
    point per branch, loop, handler, boolean operand and comprehension."""
    return {node.name: 1 + _decisions(node) for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_command_registry_holds_the_registry_and_the_guards():
    complexity = _complexity(ast.parse(
        (ROOT / "fun_time" / "command_dispatch.py").read_text(encoding="utf-8")))
    total = sum(complexity.values())
    assert total <= MAX_DISPATCHER_COMPLEXITY, (
        f"command_dispatch.py's functions sum to a complexity of {total} (ceiling "
        f"{MAX_DISPATCHER_COMPLEXITY}).  A command's handler belongs in its domain's "
        "module, registered from there; lower the ceiling when it comes down."
    )
    assert complexity["dispatch_command"] <= MAX_DISPATCH_COMMAND_COMPLEXITY

