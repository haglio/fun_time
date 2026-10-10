"""A sweep of the machine's processes is a question this process asks Windows."""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

from fun_time.process_sweep import Running, processes_matching
from fun_time.win32_process import ProcessEntry

TABLE = [
    ProcessEntry(pid=10, parent=1, image="pythonw.exe"),
    ProcessEntry(pid=11, parent=1, image="notepad.exe"),
    ProcessEntry(pid=12, parent=1, image="PythonW.EXE"),
]


def _matching(command_lines: dict[int, str | None], image: str, command_line: str):
    asked: list[int] = []

    def read(pid: int) -> str | None:
        asked.append(pid)
        return command_lines.get(pid)

    return processes_matching(image, command_line, table=lambda: TABLE, read=read), asked


def test_a_process_is_found_by_its_image_and_its_command_line_in_any_case():
    found, _asked = _matching({10: "pythonw.exe -m Satellite --x 1", 12: "pythonw.exe -m other"},
                              image=r"^pythonw?\.exe$", command_line=r"-m\s+satellite")

    assert found == [Running(pid=10, image="pythonw.exe", command_line="pythonw.exe -m Satellite --x 1")]


def test_only_a_process_whose_image_matches_is_asked_for_its_command_line():
    _found, asked = _matching({}, image=r"^notepad\.exe$", command_line="")

    assert asked == [11]


def test_a_process_windows_will_not_describe_is_not_found():
    found, _asked = _matching({10: None, 12: None}, image=r"^pythonw?\.exe$", command_line="")

    assert found == []


def test_a_sweep_starts_no_process_of_its_own():
    with patch("subprocess.Popen", side_effect=AssertionError("a sweep started a process")):
        processes_matching(r"^python\.exe$", "")


def test_it_finds_a_real_process_by_what_it_was_started_with():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "swept-for-by-a-test"])
    try:
        found = processes_matching(r"^python\.exe$", r"swept-for-by-a-test")
    finally:
        child.kill()
        child.wait()

    assert child.pid in {each.pid for each in found}
