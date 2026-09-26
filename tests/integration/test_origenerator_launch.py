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

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.window_roles import ORIGENERATOR_TITLE
from fun_time.windows_bridge_startup import (
    HandedPlayer,
    origenerator_interpreter,
    origenerator_launch_command,
    origenerator_launch_kwargs,
)
from tests.integration.integration_support import checkout_project_dirs
from tests.origenerator_contract import CONTRACT_FILE, named_checkout, published_by

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


def _players(tmp_path: Path) -> dict[str, HandedPlayer]:
    """Both satellite players, handed over the way a session hands them."""
    return {
        side: HandedPlayer(
            playlist_file=tmp_path / f"{side}_playlist.tsv",
            cmd_file=tmp_path / f"{side}_cmd.txt",
            status_file=tmp_path / f"{side}_status.txt",
            hud_file=tmp_path / f"origenerator_{side}_hud.json",
        )
        for side in ("portrait", "landscape")
    }


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


def _hosted_checkout_and_python():
    """The Origenerator this branch would launch: the one its own
    ``state/origenerator_dir.txt`` names, which is how a worktree under
    judgment is paired with a worktree of Origenerator, else the one the
    machine's config names."""
    config = _real_config()
    if config is None:
        pytest.skip("no local fun_time_config.json (git-ignored; absent in CI)")
    checkout = named_checkout() or config.paths.origenerator_dir
    if not checkout or not Path(checkout).exists():
        pytest.skip("this session hosts no Origenerator (paths.origenerator_dir)")
    python_exe = config.paths.origenerator_python_exe or origenerator_interpreter(checkout)
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
        players=_players(tmp_path),
    )
    # The same siblings the session would hand it: a branch of player_core on
    # its PYTHONPATH, exactly as launch_origenerator passes them along.
    kwargs = origenerator_launch_kwargs(
        origenerator_dir=checkout, project_dirs=checkout_project_dirs() or None)
    # Windowless whatever the app would otherwise do, and captured so a failure
    # arrives as its traceback rather than as an exit code to go hunting for.
    kwargs.pop("creationflags", None)
    kwargs.pop("startupinfo", None)
    env = dict(kwargs.pop("env", None) or {})
    if not env:
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
    session, the players it is handed, and the module the launcher runs."""
    checkout, python_exe = _hosted_checkout_and_python()
    command = origenerator_launch_command(
        python_exe=python_exe,
        layout_plan=_PLAN,
        command_file=tmp_path / "c.txt",
        paused_file=tmp_path / "p.txt",
        status_file=tmp_path / "s.txt",
        dashboard_cmd_file=tmp_path / "d.txt",
        players=_players(tmp_path),
    )

    assert command[1:3] == ["-m", "origenerator"]
    assert "--fun-time" in command
    # The RFB's rect is the main window's, and both players are handed over.
    assert command[command.index("--width") + 1] == "854"
    assert command[command.index("--portrait-playlist") + 1] == str(
        tmp_path / "portrait_playlist.tsv")
    assert command[command.index("--landscape-hud-file") + 1] == str(
        tmp_path / "origenerator_landscape_hud.json")
    assert origenerator_launch_kwargs(origenerator_dir=checkout)["cwd"] == str(checkout)


def _the_contract_it_publishes(checkout: Path) -> dict:
    """What the hosted app says its launch takes and its window is called,
    read from the checkout this session would actually start."""
    published = published_by(checkout)
    if published is None:
        pytest.skip(f"the hosted app at {checkout} publishes no {CONTRACT_FILE}")
    return published


def test_this_session_sends_exactly_the_flags_the_hosted_app_declares(tmp_path):
    """The drift that used to go unseen, in both directions: a flag renamed
    over there killed the launch in argparse, and one this session stopped
    sending fell through to a default of nothing at all."""
    checkout, python_exe = _hosted_checkout_and_python()
    published = _the_contract_it_publishes(checkout)
    command = origenerator_launch_command(
        python_exe=python_exe, layout_plan=_PLAN,
        command_file=tmp_path / "c.txt", paused_file=tmp_path / "p.txt",
        status_file=tmp_path / "s.txt", dashboard_cmd_file=tmp_path / "d.txt",
        players=_players(tmp_path),
    )
    declared = {*published["required_flags"],
                *(flag for group in ("region_flags", "player_flags")
                  for side in published[group] for flag in published[group][side])}
    written = [word for word in command if word.startswith("--")]

    assert set(written) == declared
    assert len(written) == len(declared), "a flag written twice"
    assert command[1:3] == ["-m", published["module"]]


def test_the_caption_this_session_resolves_it_by_is_the_one_it_wears():
    """Resolved by caption as well as by pid, because a standalone one of his
    owns a window with the same name."""
    checkout, _python_exe = _hosted_checkout_and_python()

    assert _the_contract_it_publishes(checkout)["window_title"] == ORIGENERATOR_TITLE
