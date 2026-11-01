"""What a Windows .lnk says it launches."""
from __future__ import annotations

import logging

from app_support import win32

from fun_time.shortcuts import Shortcut, read_shortcuts, resolve_shortcut

AN_EXE = r"C:\Windows\System32\notepad.exe"


def _made(path, **fields):
    win32.write_shortcut(str(path), **fields)
    return str(path)


def test_a_shortcut_reads_back_as_what_it_runs_from_where_and_with_what(tmp_path):
    made = _made(tmp_path / "s.lnk", target=AN_EXE, working_directory=r"C:\Windows",
                 arguments='--profile-directory="Profile 1"')

    assert resolve_shortcut(made) == Shortcut(
        AN_EXE, r"C:\Windows", '--profile-directory="Profile 1"')


def test_a_bare_target_has_no_folder_or_arguments(tmp_path):
    made = _made(tmp_path / "s.lnk", target=AN_EXE)

    assert resolve_shortcut(made) == Shortcut(AN_EXE)


def test_a_shortcut_that_cannot_be_read_says_so_and_launches_nothing(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="windows_bridge"):
        resolved = resolve_shortcut(str(tmp_path / "gone.lnk"))

    assert resolved == Shortcut()
    assert "Could not read the shortcut" in caplog.text


def test_a_folder_is_read_by_the_pattern_alone(tmp_path):
    _made(tmp_path / "Verify a.lnk", target=AN_EXE)
    _made(tmp_path / "other.lnk", target=AN_EXE)

    assert list(read_shortcuts(tmp_path, pattern="Verify*.lnk")) == [tmp_path / "Verify a.lnk"]
