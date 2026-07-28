"""A branch session is a whole real session, and it runs the *other* checkout's code.

The unit tests prove the generated config keeps pointing at the machine's real
files.  What they cannot prove is the premise underneath it: that starting the
orchestrator with its working directory in another checkout actually swaps the
code — ``fun_time`` is not installed into the venv at all, so the working
directory is what resolves the package, and every child the session launches
inherits it.  That is a claim about a real process tree, so it is tested by
running one.

The stand-in checkout here is a copy of this one rather than a ``git worktree
add``: the mechanism cares about the directory, not about git, and a run killed
mid-way would otherwise leave a worktree registered in the user's repo.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from fun_time.branch_session import build_branch_config

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

# What a session reads out of the checkout it runs from: the two packages it
# launches by module name, the hotkey script the orchestrator hands AHK, and the
# content overlay's committed fallback, which ``fun_time.content`` loads at
# import time.
_COPIED_TREES = ("fun_time", "satellite")
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
    return destination


@pytest.fixture
def branch_checkout_session():
    """A session on a branch config, launched out of a checkout of its own."""
    temp_root = build_integration_temp_root()
    branch_checkout = _copy_checkout(temp_root / "branch_checkout")
    # The isolated config plays the primary's: its ports, broker launcher and
    # microphone are already stripped, and build_branch_config carries all of
    # that through — so this session reaches nothing of the user's either.
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
        shutil.rmtree(temp_root, ignore_errors=True)


def test_a_branch_session_runs_the_branch_checkouts_code(branch_checkout_session):
    """The hotkey script the orchestrator launched names the branch checkout.

    That path is ``config.project_dir / "windows_bridge_hotkeys.ahk"``, and
    ``project_dir`` is ``config.PROJECT_DIR`` — the directory of the ``fun_time``
    package that is actually imported.  Naming the branch checkout is therefore
    proof that the branch's code ran, not the installed one's; every other
    artifact a session writes is addressed by config and would look identical
    either way.
    """
    session, branch_checkout = branch_checkout_session

    logged = session.wait_for_log("Launching AHK hotkey script", timeout=20)

    assert str(branch_checkout / "windows_bridge_hotkeys.ahk") in logged


def test_a_branch_session_brings_up_a_whole_session_in_the_worktrees_state(
    branch_checkout_session,
):
    """Not a bare orchestrator: the players and companions launched too, and
    everything they write is in the worktree rather than beside the live
    session's."""
    session, branch_checkout = branch_checkout_session
    state_dir = branch_checkout / "state"

    children = session.read_child_pids()

    assert all(children[role] for role in ("nau_pid", "portrait_pid", "landscape_pid"))
    assert (state_dir / "bridge_pids.ini").is_file()
    assert (state_dir / "windows_bridge_launch.ini").is_file()
    assert session.windows_bridge_log.parent == state_dir
