from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.hosted_contract import CONTRACT_FILE, declared_flags, published_by
from fun_time.origenerator_launch_check import (
    A_PLAN_PARSED_BUT_NEVER_PLACED,
    CHECKS_IN_ORDER,
    LAUNCH_CHECK,
    LAUNCH_CHECK_BUDGET_S,
    a_session_in,
    launch_check_command,
    launch_check_kwargs,
)
from fun_time.windows_bridge_startup import (
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


def _run_the_launch(tmp_path: Path, extra: list[str],
                    check: str = LAUNCH_CHECK) -> subprocess.CompletedProcess:
    checkout, python_exe = _the_origenerator_this_branchs_session_would_launch()
    command = launch_check_command(checkout, python_exe, tmp_path, check)
    if command is None:
        pytest.skip(f"the hosted app at {checkout} publishes no {check}")
    return run_within([*command, *extra], LAUNCH_CHECK_BUDGET_S,
                      **launch_check_kwargs(checkout, this_checkouts_siblings() or None))


@pytest.mark.parametrize("check", CHECKS_IN_ORDER)
def test_the_real_app_passes_each_check_on_the_interpreter_and_path_the_launch_names(
        tmp_path, check):
    result = _run_the_launch(tmp_path, [], check)

    assert result.returncode == 0, (
        "the hosted Origenerator could not boot on the command this session "
        f"would launch it with:\n{result.stderr[-4000:]}"
    )


def test_a_launch_that_cannot_import_fails_here(tmp_path):
    result = _run_the_launch(tmp_path, ["--no-such-flag"])

    assert result.returncode != 0


def test_the_command_under_test_is_the_one_production_builds(tmp_path):
    checkout, python_exe = _the_origenerator_this_branchs_session_would_launch()
    command = origenerator_launch_command(
        python_exe=python_exe,
        origenerator_dir=checkout,
        session=a_session_in(tmp_path),
    )

    assert command[1:3] == ["-m", "origenerator"]
    assert "--fun-time" in command
    the_browsers_rect_which_core_takes = A_PLAN_PARSED_BUT_NEVER_PLACED.random_faves_browser
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
        session=replace(a_session_in(tmp_path),
                        frames_file=tmp_path / "frame.bin", input_file=tmp_path / "input.txt"),
    )
    written = [word for word in command if word.startswith("--")]

    assert set(written) <= declared_flags(published)
    assert len(written) == len(set(written)), "a flag written twice"
    assert command[1:3] == ["-m", published["module"]]
