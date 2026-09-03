"""A session that died on its own reads differently from one the user quit.

Every line the orchestrator logs on the way down is the same either way: the
hotkey script exits, the closing screen goes up, the children are killed, the
return code is 0.  So when a session vanished there was nothing in the log to
confirm or deny it — the only evidence was the user noticing it was gone, and
the only thing to go on afterwards was a normal-looking shutdown.

The quit chord (and an exit asked for on the AHK command channel) now leaves a
one-line marker; the orchestrator reads it, says what it found, and removes it
so the next session finds only its own.
"""
from __future__ import annotations

from fun_time.windows_bridge_orchestrator import (
    SESSION_END_MARKER,
    _describe_session_end,
)


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


def test_the_hotkey_script_stamps_the_marker_on_every_deliberate_exit():
    """The other half of the contract, and the half a Python test cannot run:
    both of the script's asked-for exits go through MarkSessionEnd, so an exit
    that does NOT is exactly the unexpected one."""
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"
    body = script.read_text(encoding="utf-8")

    assert body.count("MarkSessionEnd(") == 3  # the definition, and two callers
    assert SESSION_END_MARKER in body
    # Every ExitApp that ends a live session is preceded by the marker.  The
    # two that are not are the startup failures, which never reach one.
    lines = body.splitlines()
    callers = [
        i for i, line in enumerate(lines)
        if "MarkSessionEnd(" in line and "MarkSessionEnd(reason)" not in line
    ]
    assert len(callers) == 2  # the quit chord, and the command channel's exit
    for index in callers:
        assert any("ExitApp" in line for line in lines[index:index + 3])
