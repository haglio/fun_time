"""The REAL hosted Origenerator, launched by the REAL command, proven to boot.

Everything else about origenerator mode is tested against a stub app (see
``test_origenerator_mode_integration``), because the suite must not touch the
machine's one ComfyUI, GPU queue or gallery database.  That leaves one gap the
stub can never cover, and it is the one that actually bit: whether the real app
STARTS.  A session came up missing Origenerator entirely because a module it
imports could not resolve under the interpreter this launch names — the process
died before logging was configured, so there was no traceback anywhere, and the
startup sequencer sat out its whole timeout waiting for a window that was never
coming.  Every unit test in both repos was green through it: they run on a venv
where the sibling checkouts are installed, and this launch does not.

So this runs the launch itself.  ``--check-launch`` (origenerator.fun_time_mode)
is a mode the app grew for exactly this: it boots as far as importing everything
the launch imports, then exits 0 without opening the database, reaching ComfyUI,
or showing a window.  So a run here costs the machine nothing and contends with
no live session — while still failing on the real interpreter, the real working
directory, the real PYTHONPATH and the real argv.

The command is built by the production functions rather than written out here.
A hand-written copy is exactly what would keep passing while production broke.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.windows_bridge_startup import (
    origenerator_launch_command,
    origenerator_launch_kwargs,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

# Rects with no bearing on anything: --check-launch never places a window, and
# the point here is that the app can PARSE and boot on the contract's shape.
_PLAN = WindowLayoutPlan(
    portrait=WindowRect(2560, 3, 1440, 1720),
    landscape=WindowRect(2560, 1723, 1440, 1720),
    dashboard=WindowRect(0, 0, 854, 208),
    random_favs_browser=WindowRect(0, 208, 854, 1202),
)


def _real_config():
    """The user's own config, which is where the hosted checkout is named.

    Skipped rather than faked when it is absent (a fresh clone, CI): the whole
    value of this test is that it runs the launch the machine would really run,
    and a fabricated interpreter path would only prove the fabrication boots.
    """
    here = Path(__file__).resolve()
    # This checkout first, then the primary when this is a worktree: the config
    # is git-ignored, so it exists only in the primary — which sits exactly
    # three levels above a worktree (<primary>/.claude/worktrees/<name>).
    roots = [here.parents[2]]
    if here.parents[3].name == "worktrees" and here.parents[4].name == ".claude":
        roots.append(here.parents[5])
    for root in roots:
        candidate = root / "fun_time_config.json"
        if candidate.exists():
            return load_config(candidate)
    return None


def _named_checkout() -> Path | None:
    """This worktree's own ``state/origenerator_dir.txt``, if it names one.

    The same override a branch session reads (fun_time.branch_session), for the
    same reason: a worktree under judgment is usually paired with a worktree of
    Origenerator, and the launch worth proving here is the one THIS branch would
    make — not the primary install's, which is what the machine's config names.
    """
    override = Path(__file__).resolve().parents[2] / "state" / "origenerator_dir.txt"
    if not override.exists():
        return None
    for line in override.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            named = Path(line)
            # Only if it is still THERE.  A worktree retired after its branch
            # landed leaves this file behind naming a directory that no longer
            # exists, and returning it turned the test below into a skip — a
            # guard that silently stops guarding, which is the failure mode it
            # was written against in the first place.  Fall through to the
            # config's own checkout instead, which is the launch worth proving
            # once a branch is gone.
            return named if named.exists() else None
    return None


def _hosted_checkout_and_python():
    config = _real_config()
    if config is None:
        pytest.skip("no local fun_time_config.json (git-ignored; absent in CI)")
    checkout = _named_checkout() or config.paths.origenerator_dir
    if not checkout or not Path(checkout).exists():
        pytest.skip("this session hosts no Origenerator (paths.origenerator_dir)")
    python_exe = config.paths.origenerator_python_exe or config.paths.python_exe
    if not python_exe or not Path(python_exe).exists():
        pytest.skip(f"the hosted app's interpreter is missing: {python_exe}")
    return Path(checkout), Path(python_exe)


def _run_the_launch(tmp_path: Path, extra: list[str]) -> subprocess.CompletedProcess:
    checkout, python_exe = _hosted_checkout_and_python()
    command = origenerator_launch_command(
        python_exe=python_exe,
        layout_plan=_PLAN,
        command_file=tmp_path / "origenerator_cmd.txt",
        paused_file=tmp_path / "origenerator_paused.txt",
        status_file=tmp_path / "origenerator_status.txt",
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
    )
    kwargs = origenerator_launch_kwargs(origenerator_dir=checkout)
    # Windowless whatever the app would otherwise do, and captured so a failure
    # arrives as its traceback rather than as an exit code to go hunting for.
    kwargs.pop("creationflags", None)
    kwargs.pop("startupinfo", None)
    env = dict(kwargs.pop("env", None) or {})
    if not env:
        import os

        env = {**os.environ}
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [*command, *extra], **kwargs, env=env,
        capture_output=True, text=True, timeout=300,
    )


def test_the_real_launch_command_boots_the_real_app(tmp_path):
    """Failing here is a session that comes up with Origenerator missing."""
    result = _run_the_launch(tmp_path, ["--check-launch"])

    assert result.returncode == 0, (
        "the hosted Origenerator could not boot on the command this session "
        f"would launch it with:\n{result.stderr[-4000:]}"
    )


def test_a_launch_that_cannot_import_fails_here(tmp_path):
    """A negative control.  Without it, an app that ignored --check-launch and
    exited 0 regardless would make the test above pass vacuously — and this test
    exists because a guard that could not see the break is what let the break
    ship."""
    result = _run_the_launch(tmp_path, ["--check-launch", "--no-such-flag"])

    assert result.returncode != 0


def test_the_command_under_test_is_the_one_production_builds(tmp_path):
    """The contract that keeps this honest: what ran above is the production
    argv, not a copy of it that can drift.  A rect the app parses into its
    session, the file channel, and the module the launcher runs."""
    checkout, python_exe = _hosted_checkout_and_python()
    command = origenerator_launch_command(
        python_exe=python_exe,
        layout_plan=_PLAN,
        command_file=tmp_path / "c.txt",
        paused_file=tmp_path / "p.txt",
        status_file=tmp_path / "s.txt",
        dashboard_cmd_file=tmp_path / "d.txt",
    )

    assert command[1:3] == ["-m", "origenerator"]
    assert "--fun-time" in command
    # The RFB's rect is the main window's, and both regions are named.
    assert command[command.index("--width") + 1] == "854"
    assert command[command.index("--portrait_height") + 1] == "1720"
    assert origenerator_launch_kwargs(origenerator_dir=checkout)["cwd"] == str(checkout)
