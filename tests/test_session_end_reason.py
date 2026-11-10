from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    LaunchManifest,
    write_windows_bridge_manifest,
)
from fun_time.session_end import SESSION_END_MARKER, mark_session_end
from fun_time.windows_bridge_orchestrator import (
    _describe_session_end,
    clear_last_sessions_leftovers,
)
from tests.ahk_script import function_source
from tests.test_windows_bridge_dispatch_loop import make_runner


def test_an_end_the_user_asked_for_names_what_asked(tmp_path):
    (tmp_path / SESSION_END_MARKER).write_text(
        "the quit chord (Ctrl+Alt+Q)", encoding="utf-8")

    described = _describe_session_end(tmp_path, 0)

    assert "quit chord" in described
    assert "UNEXPECTED" not in described


def test_a_session_that_ended_on_its_own_says_so_loudly(tmp_path):
    described = _describe_session_end(tmp_path, 0)

    assert "UNEXPECTED" in described


def test_a_hotkey_script_that_failed_is_told_apart_from_both(tmp_path):
    described = _describe_session_end(tmp_path, 2)

    assert "FAILED" in described


def test_the_marker_is_removed_so_the_next_session_finds_only_its_own(tmp_path):
    marker = tmp_path / SESSION_END_MARKER
    marker.write_text("the quit chord (Ctrl+Alt+Q)", encoding="utf-8")

    _describe_session_end(tmp_path, 0)

    assert not marker.exists()
    assert "UNEXPECTED" in _describe_session_end(tmp_path, 0)


def test_an_unreadable_marker_is_read_as_no_marker(tmp_path):
    (tmp_path / SESSION_END_MARKER).mkdir()

    assert "UNEXPECTED" in _describe_session_end(tmp_path, 0)


def test_the_hotkey_script_stamps_the_marker_on_every_deliberate_end():
    assert SESSION_END_MARKER in _hotkey_script()
    chord = function_source("EndSession")
    assert chord.index('MarkSessionEnd("the quit chord (Ctrl+Alt+Q)")') < chord.index(
        "EndTheSession()")
    ending = function_source("EndTheSession")
    assert "MarkSessionEnd(" not in ending
    assert "ExitApp" not in ending
    mailbox = function_source("ProcessAhkCommand")
    asked = mailbox[mailbox.index('(action = "end_session")'):mailbox.index('(action = "exit")')]
    assert asked.index("KeepOrMarkSessionEnd(") < asked.index("EndTheSession()")
    exit_branch = mailbox[mailbox.index('(action = "exit")'):]
    assert "KeepOrMarkSessionEnd(" in exit_branch
    assert "ExitApp()" in exit_branch


def test_the_command_channel_keeps_the_phrase_whatever_asked_left():
    body = _hotkey_script()

    assert 'KeepOrMarkSessionEnd("an end asked on the AHK command channel")' in body
    assert 'KeepOrMarkSessionEnd("an exit on the AHK command channel")' in body
    assert "if (FileExist(STATE_DIR" in body


def test_the_quit_chord_being_the_asker_writes_over_any_marker():
    chord = function_source("EndSession")

    assert 'MarkSessionEnd("the quit chord (Ctrl+Alt+Q)")' in chord
    assert "KeepOrMarkSessionEnd(" not in chord


def _hotkey_script() -> str:
    script = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"
    return script.read_text(encoding="utf-8")


@pytest.mark.repo_check
def test_everything_that_asks_for_the_end_says_what_asked_first():
    package = Path(__file__).resolve().parents[1] / "fun_time"
    unmarked = []
    exits = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for holder in ast.walk(tree):
            if not isinstance(holder, ast.FunctionDef):
                continue
            marks = [
                node.lineno for node in ast.walk(holder)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "mark_session_end"
            ]
            for node in ast.walk(holder):
                if _writes_to_the_mailbox(node, "exit"):
                    exits.append(holder.name)
                if not _writes_to_the_mailbox(node, "end_session"):
                    continue
                if not any(mark < node.lineno for mark in marks):
                    unmarked.append(f"{path.name}:{node.lineno} in {holder.name}")

    assert unmarked == [], (
        "these ask for the end without saying what asked: " + ", ".join(unmarked))
    assert exits == ["stop_hotkey_script"]


def _writes_to_the_mailbox(node, word: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_text"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == word
    )


def test_the_quit_command_says_whether_it_was_spoken(tmp_path):
    make_runner(tmp_path)._handle_command("quit", spoken_at=123.0)
    assert "spoken" in (tmp_path / SESSION_END_MARKER).read_text(encoding="utf-8")

    make_runner(tmp_path)._handle_command("quit")
    assert "pressed" in (tmp_path / SESSION_END_MARKER).read_text(encoding="utf-8")


def test_a_crossing_to_the_headset_names_itself(tmp_path):
    make_runner(tmp_path)._handle_command("enter_vr")

    reason = (tmp_path / SESSION_END_MARKER).read_text(encoding="utf-8")
    assert "crossing" in reason
    assert "Fun Time VR" in reason


def test_a_marker_from_a_session_that_was_cut_off_is_not_worn_by_the_next(
        cfg_factory, tmp_path):
    commands = LaunchManifest.read(write_windows_bridge_manifest(
        load_config(cfg_factory()), tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)).commands
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / SESSION_END_MARKER).write_text("the quit chord (Ctrl+Alt+Q)", encoding="utf-8")

    clear_last_sessions_leftovers(state_dir, commands, pids_file=state_dir / "bridge_pids.ini",
                                  ahk_cmd_file=state_dir / "ahk_cmd.txt")

    assert "UNEXPECTED" in _describe_session_end(state_dir, 0)


def test_a_session_comes_down_even_when_it_cannot_leave_a_note(tmp_path):
    blocked = tmp_path / "state"
    blocked.write_text("a file where the state folder should be", encoding="utf-8")

    mark_session_end(blocked, "the user quit")
    assert blocked.is_file()
