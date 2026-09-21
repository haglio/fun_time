"""What this session sends Genau, held to what Genau says it needs.

Both sides spelled the sixteen flags and two captions and nothing compared
them: a flag left off fell through to a default of Genau's, every suite green.
Walking up to its genau_contract.json is the one place they can be compared.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time.window_roles import GENAU_TITLE, GENAU_VIDEO_TITLE
from fun_time.windows_bridge_startup import genau_launch_command

CONTRACT = Path("genau") / "genau_contract.json"


def _promise() -> dict:
    for parent in Path(__file__).resolve().parents:
        published = parent / CONTRACT
        if published.is_file():
            return json.loads(published.read_text(encoding="utf-8"))
    pytest.skip(f"no {CONTRACT.as_posix()} beside this checkout")


def _the_real_command() -> list[str]:
    """Built by production, off values that name nothing on this machine."""
    return genau_launch_command(
        python_exe="a-venv/python.exe",
        genau_module="genau",
        config_path="a-checkout/genau_config.json",
        clips_folder="a-library/clips",
        genau_x=1, genau_y=2, genau_width=3, genau_height=4,
        command_file="a-state-dir/genau_cmd.txt",
        paused_file="a-state-dir/genau_paused.txt",
        console_file="a-state-dir/console.json",
        drive_file="a-state-dir/genau_drive.txt",
        status_file="a-state-dir/genau_status.txt",
        dashboard_cmd_file="a-state-dir/dashboard_cmd.txt",
        start_clip="a-clip.mp4",
    )


def test_the_launch_carries_every_flag_genau_requires():
    """The half argparse cannot see: a flag left off is a default of Genau's."""
    command = _the_real_command()

    assert set(_promise()["required_flags"]) <= set(command)


def test_the_launch_sends_no_flag_genau_does_not_know():
    """The other half, which argparse turns into an exit before Genau can log."""
    document = _promise()
    known = {*document["required_flags"], *document["optional_flags"]}

    sent = {word for word in _the_real_command() if word.startswith("--")}

    assert sent <= known


def test_the_module_run_is_the_one_genau_says_to_run():
    command = _the_real_command()

    assert command[1:3] == ["-m", _promise()["module"]]


def test_the_captions_this_session_resolves_the_window_by_are_genaus_own():
    """A caption spelled differently is a window this session never finds."""
    document = _promise()

    assert document["window_title"] == GENAU_TITLE
    assert document["video_window_title"] == GENAU_VIDEO_TITLE
