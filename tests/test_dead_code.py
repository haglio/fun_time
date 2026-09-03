"""Ensure no dead code accumulates in the production packages."""

import ast
import io
import re
import subprocess
import tokenize
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WHITELIST = ROOT / "vulture_whitelist.py"
PACKAGES = ("fun_time", "satellite", "fun_time_vr")

_REPORTED_NAME = re.compile(r"unused [a-z ]+ '([^']+)'")


def _package_sources():
    return sorted(p for pkg in PACKAGES for p in (ROOT / pkg).rglob("*.py"))


def _argparse_dests(tree):
    """Every option an ``add_argument`` call in this tree declares."""
    for node in ast.walk(tree):
        call = node
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "add_argument"):
            continue
        explicit = [
            kw.value.value for kw in call.keywords
            if kw.arg == "dest" and isinstance(kw.value, ast.Constant)
        ]
        if explicit:
            yield explicit[0], call.lineno
            continue
        flags = [a.value for a in call.args if isinstance(a, ast.Constant)]
        long_flags = [f for f in flags if f.startswith("--")]
        spelling = next(iter(long_flags or flags), None)
        if spelling:
            yield spelling.lstrip("-").replace("-", "_"), call.lineno


def _vulture(package: str, whitelist: Path):
    """Vulture over ONE package.

    Scanned together, the three packages hide each other's corpses: vulture
    matches by bare name, so a dead member is invisible whenever any of the
    three has a live name like it. One package at a time is narrower -- what
    it costs is the cross-package readers, which the whitelist names.

    The satellite player ships from this repo too, so it is held to the same
    bar. It went unscanned while it lived in genau, which is how two
    unreachable SatelliteSession methods survived the move here.
    """
    return subprocess.run(
        [
            sys.executable, "-m", "vulture",
            str(ROOT / package),
            str(whitelist),
            "--min-confidence", "60",
        ],
        capture_output=True,
        text=True,
    )


def _whitelisted_names():
    """The bare name each whitelist entry suppresses, in file order."""
    names = []
    for node in ast.parse(WHITELIST.read_text()).body:
        if not isinstance(node, ast.Expr):
            continue
        if isinstance(node.value, ast.Attribute):  # the `_.attr` spelling
            names.append(node.value.attr)
        elif isinstance(node.value, ast.Name):
            names.append(node.value.id)
    return names


def test_no_dead_code():
    for package in PACKAGES:
        result = _vulture(package, WHITELIST)
        if result.returncode != 0:
            raise AssertionError(
                f"vulture found dead code in {package}:\n{result.stdout}{result.stderr}"
            )


def test_every_whitelist_entry_suppresses_a_report(tmp_path):
    """An entry that suppresses nothing is a standing blind spot.

    Vulture matches by bare name, so an entry keeps covering whatever is next
    given that name -- long after the symbol it was written for is gone. Ask
    the whole file the question the ablation asks one line at a time: with
    nothing whitelisted, every entry's name must be among the reports, or that
    entry is covering a name the packages no longer report as dead.
    """
    nothing_whitelisted = tmp_path / "empty_whitelist.py"
    nothing_whitelisted.write_text("")
    reported = {
        name
        for package in PACKAGES
        for name in _REPORTED_NAME.findall(_vulture(package, nothing_whitelisted).stdout)
    }

    unnecessary = [name for name in _whitelisted_names() if name not in reported]

    assert not unnecessary, (
        "vulture_whitelist.py entries that suppress nothing: "
        + ", ".join(unnecessary)
    )


def test_every_declared_command_line_option_is_read():
    """A flag nobody reads is a launcher surface that does nothing.

    The parser is what a reader — and the next launcher change — takes for the
    list of what a `python -m` entry point accepts, so an option declared and
    never read promises behaviour the app does not have. Reads are matched by
    attribute name across the three packages, because a parser and the code
    that consumes its namespace need not live in the same module (satellite's
    do not). That makes this a floor like vulture's: it cannot see an option
    whose name collides with a live attribute elsewhere.
    """
    declared: dict[str, str] = {}
    read: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for dest, lineno in _argparse_dests(tree):
            declared.setdefault(dest, f"{path.relative_to(ROOT)}:{lineno}")
        read.update(
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )

    unread = {dest: where for dest, where in declared.items() if dest not in read}

    assert not unread, "command-line options nothing reads: " + ", ".join(
        f"{dest} ({where})" for dest, where in sorted(unread.items())
    )


def test_no_dataclass_field_goes_unread():
    """A field written on every build and read by nobody is state that lies.

    It reads as part of the object's contract, so the next reader preserves
    whatever computes it -- which is how a manifest key gets parsed, converted
    and threaded through three call layers for nothing. Reads are matched by
    attribute name across the three packages, plus the literal name of any
    `getattr(x, "name", ...)`, so this shares vulture's blindness to a name
    that collides with a live attribute elsewhere.
    """
    declared: dict[str, str] = {}
    read: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                read.add(node.attr)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                read.add(node.args[1].value)
            elif isinstance(node, ast.ClassDef) and any(
                "dataclass" in ast.unparse(d) for d in node.decorator_list
            ):
                for statement in node.body:
                    if isinstance(statement, ast.AnnAssign) and isinstance(
                        statement.target, ast.Name
                    ):
                        declared.setdefault(
                            f"{node.name}.{statement.target.id}",
                            f"{path.relative_to(ROOT)}:{statement.lineno}",
                        )

    unread = {
        field: where
        for field, where in declared.items()
        if field.split(".")[1] not in read
    }

    assert not unread, "dataclass fields nothing reads: " + ", ".join(
        f"{field} ({where})" for field, where in sorted(unread.items())
    )


def _ruff(*rules: str):
    return subprocess.run(
        [
            sys.executable, "-m", "ruff", "check",
            "--output-format", "concise",
            "--select", ",".join(rules),
            *(str(ROOT / pkg) for pkg in PACKAGES),
            str(ROOT / "tools"),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_nothing_is_imported_or_assigned_and_left_unread():
    """The hole vulture cannot see: deadness that is local to one module.

    Vulture resolves names across everything it scans, so an import unused
    HERE but live in a sibling module is invisible to it -- which is how two
    imports sat in the busiest dispatch file reading as though it built window
    ops. ruff answers per file.
    """
    result = _ruff("F401", "F811", "F841")

    assert result.returncode == 0, result.stdout + result.stderr


def test_no_plain_function_declares_an_argument_it_never_reads():
    """A parameter nothing reads is a lie in the signature.

    Someone adding a media-root feature reasonably assumes start_core_session
    already has what it needs. Where a value really is required and unused --
    a library's callback signature -- the name says so with a leading
    underscore. ARG002 is deliberately not enforced: a framework override
    (Qt's paintEvent, the satellite player's pump) is handed arguments it is
    free to ignore, and renaming those would cost more than it says.
    """
    result = _ruff("ARG001")

    assert result.returncode == 0, result.stdout + result.stderr


# How many lines of the packages are prose rather than code, as measured by
# _prose_and_code below. A RATCHET: lower it when prose goes, never raise it.
# The audit that set it measured 0.46 prose per line of code against a ~0.25
# norm, with the reasoning for the design living in comments rather than in
# names and tests -- which is how a docstring came to cite a module that had
# been deleted and a comment came to promise a seventh child that already
# existed.
#
# A COUNT, not that ratio, since 2026-08-31. Held as a ratio it fired on any
# small edit in EITHER direction once the number sat near its cap: deleting a
# line of dead code -- the thing every other gate in this file exists to force
# -- raised the ratio and failed the build, pointing the author at prose they
# had never touched. What the gate is for is prose that outgrows what it
# explains, and that is what this counts.
# 6925 since 2026-09-03: the commits main took between the last ratchet and the
# audit stack's landing (the hosted app in the suite, the sibling pin, the VR
# icon) brought their prose with them, through the ratio gate that held main
# then; the count ratchets down from the merged tree, not from either side.
MAX_PROSE_LINES = 6925

# What the count was against at the last ratchet, so the norm the audit
# measured stays readable. Reported on failure; not asserted.
_PROSE_RATIO_AT_RATCHET = 0.4553


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
        f"-- {prose / code:.4f} per line, from {_PROSE_RATIO_AT_RATCHET} at the last "
        "ratchet. Delete a stale block, or move what it says into a name or a "
        "test; lower MAX_PROSE_LINES when you do."
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
    "fun_time/monitors.py": 13,
    # A vtable call built per invocation, inside a function body, so it never
    # runs at import -- win32_loader's prototype cannot express one.
    "fun_time/win32_taskbar.py": 3,
    # A fourth enumeration walk with its own hoisted handle and its own
    # prototype; folding it into win32._first_window is its own change.
    "fun_time/windows_bridge_sequencer.py": 9,
    # An error popup, and an icon handed to a window: both belong behind a
    # named call the way the dashboard's chrome now is.
    "fun_time_vr/player.py": 2,
    "fun_time_vr/vr_session.py": 4,
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


def test_nothing_new_binds_win32_behind_the_layer_that_exists_to():
    """A ceiling on coupling, not on size, held per file and as an equality.

    ``fun_time/win32.py`` wraps every cross-process window call in a guard
    after a stalled player once wedged a whole session, and names the constants
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
