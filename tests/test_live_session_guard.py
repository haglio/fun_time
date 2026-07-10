"""Unit tests: an integration run never reaches the user's live Fun Time session.

The integration config keeps the production VLC HTTP ports, so a run that starts
while Fun Time is open drives the user's own satellite VLCs.  The guard decides,
before the hidden desktop is even created, whether the run may proceed.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

from fun_time.windows_bridge_orchestrator import ChildProcess
from tests.integration import live_session_guard
from tests.integration.live_session_guard import (
    LiveSession,
    allow_integration_run,
    close_live_session,
    find_live_session,
    read_recorded_children,
)


LIVE = ChildProcess(pid=4321, created_at=999)


def _write_state(state_dir: Path, *, children: dict[str, ChildProcess], omni_paused: bool) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    pids = configparser.ConfigParser()
    pids.optionxform = str
    pids["pids"] = {k: str(c.pid) for k, c in children.items()}
    pids["created_at"] = {k: str(c.created_at) for k, c in children.items()}
    with (state_dir / "bridge_pids.ini").open("w", encoding="utf-8") as fh:
        pids.write(fh)
    shared = configparser.ConfigParser()
    shared["state"] = {"omni_paused": "1" if omni_paused else "0"}
    with (state_dir / "shared_bridge_state.ini").open("w", encoding="utf-8") as fh:
        shared.write(fh)


class TestFindLiveSession:
    def test_no_session_when_no_pids_file(self, tmp_path):
        assert find_live_session(tmp_path) is None

    def test_no_session_when_every_recorded_child_has_exited(self, tmp_path):
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=False)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=None):
            assert find_live_session(tmp_path) is None

    def test_no_session_when_the_pid_was_recycled(self, tmp_path):
        """A PID alone is not an identity — Windows hands freed PIDs straight back
        out, so a stale bridge_pids.ini must not read as a live session."""
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=False)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at + 1):
            assert find_live_session(tmp_path) is None

    def test_live_session_carries_the_omnipause_flag(self, tmp_path):
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=True)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at):
            session = find_live_session(tmp_path)

        assert session is not None
        assert session.omni_paused is True
        assert session.children == {"nau_pid": LIVE}

    def test_live_session_reports_playing_when_not_omnipaused(self, tmp_path):
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=False)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at):
            session = find_live_session(tmp_path)

        assert session is not None
        assert session.omni_paused is False

    def test_a_child_that_was_never_launched_is_never_live(self, tmp_path):
        """pid 0 is the orchestrator's record of a child it never started, and no
        live process can match it — so it must not be probed or counted."""
        _write_state(tmp_path, children={"genau_pid": ChildProcess(pid=0, created_at=0)}, omni_paused=True)

        with patch.object(live_session_guard, "get_process_creation_time") as creation_time:
            assert find_live_session(tmp_path) is None
        creation_time.assert_not_called()


class TestReadRecordedChildren:
    def test_parses_every_recorded_role(self, tmp_path):
        never_launched = ChildProcess(pid=0, created_at=0)
        _write_state(tmp_path, children={"nau_pid": LIVE, "genau_pid": never_launched}, omni_paused=False)

        assert read_recorded_children(tmp_path) == {"nau_pid": LIVE, "genau_pid": never_launched}

    def test_empty_when_no_session_ever_wrote_the_file(self, tmp_path):
        assert read_recorded_children(tmp_path) == {}


class TestAllowIntegrationRun:
    def _allow(self, session, *, answer=True):
        asked: list[bool] = []
        closed: list[LiveSession] = []
        announced: list[str] = []

        def ask(_session):
            asked.append(True)
            return answer

        with patch.object(live_session_guard, "find_live_session", return_value=session):
            allowed = allow_integration_run(
                Path("state"),
                ask=ask,
                close=lambda _state_dir, s: closed.append(s),
                announce=announced.append,
            )
        return allowed, asked, closed, announced

    def test_runs_when_fun_time_is_not_open(self):
        allowed, asked, closed, _ = self._allow(None)

        assert allowed is True
        assert asked == []
        assert closed == []

    def test_denies_without_asking_while_fun_time_is_playing(self):
        """The user is watching — never interrupt, never prompt."""
        allowed, asked, closed, announced = self._allow(
            LiveSession(children={"nau_pid": LIVE}, omni_paused=False)
        )

        assert allowed is False
        assert asked == []
        assert closed == []
        assert any("playing" in line for line in announced)

    def test_closes_fun_time_and_runs_when_the_user_agrees(self):
        session = LiveSession(children={"nau_pid": LIVE}, omni_paused=True)

        allowed, asked, closed, _ = self._allow(session, answer=True)

        assert allowed is True
        assert asked == [True]
        assert closed == [session]

    def test_denies_and_leaves_fun_time_alone_when_the_user_declines(self):
        session = LiveSession(children={"nau_pid": LIVE}, omni_paused=True)

        allowed, asked, closed, announced = self._allow(session, answer=False)

        assert allowed is False
        assert asked == [True]
        assert closed == []
        assert any("declined" in line.lower() for line in announced)


class TestCloseLiveSession:
    def test_asks_ahk_to_exit_then_waits_for_the_children_to_die(self, tmp_path):
        """The bridge's own quit path: AHK exits, the orchestrator wakes and
        shuts its children down.  No taskkill needed when it works."""
        session = LiveSession(children={"nau_pid": LIVE}, omni_paused=True)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=None), \
             patch.object(live_session_guard, "kill_recorded_child") as kill:
            close_live_session(tmp_path, session, sleep=lambda _s: None)

        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "exit"
        kill.assert_not_called()

    def test_taskkills_a_child_that_outlives_the_graceful_quit(self, tmp_path):
        session = LiveSession(children={"nau_pid": LIVE}, omni_paused=True)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at), \
             patch.object(live_session_guard, "kill_recorded_child") as kill:
            close_live_session(tmp_path, session, timeout=0.0, sleep=lambda _s: None)

        kill.assert_called_once_with(LIVE)
