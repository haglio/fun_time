"""Unit tests: an integration run never reaches the user's live Fun Time session.

A run isolates itself by state dir and nothing more, so what a session does on
its way up — restarting the shared OSR2 broker, competing for the GPU — still
lands on the user's live session.  The guard decides, before the hidden desktop
is even created, whether the run may proceed.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

from fun_time.live_session import LiveSessionClaim
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


def _session(*, state_dir: Path | None = None, children=None, omni_paused: bool) -> LiveSession:
    return LiveSession(
        state_dir=state_dir or Path("state"),
        children={"nau_pid": LIVE} if children is None else children,
        omni_paused=omni_paused,
    )


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


class TestFindLiveSessionAcrossCheckouts:
    def test_finds_the_session_a_different_checkout_is_running(self, tmp_path):
        """The regression that let a run kill the user's players.

        Agents work in worktrees, and a worktree's config resolves ``state_dir``
        to that worktree.  A guard that looked in its own state dir therefore
        looked at an empty directory and declared the machine idle every single
        time, while the user's session ran out of the primary checkout.  The
        claim is published somewhere both checkouts resolve identically, so the
        session is found wherever it is running from.
        """
        users_state_dir = tmp_path / "primary_checkout" / "state"
        _write_state(users_state_dir, children={"nau_pid": LIVE}, omni_paused=True)
        claim = LiveSessionClaim(pid=1, created_at=2, state_dir=users_state_dir)

        with patch.object(live_session_guard, "read_live_session", return_value=claim), \
             patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at):
            session = find_live_session()

        assert session is not None
        assert session.state_dir == users_state_dir
        assert session.children == {"nau_pid": LIVE}
        assert session.omni_paused is True


def _claiming(state_dir: Path):
    """Patch in a live session claiming *state_dir*, as its orchestrator would."""
    return patch.object(
        live_session_guard,
        "read_live_session",
        return_value=LiveSessionClaim(pid=1, created_at=2, state_dir=state_dir),
    )


class TestFindLiveSession:
    def test_no_session_when_nothing_claimed_the_machine(self, tmp_path):
        with patch.object(live_session_guard, "read_live_session", return_value=None):
            assert find_live_session() is None

    def test_a_session_still_starting_up_has_recorded_no_children(self, tmp_path):
        """The claim goes out before the orchestrator launches anything, so this
        is the shape of a session caught mid-startup — live, but with nothing yet
        that could be closed gracefully."""
        with _claiming(tmp_path):
            session = find_live_session()

        assert session is not None
        assert session.children == {}

    def test_children_whose_processes_have_exited_are_not_counted(self, tmp_path):
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=False)

        with _claiming(tmp_path), \
             patch.object(live_session_guard, "get_process_creation_time", return_value=None):
            assert find_live_session().children == {}

    def test_a_recycled_pid_is_not_counted_as_a_child(self, tmp_path):
        """A PID alone is not an identity — Windows hands freed PIDs straight back
        out, so a stale bridge_pids.ini must not read as a running child."""
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=False)

        with _claiming(tmp_path), \
             patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at + 1):
            assert find_live_session().children == {}

    def test_live_session_carries_the_omnipause_flag(self, tmp_path):
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=True)

        with _claiming(tmp_path), \
             patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at):
            session = find_live_session()

        assert session is not None
        assert session.omni_paused is True
        assert session.children == {"nau_pid": LIVE}

    def test_live_session_reports_playing_when_not_omnipaused(self, tmp_path):
        _write_state(tmp_path, children={"nau_pid": LIVE}, omni_paused=False)

        with _claiming(tmp_path), \
             patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at):
            session = find_live_session()

        assert session is not None
        assert session.omni_paused is False

    def test_a_child_that_was_never_launched_is_never_live(self, tmp_path):
        """pid 0 is the orchestrator's record of a child it never started, and no
        live process can match it — so it must not be probed or counted."""
        _write_state(tmp_path, children={"genau_pid": ChildProcess(pid=0, created_at=0)}, omni_paused=True)

        with _claiming(tmp_path), \
             patch.object(live_session_guard, "get_process_creation_time") as creation_time:
            assert find_live_session().children == {}
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
                ask=ask,
                close=closed.append,
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
        allowed, asked, closed, announced = self._allow(_session(omni_paused=False))

        assert allowed is False
        assert asked == []
        assert closed == []
        assert any("playing" in line for line in announced)

    def test_denies_a_session_that_has_not_recorded_its_children_yet(self):
        """Caught mid-startup: it has claimed the machine, so it is live, but
        there is nothing recorded that closing could shut down."""
        allowed, asked, closed, announced = self._allow(_session(children={}, omni_paused=True))

        assert allowed is False
        assert asked == []
        assert closed == []
        assert any("starting up" in line for line in announced)

    def test_closes_fun_time_and_runs_when_the_user_agrees(self):
        session = _session(omni_paused=True)

        allowed, asked, closed, _ = self._allow(session, answer=True)

        assert allowed is True
        assert asked == [True]
        assert closed == [session]

    def test_denies_and_leaves_fun_time_alone_when_the_user_declines(self):
        allowed, asked, closed, announced = self._allow(_session(omni_paused=True), answer=False)

        assert allowed is False
        assert asked == [True]
        assert closed == []
        assert any("declined" in line.lower() for line in announced)


class TestCloseLiveSession:
    def test_asks_the_sessions_own_ahk_to_exit_then_waits_for_it_to_die(self, tmp_path):
        """The bridge's own quit path: AHK exits, the orchestrator wakes and
        shuts its children down.  No taskkill needed when it works.

        The command must land in the session's own state dir — writing it into
        ours would drop it in a worktree directory no AHK is watching.
        """
        session = _session(state_dir=tmp_path, omni_paused=True)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=None), \
             patch.object(live_session_guard, "kill_recorded_child") as kill:
            close_live_session(session, sleep=lambda _s: None)

        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "exit"
        kill.assert_not_called()

    def test_taskkills_a_child_that_outlives_the_graceful_quit(self, tmp_path):
        session = _session(state_dir=tmp_path, omni_paused=True)

        with patch.object(live_session_guard, "get_process_creation_time", return_value=LIVE.created_at), \
             patch.object(live_session_guard, "kill_recorded_child") as kill:
            close_live_session(session, timeout=0.0, sleep=lambda _s: None)

        kill.assert_called_once_with(LIVE)
