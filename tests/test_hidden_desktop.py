"""Unit tests for the hidden-desktop integration runner's command building.

The Win32 desktop plumbing (create/launch/enumerate) is validated by actually
running the suite through it; here we pin the pure decision — what pytest command
the hidden desktop runs, and that it excludes the interactive foreground test by
*collection* (``--ignore``) rather than skip/deselect, so the run stays a clean
pass with nothing deselected.
"""
from __future__ import annotations

import sys

from tests.integration.hidden_desktop import INTERACTIVE_TEST_FILE, build_pytest_argv


def test_argv_runs_pytest_on_the_integration_dir():
    argv = build_pytest_argv([])
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]
    assert "tests/integration/" in argv


def test_argv_ignores_the_interactive_foreground_test():
    argv = build_pytest_argv([])
    assert f"--ignore={INTERACTIVE_TEST_FILE}" in argv
    assert INTERACTIVE_TEST_FILE == "tests/integration/test_startup_foreground_interactive.py"


def test_argv_appends_caller_args_after_the_defaults():
    argv = build_pytest_argv(["-k", "smoke", "-x"])
    assert argv[-3:] == ["-k", "smoke", "-x"]
