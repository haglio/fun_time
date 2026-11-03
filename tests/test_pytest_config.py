from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_check

REPO_ROOT = Path(__file__).resolve().parent.parent


def _settings() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as config:
        return tomllib.load(config)["tool"]["pytest"]["ini_options"]


def test_the_settings_live_in_pyproject():
    assert _settings()


def test_nothing_outranks_pyproject_as_the_config_file():
    assert not (REPO_ROOT / "pytest.ini").exists()

    for name, section in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        candidate = REPO_ROOT / name
        if not candidate.exists():
            continue
        parsed = configparser.ConfigParser()
        parsed.read(candidate, encoding="utf-8")
        assert not parsed.has_section(section), f"{name} shadows pyproject.toml"


def test_an_ordinary_run_does_not_collect_the_integration_suite():
    assert "integration" in _settings()["norecursedirs"]


def test_the_sanitize_plugin_and_the_hang_clock_are_on_every_run():
    addopts = _settings()["addopts"]

    assert "app_support.sanitize.pytest_plugin" in addopts
    assert "--timeout=" in addopts
