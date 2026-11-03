from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from fun_time.branch_session import build_branch_config
from fun_time.project_paths import (
    SIBLING_CHECKOUTS_OVERRIDE_NAME,
    STATE_DIRNAME,
    sibling_checkouts_named_in,
)

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

CHECKOUT_DIR = Path(__file__).resolve().parents[2]

_COPIED_TREES = ("fun_time", "main_funestra", "satellite")
_COPIED_FILES = ("windows_bridge_hotkeys.ahk", "content.example.json")


def _copy_checkout(destination: Path) -> Path:
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


@pytest.fixture
def branch_checkout_session():
    temp_root = build_integration_temp_root()
    branch_checkout = _copy_checkout(temp_root / "branch_checkout")
    branch_config = build_branch_config(
        branch_checkout,
        primary_config_path=build_integration_config(temp_root),
        primary=CHECKOUT_DIR,
    )
    session = FunTimeIntegrationSession(branch_config)
    try:
        session.start(project_dir=branch_checkout)
        yield session, branch_checkout
    finally:
        session.stop()


def test_a_branch_session_runs_the_branch_checkouts_code(branch_checkout_session):
    session, branch_checkout = branch_checkout_session

    logged = session.wait_for_log("Launching AHK hotkey script")

    assert str(branch_checkout / "windows_bridge_hotkeys.ahk") in logged


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
