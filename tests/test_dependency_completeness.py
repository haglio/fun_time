"""What this repo needs is what its pyproject says, in each of the ways it says it.

A package that imports something nobody declared works on the machine that
happened to have it and dies on the merge gate, which installs exactly what the
pyproject says; so does a version nobody bounded, a sibling checkout nobody
recorded, and a Python floor no run proves.  The gates are the family's
(``app_support.dependencies``); what is here is which packages are this repo's
own and which trees to read.
"""
from __future__ import annotations

from pathlib import Path

from app_support.dependencies import (
    assert_every_dependency_is_bounded,
    assert_every_import_is_declared,
    assert_every_sibling_is_declared,
    assert_the_declared_floor_is_the_one_the_gate_runs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Every top-level package ships from this repo, so all must be covered: the
# satellite player pulls in numpy and Pillow that the orchestrator alone would
# not have justified, and fun_time_vr brings the OpenXR/GL stack.
PACKAGE_DIRS = (
    PROJECT_ROOT / "fun_time",
    PROJECT_ROOT / "satellite",
    PROJECT_ROOT / "fun_time_vr",
)
TREES = (*PACKAGE_DIRS, PROJECT_ROOT / "tests", PROJECT_ROOT / "tools",
         PROJECT_ROOT / "vulture_whitelist.py")
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
MERGE_GATE = PROJECT_ROOT / ".github" / "workflows" / "merge-gate.yml"


def test_all_third_party_imports_declared_in_pyproject():
    # The gate is the family's; what is this repo's is which packages are its
    # own and the two import names its pyproject spells differently.
    assert_every_import_is_declared(
        PROJECT_ROOT, PACKAGE_DIRS, PYPROJECT,
        local=("fun_time", "satellite", "fun_time_vr"),
        import_names={"glfw": "glfw", "PyQt6": "PyQt6"})


def test_every_requirement_has_an_upper_bound():
    assert_every_dependency_is_bounded(PYPROJECT)


def test_every_sibling_this_repo_needs_is_declared():
    assert_every_sibling_is_declared(PROJECT_ROOT, TREES, PYPROJECT)


def test_the_declared_python_floor_is_the_one_ci_actually_runs():
    """``requires-python`` said >=3.10 while the tree needed 3.12 and CI ran it.

    ``branch_session.py`` has an f-string carrying a `"` and a backslash inside
    its replacement field — both PEP 701, both a SyntaxError before 3.12 — so a
    3.10 or 3.11 install advertised as supported cannot even import this
    package.  This repo found that first and held the two declarations together
    for itself; the check is now the family's, and reads the lowest version any
    leg of the gate runs rather than the first one it finds.
    """
    assert_the_declared_floor_is_the_one_the_gate_runs(PYPROJECT, MERGE_GATE)
