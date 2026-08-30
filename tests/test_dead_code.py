"""Ensure no dead code accumulates in the production packages."""

import ast
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WHITELIST = ROOT / "vulture_whitelist.py"

_REPORTED_NAME = re.compile(r"unused [a-z ]+ '([^']+)'")


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
