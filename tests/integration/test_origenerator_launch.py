from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.hosted_contract import CONTRACT_FILE, declared_flags, published_by
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.windows_bridge_startup import (
    HandedFunestra,
    OrigeneratorSession,
    origenerator_interpreter,
    origenerator_launch_command,
    origenerator_launch_kwargs,
)
from tests.integration.integration_support import (
    on_the_settings_this_machine_has,
    run_within,
    this_checkouts_siblings,
)
from tests.origenerator_contract import named_checkout_unless_retired

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


@pytest.fixture(autouse=True)
def _the_settings_this_machine_has():
    with on_the_settings_this_machine_has():
        yield

_A_PLAN_PARSED_BUT_NEVER_PLACED = WindowLayoutPlan(
    portrait=WindowRect(2560, 3, 1440, 1720),
    landscape=WindowRect(2560, 1723, 1440, 1720),
    dashboard=WindowRect(0, 0, 854, 208),
    random_faves_browser=WindowRect(0, 208, 854, 1202),
)


def _funestras(tmp_path: Path) -> dict[str, HandedFunestra]:
    return {
        satellite: HandedFunestra(
            playlist_file=tmp_path / f"{satellite}_playlist.tsv",
            cmd_file=tmp_path / f"{satellite}_cmd.txt",
            status_file=tmp_path / f"{satellite}_status.txt",
            hud_file=tmp_path / f"origenerator_{satellite}_hud.json",
        )
        for satellite in ("portrait", "landscape")
    }


def _this_machines_own_config():
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


def _the_origenerator_this_branchs_session_would_launch():
    config = _this_machines_own_config()
    if config is None:
        pytest.skip("no local fun_time_config.json (git-ignored; absent in CI), "
                    "and a made-up one would prove only that what it made up boots")
    checkout = named_checkout_unless_retired() or config.paths.origenerator_dir
    if not checkout or not Path(checkout).exists():
        pytest.skip("this session hosts no Origenerator (paths.origenerator_dir)")
    python_exe = config.paths.origenerator_python_exe or origenerator_interpreter(checkout)
    if not python_exe or not Path(python_exe).exists():
        pytest.skip(f"the hosted app's interpreter is missing: {python_exe}")
    return Path(checkout), Path(python_exe)


def _run_the_launch(tmp_path: Path, extra: list[str]) -> subprocess.CompletedProcess:
    checkout, python_exe = _the_origenerator_this_branchs_session_would_launch()
    command = origenerator_launch_command(
        python_exe=python_exe,
        origenerator_dir=checkout,
        session=OrigeneratorSession(
            layout_plan=_A_PLAN_PARSED_BUT_NEVER_PLACED,
            command_file=tmp_path / "origenerator_cmd.txt",
            paused_file=tmp_path / "origenerator_paused.txt",
            status_file=tmp_path / "origenerator_status.txt",
            dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            funestras=_funestras(tmp_path)),
    )
    kwargs = origenerator_launch_kwargs(
        origenerator_dir=checkout, sibling_checkouts=this_checkouts_siblings() or None)
    return run_within([*command, *extra], 300,
                      **_shown_nowhere_with_its_traceback_captured(kwargs))


def _shown_nowhere_with_its_traceback_captured(production_kwargs: dict) -> dict:
    kwargs = dict(production_kwargs)
    kwargs.pop("creationflags", None)
    kwargs.pop("startupinfo", None)
    env = dict(kwargs.pop("env", None) or {})
    if not env:
        env = {**os.environ}
    env["QT_QPA_PLATFORM"] = "offscreen"
    return {**kwargs, "env": env, "capture_output": True, "text": True}


def test_the_real_app_imports_on_the_interpreter_and_path_the_launch_names(tmp_path):
    result = _run_the_launch(tmp_path, ["--check-launch"])

    assert result.returncode == 0, (
        "the hosted Origenerator could not boot on the command this session "
        f"would launch it with:\n{result.stderr[-4000:]}"
    )


def test_a_launch_that_cannot_import_fails_here(tmp_path):
    result = _run_the_launch(tmp_path, ["--check-launch", "--no-such-flag"])

    assert result.returncode != 0


def test_the_command_under_test_is_the_one_production_builds(tmp_path):
    checkout, python_exe = _the_origenerator_this_branchs_session_would_launch()
    command = origenerator_launch_command(
        python_exe=python_exe,
        origenerator_dir=checkout,
        session=OrigeneratorSession(
            layout_plan=_A_PLAN_PARSED_BUT_NEVER_PLACED,
            command_file=tmp_path / "c.txt",
            paused_file=tmp_path / "p.txt",
            status_file=tmp_path / "s.txt",
            dashboard_cmd_file=tmp_path / "d.txt",
            funestras=_funestras(tmp_path)),
    )

    assert command[1:3] == ["-m", "origenerator"]
    assert "--fun-time" in command
    the_browsers_rect_which_core_takes = _A_PLAN_PARSED_BUT_NEVER_PLACED.random_faves_browser
    assert command[command.index("--width") + 1] == str(the_browsers_rect_which_core_takes.width)
    assert command[command.index("--portrait-playlist") + 1] == str(
        tmp_path / "portrait_playlist.tsv")
    assert command[command.index("--landscape-hud-file") + 1] == str(
        tmp_path / "origenerator_landscape_hud.json")
    assert origenerator_launch_kwargs(origenerator_dir=checkout)["cwd"] == str(checkout)


def _the_contract_it_publishes(checkout: Path) -> dict:
    published = published_by(checkout)
    if published is None:
        pytest.skip(f"the hosted app at {checkout} publishes no {CONTRACT_FILE}")
    return published


def test_every_flag_this_session_sends_is_one_the_installed_copy_declares(tmp_path):
    checkout, python_exe = _the_origenerator_this_branchs_session_would_launch()
    published = _the_contract_it_publishes(checkout)
    command = origenerator_launch_command(
        python_exe=python_exe, origenerator_dir=checkout,
        session=OrigeneratorSession(
            layout_plan=_A_PLAN_PARSED_BUT_NEVER_PLACED,
            command_file=tmp_path / "c.txt", paused_file=tmp_path / "p.txt",
            status_file=tmp_path / "s.txt", dashboard_cmd_file=tmp_path / "d.txt",
            funestras=_funestras(tmp_path),
            frames_file=tmp_path / "frame.bin", input_file=tmp_path / "input.txt"),
    )
    written = [word for word in command if word.startswith("--")]

    assert set(written) <= declared_flags(published)
    assert len(written) == len(set(written)), "a flag written twice"
    assert command[1:3] == ["-m", published["module"]]
