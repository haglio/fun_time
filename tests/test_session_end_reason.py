"""A session that died on its own reads differently from one the user quit.

Every line the orchestrator logs on the way down is the same either way: the
hotkey script exits, the closing screen goes up, the children are killed, the
return code is 0.  So when a session vanished there was nothing in the log to
confirm or deny it — the only evidence was the user noticing it was gone, and
the only thing to go on afterwards was a normal-looking shutdown.

Whatever asks now leaves a one-line marker; the orchestrator reads it, says
what it found, and removes it so the next session finds only its own.  The
phrase is the asker's own, because the one the hotkey script can write for
itself -- "an exit on the AHK command channel" -- is what a spoken "quit", a
crossing to the headset and the dashboard window's close box all looked like.
"""
from __future__ import annotations

import ast
from pathlib import Path

from fun_time.config import load_config
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    LaunchManifest,
    write_windows_bridge_manifest,
)
from fun_time.session_end import SESSION_END_MARKER
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
    """Its own error paths exit non-zero (a missing manifest value, say), which
    is neither an asked-for end nor a silent disappearance."""
    described = _describe_session_end(tmp_path, 2)

    assert "FAILED" in described


def test_the_marker_is_removed_so_the_next_session_finds_only_its_own(tmp_path):
    marker = tmp_path / SESSION_END_MARKER
    marker.write_text("the quit chord (Ctrl+Alt+Q)", encoding="utf-8")

    _describe_session_end(tmp_path, 0)

    assert not marker.exists()
    assert "UNEXPECTED" in _describe_session_end(tmp_path, 0)


def test_an_unreadable_marker_is_read_as_no_marker(tmp_path):
    """A directory where the file should be, a permission fault mid-read: the
    session still comes down, and the line still says something true."""
    (tmp_path / SESSION_END_MARKER).mkdir()

    assert "UNEXPECTED" in _describe_session_end(tmp_path, 0)


def test_the_hotkey_script_stamps_the_marker_on_every_deliberate_end():
    """The other half of the contract, and the half a Python test cannot run:
    every asked-for END stamps the marker, so one that does NOT is exactly the
    unexpected one.

    An end the session asked for -- the quit chord, or end_session on the
    mailbox -- leaves the script running over the closing cover, where Esc can
    still call it off; the marker is what tells the orchestrator it ended.  Only
    the mailbox's exit marks and exits at once.

    Mid-startup the quit chord cannot end anything -- the orchestrator is still
    building a session that has to come down first -- so it asks startup to
    unwind and says "quit" in the cancel flag instead.  Not through this marker:
    crossing over leaves one of those too, so reading it made every Esc look
    like a quit.
    """
    assert SESSION_END_MARKER in _hotkey_script()
    chord = function_source("EndSession")
    assert chord.index('MarkSessionEnd("the quit chord (Ctrl+Alt+Q)")') < chord.index(
        "EndTheSession()")
    ending = function_source("EndTheSession")
    assert "MarkSessionEnd(" not in ending and "ExitApp" not in ending
    mailbox = function_source("ProcessAhkCommand")
    asked = mailbox[mailbox.index('(action = "end_session")'):mailbox.index('(action = "exit")')]
    assert asked.index("KeepOrMarkSessionEnd(") < asked.index("EndTheSession()")
    exit_branch = mailbox[mailbox.index('(action = "exit")'):]
    assert "KeepOrMarkSessionEnd(" in exit_branch and "ExitApp()" in exit_branch


def test_the_command_channel_keeps_the_phrase_whatever_asked_left():
    """The channel is how EVERY end but the quit chord arrives, and all it can
    say for itself is that it was asked.  A spoken "quit", a crossing to the
    headset and the dashboard window's close box each stamp their own phrase
    before asking for the end; overwriting it here would put them all back
    under the one line that cannot tell them apart."""
    body = _hotkey_script()

    assert 'KeepOrMarkSessionEnd("an end asked on the AHK command channel")' in body
    assert 'KeepOrMarkSessionEnd("an exit on the AHK command channel")' in body
    assert "if (FileExist(STATE_DIR" in body
    # The chord is the one caller that IS the asker, so it still writes over
    # whatever it finds.
    assert 'MarkSessionEnd("the quit chord (Ctrl+Alt+Q)")' in body


def _hotkey_script() -> str:
    script = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"
    return script.read_text(encoding="utf-8")


def test_everything_that_asks_for_the_end_says_what_asked_first():
    """The rule the phrases depend on, over the whole package: writing
    "end_session" to the hotkey script's mailbox ends the session, and every
    place that does it stamps the marker first.  One that does not puts its end
    back under the command channel's own line, which cannot tell a spoken
    "quit" from a crossing to the headset from a window someone closed.  The
    exit is left to stopping a script whose session never began or is over."""
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
    """A mis-heard word and a deliberate press both arrive here as "quit", and
    used to leave the same phrase -- so the question the log could not answer
    was the obvious one: did voice control end that session?"""
    make_runner(tmp_path)._handle_command("quit", spoken_at=123.0)
    assert "spoken" in (tmp_path / SESSION_END_MARKER).read_text(encoding="utf-8")

    make_runner(tmp_path)._handle_command("quit")
    assert "pressed" in (tmp_path / SESSION_END_MARKER).read_text(encoding="utf-8")


def test_a_crossing_to_the_headset_names_itself(tmp_path):
    make_runner(tmp_path)._handle_command("enter_vr")

    reason = (tmp_path / SESSION_END_MARKER).read_text(encoding="utf-8")
    assert "crossing" in reason and "FunTimeVR" in reason


def test_a_marker_from_a_session_that_was_cut_off_is_not_worn_by_the_next(
        cfg_factory, tmp_path):
    """A session killed from the task list, or cut off by the machine
    restarting under it, never reaches the read that removes its marker."""
    commands = LaunchManifest.read(write_windows_bridge_manifest(
        load_config(cfg_factory()), tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)).commands
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / SESSION_END_MARKER).write_text("the quit chord (Ctrl+Alt+Q)", encoding="utf-8")

    clear_last_sessions_leftovers(state_dir, commands, pids_file=state_dir / "bridge_pids.ini",
                                  ahk_cmd_file=state_dir / "ahk_cmd.txt")

    assert "UNEXPECTED" in _describe_session_end(state_dir, 0)
