"""Ensure no dead code accumulates in the production packages."""

import ast
import re
import subprocess
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


def _vulture(whitelist: Path):
    return subprocess.run(
        [
            sys.executable, "-m", "vulture",
            str(ROOT / "fun_time"),
            # The satellite player ships from this repo too, so it is held to the
            # same bar. It went unscanned while it lived in genau, which is how
            # two unreachable SatelliteSession methods survived the move here.
            str(ROOT / "satellite"),
            str(ROOT / "fun_time_vr"),
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
    result = _vulture(WHITELIST)
    if result.returncode != 0:
        raise AssertionError(
            f"vulture found dead code:\n{result.stdout}{result.stderr}"
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
    reported = set(_REPORTED_NAME.findall(_vulture(nothing_whitelisted).stdout))

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
