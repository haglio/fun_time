from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from fun_time.branch_session import build_branch_config
from fun_time.content import EXAMPLE_CONTENT
from fun_time.project_paths import (
    HOTKEY_SCRIPT_NAME,
    SIBLING_CHECKOUTS_OVERRIDE_NAME,
    STATE_DIRNAME,
    sibling_checkouts_named_in,
)

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    settings_of_the_run_at,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

CHECKOUT_DIR = Path(__file__).resolve().parents[2]

_COPIED_TREES = ("fun_time", "main_funestra", "satellite")
_THE_FILE_IMPORTING_FUN_TIME_READS = EXAMPLE_CONTENT.name
_COPIED_FILES = (HOTKEY_SCRIPT_NAME, _THE_FILE_IMPORTING_FUN_TIME_READS)


def _a_copied_checkout_registered_in_no_repo(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for tree in _COPIED_TREES:
        shutil.copytree(
            CHECKOUT_DIR / tree,
            destination / tree,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    for name in _COPIED_FILES:
        shutil.copyfile(CHECKOUT_DIR / name, destination / name)
    named = sibling_checkouts_named_in(CHECKOUT_DIR / STATE_DIRNAME)
    if named is not None:
        (destination / STATE_DIRNAME).mkdir(parents=True, exist_ok=True)
        (destination / STATE_DIRNAME / SIBLING_CHECKOUTS_OVERRIDE_NAME).write_text(
            "\n".join(named), encoding="utf-8")
    return destination


def _a_branch_config_as_stripped_as_the_runs(branch_checkout: Path, temp_root: Path) -> Path:
    primary_config = build_integration_config(temp_root)
    branch_config = build_branch_config(
        branch_checkout,
        primary_config_path=primary_config,
        primary=CHECKOUT_DIR,
    )
    shutil.copyfile(settings_of_the_run_at(primary_config), settings_of_the_run_at(branch_config))
    return branch_config


def _the_one_path_the_imported_code_places_not_the_config(checkout: Path) -> Path:
    return checkout / HOTKEY_SCRIPT_NAME


@pytest.fixture
def branch_checkout_session():
    temp_root = build_integration_temp_root()
    branch_checkout = _a_copied_checkout_registered_in_no_repo(temp_root / "branch_checkout")
    session = FunTimeIntegrationSession(
        _a_branch_config_as_stripped_as_the_runs(branch_checkout, temp_root))
    try:
        session.start(project_dir=branch_checkout)
        yield session, branch_checkout
    finally:
        session.stop()


def test_a_branch_session_runs_the_branch_checkouts_code(branch_checkout_session):
    session, branch_checkout = branch_checkout_session

    logged = session.wait_for_log("Launching AHK hotkey script")

    assert str(_the_one_path_the_imported_code_places_not_the_config(branch_checkout)) in logged


def test_a_branch_session_brings_up_a_whole_session_in_the_worktrees_state(
    branch_checkout_session,
):
    session, branch_checkout = branch_checkout_session
    state_dir = branch_checkout / "state"

    children = session.read_child_pids()

    assert all(children[role] for role in ("main_funestra_pid", "portrait_pid", "landscape_pid"))
    assert (state_dir / "bridge_pids.ini").is_file()
    assert (state_dir / "windows_bridge_launch.ini").is_file()
    assert session.windows_bridge_log.parent == state_dir
