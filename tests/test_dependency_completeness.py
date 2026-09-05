"""Every third-party import in the packages is a dependency pyproject declares,
and the Python floor pyproject declares is the one the gate runs."""
from __future__ import annotations

import re
from pathlib import Path

from app_support.dependencies import assert_every_import_is_declared

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Every top-level package ships from this repo, so all must be covered: the
# satellite player pulls in numpy and Pillow that the orchestrator alone would
# not have justified, and fun_time_vr brings the OpenXR/GL stack.
PACKAGE_DIRS = (
    PROJECT_ROOT / "fun_time",
    PROJECT_ROOT / "satellite",
    PROJECT_ROOT / "fun_time_vr",
)
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def test_all_third_party_imports_declared_in_pyproject():
    # The gate is the family's; what is this repo's is which packages are its
    # own and the two import names its pyproject spells differently.
    assert_every_import_is_declared(
        PROJECT_ROOT, PACKAGE_DIRS, PYPROJECT,
        local=("fun_time", "satellite", "fun_time_vr"),
        import_names={"glfw": "glfw", "PyQt6": "PyQt6"})


def test_the_declared_python_floor_is_the_one_ci_actually_runs():
    """``requires-python`` said >=3.10 while the tree needed 3.12 and CI ran it.

    ``branch_session.py:592`` is an f-string carrying a `"` and a backslash
    inside its replacement field — both PEP 701, both a SyntaxError before 3.12
    — so a 3.10 or 3.11 install advertised as supported cannot even import this
    package.  The floor and the version CI proves are the only two places that
    state it, and they are held together here so neither can drift alone.
    """
    declared = re.search(r'requires-python\s*=\s*"[><=]*([0-9]+\.[0-9]+)"',
                         PYPROJECT.read_text(encoding="utf-8"))
    proven = re.search(r'python-version:\s*"([0-9]+\.[0-9]+)"',
                       (PROJECT_ROOT / ".github" / "workflows" / "merge-gate.yml").read_text(encoding="utf-8"))
    assert declared and proven, "one of the two declarations has moved"

    assert declared.group(1) == proven.group(1), (
        f"pyproject says >={declared.group(1)} and the merge gate proves "
        f"{proven.group(1)}; the floor is whatever is actually run"
    )
