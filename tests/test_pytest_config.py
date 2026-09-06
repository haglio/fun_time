"""Unit tests: one pytest configuration file, and it is the one a caller would name.

pytest takes its whole configuration from a single file.  While these settings lived
in a ``pytest.ini`` beside ``pyproject.toml``, passing ``-c pyproject.toml`` — the
natural thing to reach for in a worktree, and what an agent did — selected a config
that declared nothing: no ``addopts`` (so no sanitize plugin and no timeout) and no
``norecursedirs``, which swept ``tests/integration/`` into what was meant to be a unit
run and put the suite's real players, Nau and AHK bridge on the user's monitors while
he was working.

Holding the settings in ``pyproject.toml`` is what makes an explicit ``-c`` and bare
discovery the same file.  A ``pytest.ini`` (or a ``[tool:pytest]`` / ``[pytest]``
section elsewhere) reintroduces the split silently, because it outranks
``pyproject.toml`` without saying so — hence the shadow check below.
"""
from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _settings() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as config:
        return tomllib.load(config)["tool"]["pytest"]["ini_options"]


def test_the_settings_live_in_pyproject():
    assert _settings()


def test_nothing_outranks_pyproject_as_the_config_file():
    """pytest's precedence is pytest.ini, then pyproject.toml, then tox.ini, then
    setup.cfg.  Any of the others carrying pytest settings takes the whole
    configuration with it, and the file a caller names stops being the file that
    was configured."""
    assert not (REPO_ROOT / "pytest.ini").exists()

    for name, section in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        candidate = REPO_ROOT / name
        if not candidate.exists():
            continue
        parsed = configparser.ConfigParser()
        parsed.read(candidate, encoding="utf-8")
        assert not parsed.has_section(section), f"{name} shadows pyproject.toml"


def test_an_ordinary_run_does_not_collect_the_integration_suite():
    """The convenience half of keeping integration runs off the user's screen.
    The guard half — which fires however the directory is reached — is
    tests/integration/conftest.py, pinned by tests/test_integration_desktop_guard.py."""
    assert "integration" in _settings()["norecursedirs"]


def test_the_sanitize_plugin_and_the_hang_clock_are_on_every_run():
    """Both were lost by the same wrong ``-c``: the run that leaked windows onto
    the user's screen also ran with the private-data guard switched off."""
    addopts = _settings()["addopts"]

    assert "app_support.sanitize.pytest_plugin" in addopts
    assert "--timeout=" in addopts
