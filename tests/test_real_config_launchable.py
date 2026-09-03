"""The config this machine will actually launch with must name things that exist.

Every other guard here stops one step short of that. ``test_startup_imports``
drives the launch graph's *import* phase and never calls ``main()``, so
``validate_config`` is never reached -- and it forces overlay reads onto
``content.example.json``, a different file from ``fun_time_config.json``, which
it never opens. ``test_orchestrator.TestValidateConfig`` does call
``validate_config``, but on a fixture config whose executables it creates under
``tmp_path``. The integration tests are the only ones that load the real config,
and none of them validates it.

So nothing asserted that the paths in the real config point at anything. Moving
the checkouts out of the synced tree invalidated three of them, and Fun Time
died on the first -- ``FileNotFoundError`` from ``require_file``, before any
window, with the launcher's hidden console the only place it was written down.

This is that assertion and nothing more: load the default config the way
``main()`` does, and check it. It calls ``validate_config`` directly rather than
``orchestrator.main(--check)`` on purpose -- that path also takes the
single-instance lock (so it fails whenever Fun Time is genuinely running) and
stamps the taskbar shortcut's AppUserModelID, neither of which a test should do
to the machine it runs on.

``fun_time_config.json`` is git-ignored, so a fresh or public checkout -- and CI
-- has none and everything here skips. This is a check on the machine's state,
not on the code.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.config import DEFAULT_CONFIG_PATH, load_config
from fun_time.orchestrator import validate_config

# Off the machine the whole file is uncollected (see tests/conftest.py); this
# mark only backstops a direct invocation naming the file, which bypasses
# collect_ignore.
pytestmark = pytest.mark.skipif(
    not DEFAULT_CONFIG_PATH.is_file(),
    reason=f"no local config at {DEFAULT_CONFIG_PATH} (git-ignored; absent in CI)",
)

# A worktree's ``.git`` is a file pointing at the main player's admin dir, not a dir.
_IN_A_WORKTREE = (Path(__file__).resolve().parents[1] / ".git").is_file()


def test_the_default_config_loads():
    """A config that cannot even be read is the earlier half of the same failure."""
    config = load_config()

    assert config.config_path == DEFAULT_CONFIG_PATH


def test_the_paths_that_reach_outside_this_checkout_are_where_the_config_says():
    """The sibling executables and the sibling app's config, named individually.

    These are the ones that move, because they live in other repos; a bare
    FileNotFoundError from ``validate_config`` does not say which. Checked in a
    worktree too -- they are absolute, so they do not vary by checkout.
    """
    paths = load_config().paths

    for label, path in (
        ("paths.python_exe", paths.python_exe),
        ("paths.ahk_exe", paths.ahk_exe),
        ("paths.genau_config_path", paths.genau_config_path),
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
def test_everything_the_launch_requires_exists():
    """``validate_config`` is what ``main()`` runs before committing to a launch.

    Failing here means the icon would do nothing: the orchestrator raises before
    any window, and the launcher's console is hidden.
    """
    validate_config(load_config())
