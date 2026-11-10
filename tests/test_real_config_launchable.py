from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.config import DEFAULT_CONFIG_PATH, load_config
from fun_time.orchestrator import validate_config
from tests.integration.integration_support import on_the_settings_this_machine_has

pytestmark = pytest.mark.skipif(
    not DEFAULT_CONFIG_PATH.is_file(),
    reason=(f"no local config at {DEFAULT_CONFIG_PATH} (git-ignored; absent in CI); "
            "only a run that names this file gets here past conftest's collect_ignore"),
)

# A worktree's ``.git`` is a file pointing at the Main Funestra's admin dir, not a dir.
_IN_A_WORKTREE = (Path(__file__).resolve().parents[1] / ".git").is_file()


@pytest.fixture(autouse=True)
def _the_settings_this_machine_has():
    with on_the_settings_this_machine_has():
        yield


def test_the_default_config_loads():
    config = load_config()

    assert config.config_path == DEFAULT_CONFIG_PATH


def test_each_path_that_moves_when_another_checkout_does_is_named_when_missing():
    paths = load_config().paths

    for label, path in (
        ("paths.python_exe", paths.python_exe),
        ("paths.ahk_exe", paths.ahk_exe),
    ):
        if path is None:
            continue
        assert path.exists(), f"{label} names {path}, which is not there"


@pytest.mark.skipif(
    _IN_A_WORKTREE,
    reason=(
        "validate_config resolves the browser shortcut relative to the checkout, "
        "and that file is untracked, so it exists only in the primary -- which is "
        "also the only checkout the taskbar icon launches"
    ),
)
def test_the_icon_would_do_nothing_without_everything_the_launch_requires():
    _validate_without_the_session_lock_main_would_take(load_config())


def _validate_without_the_session_lock_main_would_take(config) -> None:
    validate_config(config)
