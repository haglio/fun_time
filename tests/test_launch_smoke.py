from __future__ import annotations

import re

import pytest
from app_support.launcher import launchers

from tests.launcher_python import (
    THE_CHECKOUT_A_LAUNCHER_STARTS_IN,
    run_python_the_way_a_launcher_does,
    without_the_pythonpath_no_launcher_sets,
)

_LAUNCH_MODULE = re.compile(r'-m\s+([A-Za-z_][\w.]*)')

_ENTRY_POINTS_NO_LAUNCHER_NAMES = (
    ("session_handoff (started by an orchestrator on its way out, so a dead one shows nothing)",
     "fun_time.session_handoff"),
    ("windows_bridge_orchestrator (loaded under the loading screen, most sibling imports)",
     "fun_time.windows_bridge_orchestrator"),
)


def _modules_read_from_the_launchers_own_specs() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for launcher in launchers(THE_CHECKOUT_A_LAUNCHER_STARTS_IN):
        found.extend((launcher.file, module) for module in _LAUNCH_MODULE.findall(launcher.run))
    return found


LAUNCHED = [
    *_ENTRY_POINTS_NO_LAUNCHER_NAMES,
    *_modules_read_from_the_launchers_own_specs(),
]


def test_the_launchers_name_modules_to_check():
    assert [module for launcher, module in LAUNCHED if launcher.endswith(".vbs")]


def test_the_checked_launchers_are_read_from_their_own_specs():
    launchers_checked = {launcher for launcher, _module in LAUNCHED if launcher.endswith(".vbs")}

    assert launchers_checked == {
        launcher.file for launcher in launchers(THE_CHECKOUT_A_LAUNCHER_STARTS_IN)
        if _LAUNCH_MODULE.search(launcher.run)}


def test_a_pythonpath_lent_to_the_test_run_never_vouches_for_a_launch():
    lent = {"PYTHONPATH": "an unlanded sibling branch", "PATH": "kept"}

    assert without_the_pythonpath_no_launcher_sets(lent) == {"PATH": "kept"}


@pytest.mark.parametrize(
    ("launcher", "module"),
    LAUNCHED,
    ids=[f"{launcher}-{module}" for launcher, module in LAUNCHED],
)
def test_a_launcher_s_module_imports_the_way_it_is_run(launcher, module):
    result = run_python_the_way_a_launcher_does(f"import {module}")

    assert result.returncode == 0, f"{launcher} runs {module}:\n{result.stderr}"
