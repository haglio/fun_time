"""Unit tests: the integration suite runs on the hidden desktop or not at all.

Bare ``pytest tests/integration/`` has always been forbidden — it throws the
suite's real windows onto the user's monitors and grabs focus — but nothing
enforced it, and the harness quietly grew a second, machine-wide reap to serve
it.  Documentation is not a guard; this is.
"""
from __future__ import annotations

import pytest

from tests.integration.hidden_desktop import HIDDEN_DESKTOP_NAME, require_hidden_desktop


def test_a_run_on_the_hidden_desktop_is_allowed():
    require_hidden_desktop(desktop_name=lambda: HIDDEN_DESKTOP_NAME)


def test_a_run_on_the_users_own_desktop_is_refused():
    """The user's desktop is where their session lives.  A suite that launches
    real windows, a real AHK bridge and real players there is a second session
    on top of theirs, however carefully the rest of it is scoped."""
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
