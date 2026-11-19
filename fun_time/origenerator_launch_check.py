from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .funestra_engine import its_own_error_without_the_traceback_frames
from .hosted_contract import published_by
from .window_layout import WindowLayoutPlan, WindowRect
from .windows_bridge_startup import (
    HandedFunestra,
    OrigeneratorSession,
    origenerator_interpreter,
    origenerator_launch_command,
    origenerator_launch_kwargs,
)

LAUNCH_CHECK_BUDGET_S = 300.0
SATELLITES = ("portrait", "landscape")

A_PLAN_PARSED_BUT_NEVER_PLACED = WindowLayoutPlan(
    portrait=WindowRect(2560, 3, 1440, 1720),
    landscape=WindowRect(2560, 1723, 1440, 1720),
    dashboard=WindowRect(0, 0, 854, 208),
    random_faves_browser=WindowRect(0, 208, 854, 1202),
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def a_session_in(folder: Path) -> OrigeneratorSession:
    return OrigeneratorSession(
        layout_plan=A_PLAN_PARSED_BUT_NEVER_PLACED,
        command_file=folder / "origenerator_cmd.txt",
        paused_file=folder / "origenerator_paused.txt",
        status_file=folder / "origenerator_status.txt",
        dashboard_cmd_file=folder / "dashboard_cmd.txt",
        funestras={satellite: HandedFunestra(
            playlist_file=folder / f"{satellite}_playlist.tsv",
            cmd_file=folder / f"{satellite}_cmd.txt",
            status_file=folder / f"{satellite}_status.txt",
            hud_file=folder / f"origenerator_{satellite}_hud.json",
            notice_file=folder / f"origenerator_{satellite}_notice.txt",
        ) for satellite in SATELLITES},
    )


def launch_check_command(checkout: Path, python_exe: str | Path, folder: Path) -> list[str] | None:
    flag = (published_by(checkout) or {}).get("check_launch_flag")
    if not flag:
        return None
    return [*origenerator_launch_command(
        python_exe=python_exe, origenerator_dir=checkout, session=a_session_in(folder)), flag]


def launch_check_kwargs(checkout: Path, sibling_checkouts: str | None) -> dict:
    kwargs = origenerator_launch_kwargs(
        origenerator_dir=checkout, sibling_checkouts=sibling_checkouts)
    env = dict(kwargs.get("env") or os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return {**kwargs, "env": env, "capture_output": True, "text": True}


def what_stops_the_hosted_launch(
        checkout: Path, *, sibling_checkouts: str | None = None,
        python_exe: str | Path | None = None, run: Runner = subprocess.run) -> str | None:
    with tempfile.TemporaryDirectory() as folder:
        command = launch_check_command(
            checkout, python_exe or origenerator_interpreter(checkout), Path(folder))
        if command is None:
            return None
        result = run(command, timeout=LAUNCH_CHECK_BUDGET_S,
                     **launch_check_kwargs(checkout, sibling_checkouts))
    if result.returncode == 0:
        return None
    return its_own_error_without_the_traceback_frames(result.stderr or "") or (
        f"it exited with code {result.returncode} and said nothing")
