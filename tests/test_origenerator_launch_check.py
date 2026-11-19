from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fun_time import origenerator_launch_check
from fun_time.hosted_contract import CONTRACT_FILE


def _a_checkout_publishing(tmp_path: Path, **contract) -> Path:
    checkout = tmp_path / "origenerator"
    checkout.mkdir()
    (checkout / CONTRACT_FILE).write_text(
        json.dumps({"required_flags": ["--fun-time"], **contract}), encoding="utf-8")
    return checkout


def _an_app_that_exits(code: int, stderr: str = ""):
    asked = []

    def run(command, **kwargs):
        asked.append((command, kwargs))
        return subprocess.CompletedProcess(command, code, "", stderr)

    return run, asked


def test_the_check_runs_the_launch_command_with_the_contracts_check_flag(tmp_path):
    checkout = _a_checkout_publishing(tmp_path, check_launch_flag="--check-launch")
    run, asked = _an_app_that_exits(0)

    assert origenerator_launch_check.what_stops_the_hosted_launch(
        checkout, python_exe=tmp_path / "python.exe", run=run) is None

    command, kwargs = asked[0]
    assert command[-2:] == ["--fun-time", "--check-launch"], (
        "the check is the launch a session makes, with the app told to stop once it would boot")
    assert kwargs["cwd"] == str(checkout)
    assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen", "a check may put no window on his screen"


def test_an_app_that_cannot_launch_is_answered_with_its_own_last_words(tmp_path):
    checkout = _a_checkout_publishing(tmp_path, check_launch_flag="--check-launch")
    run, _asked = _an_app_that_exits(1, stderr=(
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 1, in <module>\n"
        "ImportError: cannot import name 'ExamplePainter' from 'funestra.example'\n"))

    said = origenerator_launch_check.what_stops_the_hosted_launch(
        checkout, python_exe=tmp_path / "python.exe", run=run)

    assert said == "ImportError: cannot import name 'ExamplePainter' from 'funestra.example'"


def test_an_app_that_publishes_no_check_is_not_asked(tmp_path):
    checkout = _a_checkout_publishing(tmp_path)
    run, asked = _an_app_that_exits(1)

    assert origenerator_launch_check.what_stops_the_hosted_launch(
        checkout, python_exe=tmp_path / "python.exe", run=run) is None
    assert asked == []
