"""Unit tests: the integration suite runs on the hidden desktop or not at all.

Bare ``pytest tests/integration/`` has always been forbidden — it throws the
suite's real windows onto the user's monitors and grabs focus — but nothing
enforced it, and the harness quietly grew a second, machine-wide reap to serve
it.  Documentation is not a guard; this is.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration import conftest as integration_conftest
from tests.integration.hidden_desktop import (
    HIDDEN_DESKTOP_NAME,
    REFUSED_EXIT_CODE,
    on_hidden_desktop,
    require_hidden_desktop,
)
from tests.integration.line_record import RUN_RECORD_ENV


def test_a_run_on_the_hidden_desktop_is_allowed():
    require_hidden_desktop(desktop_name=lambda: HIDDEN_DESKTOP_NAME)


def test_a_run_on_the_users_own_desktop_is_refused():
    with pytest.raises(RuntimeError) as failure:
        require_hidden_desktop(desktop_name=lambda: "Default")

    assert "Default" in str(failure.value)


def test_the_refusal_names_the_runner_to_use_instead():
    with pytest.raises(RuntimeError) as failure:
        require_hidden_desktop(desktop_name=lambda: "Default")

    assert "tests.integration.hidden_desktop" in str(failure.value)


def test_the_guard_defaults_to_the_desktop_it_is_actually_running_on():
    """Wired to the real Win32 lookup rather than left to each caller — a unit
    run is on the ordinary desktop, so the default wiring must refuse."""
    with pytest.raises(RuntimeError):
        require_hidden_desktop()


def test_the_predicate_and_the_refusal_agree():
    assert on_hidden_desktop(desktop_name=lambda: HIDDEN_DESKTOP_NAME)
    assert not on_hidden_desktop(desktop_name=lambda: "Default")


def test_a_run_that_merely_sweeps_the_directory_in_is_refused_too(pytestconfig):
    without_the_exclusion = [
        directory for directory in pytestconfig.getini("norecursedirs") if directory != "integration"
    ]

    swept_in = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests",
            "-o", "norecursedirs=" + " ".join(without_the_exclusion),
            "--collect-only", "-q",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env={**os.environ, "PYTEST_ADDOPTS": "", "PYTHONIOENCODING": "utf-8"},
        check=False,
    )

    # The wording, not just the code: pytest spends exit 4 on any usage error, so
    # only the refusal's own sentence proves the guard is what stopped the run.
    said = swept_in.stdout + swept_in.stderr

    assert swept_in.returncode == REFUSED_EXIT_CODE, said
    assert f"must run on the '{HIDDEN_DESKTOP_NAME}' desktop" in said, said


@pytest.mark.skipif(sys.platform != "win32", reason="the refusal returns early off Windows")
def test_a_run_on_the_hidden_desktop_that_did_not_write_itself_down_for_line_is_refused(monkeypatch):
    monkeypatch.setattr(integration_conftest, "require_hidden_desktop", lambda: None)
    monkeypatch.delenv(RUN_RECORD_ENV, raising=False)

    with pytest.raises(pytest.exit.Exception) as refusal:
        integration_conftest.refuse_a_run_that_may_not_start()

    assert refusal.value.returncode == REFUSED_EXIT_CODE
    assert "-m tests.integration.hidden_desktop" in str(refusal.value)


def test_the_unit_suite_never_inherits_the_integration_flag():
    assert os.environ.get("FUN_TIME_RUN_INTEGRATION") is None


@pytest.mark.repo_check
def test_every_integration_module_stays_off_a_machine_that_is_not_windows():
    """Off Windows the session-start refusal returns early, so a module's own
    platform mark is all that keeps ``pytest tests/integration/`` from starting
    the satellite and VR Funestras there.  Two of the fourteen once carried none
    (bug 35); this walks them all so a fifteenth cannot arrive without one."""
    integration = Path(__file__).resolve().parent / "integration"
    unguarded = [
        module.name
        for module in sorted(integration.glob("test_*.py"))
        if not _skips_off_windows(module.read_text(encoding="utf-8"))
    ]

    assert unguarded == []


def _skips_off_windows(source: str) -> bool:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            return "sys.platform != 'win32'" in ast.unparse(node.value)
    return False
