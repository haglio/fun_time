"""The crossing between the two sessions: "enter VR", "exit VR".

The design is in ``docs/entering-vr.md``; what is held here is every rule of it
that a reader could otherwise only take on trust.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time import session_handoff
from fun_time.config import load_config
from fun_time.orchestrator import STARTUP_MARKER_NAME
from fun_time.session_handoff import (
    DESKTOP,
    STARTUP_TIMEOUT_S,
    VR,
    clear_handoff_request,
    crossing_progress_path,
    drop_crossing_cover,
    hand_over_if_asked,
    handoff_request_path,
    last_lines_of,
    launch_crossing_cover,
    pending_handoff,
    raise_crossing_cover,
    report_a_failed_crossing,
    request_handoff,
    run,
    start_the_session,
    take_handoff_request,
    this_session,
    wait_for_the_session_to_come_up,
    wait_for_the_session_to_let_go,
)
from fun_time_vr.orchestrator import VR_STARTUP_MARKER_NAME
from tests.test_launch_smoke import LAUNCHED


@pytest.fixture
def config(cfg_factory):
    return load_config(cfg_factory())


class TestTheRequestFile:
    """One ending, described once."""

    def test_a_request_is_taken_off_the_disk_as_it_is_read(self, tmp_path: Path):
        request_handoff(tmp_path, VR)
        assert take_handoff_request(tmp_path) is VR
        assert not handoff_request_path(tmp_path).exists()
        assert take_handoff_request(tmp_path) is None

    def test_a_teardown_may_read_the_request_without_spending_it(self, tmp_path: Path):
        """The teardown has to know the room is changing shape rather than
        closing; the orchestrator still has to find the request afterwards."""
        request_handoff(tmp_path, VR)

        assert pending_handoff(tmp_path) is VR
        assert pending_handoff(tmp_path) is VR
        assert take_handoff_request(tmp_path) is VR

    def test_a_session_nobody_asked_to_cross_reads_as_none(self, tmp_path: Path):
        assert take_handoff_request(tmp_path) is None

    def test_an_unrecognized_request_reads_as_none_and_is_still_cleared(self, tmp_path: Path):
        handoff_request_path(tmp_path).write_text("sideways\n", encoding="utf-8")
        assert take_handoff_request(tmp_path) is None
        assert not handoff_request_path(tmp_path).exists()

    def test_clearing_is_what_startup_does_to_a_request_a_crash_left(self, tmp_path: Path):
        request_handoff(tmp_path, DESKTOP)
        clear_handoff_request(tmp_path)
        clear_handoff_request(tmp_path)  # and again, on a session that crossed cleanly
        assert not handoff_request_path(tmp_path).exists()

    def test_a_request_names_the_target_it_will_be_read_back_as(self, tmp_path: Path):
        for target in (DESKTOP, VR):
            request_handoff(tmp_path, target)
            assert handoff_request_path(tmp_path).read_text(encoding="utf-8").strip() == target.key


class TestTheCoverThatSpansTheCrossing:
    """Raised by the session leaving, taken down by the one arriving -- the one
    thing a closing screen cannot do, and why the monitors went bare between
    them (docs/entering-vr.md)."""

    @pytest.mark.parametrize(
        ("target", "says"), [(VR, "Entering VR"), (DESKTOP, "Returning to Fun Time")],
    )
    def test_it_names_the_session_being_waited_for(self, tmp_path: Path, target, says):
        raise_crossing_cover(tmp_path, target)

        assert says in crossing_progress_path(tmp_path).read_text(encoding="utf-8")

    def test_dropping_it_is_what_the_cover_reads_as_finished(self, tmp_path: Path):
        raise_crossing_cover(tmp_path, VR)

        drop_crossing_cover(tmp_path)

        assert crossing_progress_path(tmp_path).read_text(encoding="utf-8").strip() == "DONE"

    def test_an_ordinary_startup_drops_nothing_and_leaves_nothing(self, tmp_path: Path):
        """Every desktop startup asks; a DONE written where no cover was raised
        would leave a file for the next session to read as one."""
        drop_crossing_cover(tmp_path)

        assert not crossing_progress_path(tmp_path).exists()

    def test_it_is_launched_as_the_transition_screen_over_that_file(self, tmp_path: Path):
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            launch_crossing_cover(tmp_path, VR)

        command = popen.call_args.args[0]
        assert command[1:] == [
            "-m", "fun_time.transition_screen", str(crossing_progress_path(tmp_path)),
        ]

    def test_a_crossing_that_never_happened_uncovers_the_monitors(self, config):
        """The cover is waiting for a session that is not coming, and its own
        staleness timeout is minutes long."""
        raise_crossing_cover(config.paths.state_dir, VR)
        with patch.object(session_handoff, "wait_for_the_session_to_let_go",
                          return_value=False), \
             patch.object(session_handoff, "report_a_failed_crossing"):
            assert run(VR, config) == 1

        assert crossing_progress_path(
            config.paths.state_dir
        ).read_text(encoding="utf-8").strip() == "DONE"


class TestWhichSessionIsWhich:
    """Where the main player lives is the whole difference between the two."""

    def test_a_session_hosting_its_main_player_in_vr_is_the_vr_one(self):
        assert this_session(vr_main_player=True) is VR
        assert this_session(vr_main_player=False) is DESKTOP

    def test_each_target_names_the_marker_its_orchestrator_really_writes(self):
        """The relay watches the file the incoming session drops, so a marker
        renamed on one side and not the other would read every launch as wedged."""
        assert DESKTOP.ready_marker == STARTUP_MARKER_NAME
        assert VR.ready_marker == VR_STARTUP_MARKER_NAME

    def test_each_target_names_a_module_a_launcher_actually_starts(self):
        """Read out of the ``.vbs`` launchers, so the relay cannot start a
        session by a module name nothing else in the repo launches."""
        launched = {
            module for launcher, module in LAUNCHED if launcher.endswith(".vbs")
        }
        assert {DESKTOP.module, VR.module} <= launched


class TestWaitingForTheOutgoingSession:
    """The mutex frees when the outgoing process is gone, not when it is asked."""

    def test_it_returns_as_soon_as_nobody_holds_the_mutex(self):
        holds = iter([True, True, False])
        with patch.object(session_handoff, "is_mutex_held", lambda _name: next(holds)):
            assert wait_for_the_session_to_let_go("m", poll_s=0) is True

    def test_it_gives_up_on_a_session_that_wedged_holding_it(self):
        with patch.object(session_handoff, "is_mutex_held", return_value=True):
            assert wait_for_the_session_to_let_go("m", timeout_s=0, poll_s=0) is False


class TestStartingTheIncomingSession:
    def test_the_stale_marker_goes_before_the_launch(self, tmp_path: Path):
        """It is written once per launch and read as this launch's: left there,
        the session that just ended would vouch for one not started yet."""
        (tmp_path / VR.ready_marker).write_text("ready\n", encoding="utf-8")
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            popen.side_effect = lambda *a, **k: (
                pytest.fail("the marker was still there at launch")
                if (tmp_path / VR.ready_marker).exists() else MagicMock()
            )
            start_the_session(
                VR, python_exe="py.exe", project_dir=tmp_path,
                state_dir=tmp_path, config_path=tmp_path / "c.json",
            )

    def test_it_runs_the_module_on_this_session_s_config_from_its_own_checkout(
        self, tmp_path: Path
    ):
        """The working directory is what swaps the code, so a branch session
        crosses into the branch rather than into the primary.  The interpreter
        is the named copy where there is one, and the plain one where there is
        not — as here, where nothing can be copied from a fake."""
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            start_the_session(
                VR, python_exe="py.exe", project_dir=tmp_path / "worktree",
                state_dir=tmp_path, config_path=tmp_path / "branch.json",
            )
        command = popen.call_args.args[0]
        assert command == [
            "py.exe", "-m", "fun_time_vr.orchestrator",
            "--config", str(tmp_path / "branch.json"),
        ]
        assert popen.call_args.kwargs["cwd"] == str(tmp_path / "worktree")


class TestWatchingItComeUp:
    def _session(self, *, returncode=None):
        session = MagicMock()
        session.poll.return_value = returncode
        session.returncode = returncode
        return session

    def test_the_marker_is_the_answer(self, tmp_path: Path):
        (tmp_path / VR.ready_marker).write_text("ready\n", encoding="utf-8")
        assert wait_for_the_session_to_come_up(
            VR, self._session(), state_dir=tmp_path, poll_s=0
        ) == ""

    def test_a_session_that_died_is_reported_at_once_with_its_exit_code(self, tmp_path: Path):
        reason = wait_for_the_session_to_come_up(
            VR, self._session(returncode=3), state_dir=tmp_path,
            timeout_s=STARTUP_TIMEOUT_S, poll_s=0,
        )
        assert "stopped during startup" in reason and "3" in reason

    def test_a_session_that_never_reports_is_a_wedge_rather_than_a_crash(self, tmp_path: Path):
        reason = wait_for_the_session_to_come_up(
            VR, self._session(), state_dir=tmp_path, timeout_s=0, poll_s=0
        )
        assert "did not finish starting" in reason


class TestReportingAFailedCrossing:
    """By now there is nothing on screen at all, so the dialog is the only word."""

    def test_it_carries_the_tail_of_the_launcher_log(self, tmp_path: Path):
        log = tmp_path / "vr_launcher.log"
        log.write_text("\n".join(f"line {n}" for n in range(40)) + "\n\n\n", encoding="utf-8")
        with patch("shared_ui.alert.show_alert") as alert:
            report_a_failed_crossing("FunTimeVR did not start.", log)
        message = alert.call_args.args[1]
        assert "FunTimeVR did not start." in message
        assert "line 39" in message and "line 24" not in message

    def test_a_log_that_cannot_be_read_still_gets_a_dialog(self, tmp_path: Path):
        with patch("shared_ui.alert.show_alert") as alert:
            report_a_failed_crossing("Nothing came up.", tmp_path / "never_written.log")
        assert "Nothing came up." in alert.call_args.args[1]

    def test_the_tail_stops_at_the_last_line_with_anything_on_it(self, tmp_path: Path):
        log = tmp_path / "log.txt"
        log.write_text("first\nlast\n\n   \n", encoding="utf-8")
        assert last_lines_of(log) == "first\nlast"


class TestHandingOver:
    def test_a_session_that_merely_quit_starts_no_relay(self, config):
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            assert hand_over_if_asked(config, MagicMock()) is None
        popen.assert_not_called()

    def test_a_crossing_spawns_the_relay_detached_on_this_session_s_config(self, config):
        request_handoff(config.paths.state_dir, VR)
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            assert hand_over_if_asked(config, MagicMock()) is VR
        command = popen.call_args.args[0]
        assert command == [
            str(config.paths.python_exe), "-m", "fun_time.session_handoff",
            "--target", "vr", "--config", str(config.config_path),
        ]
        assert popen.call_args.kwargs["cwd"] == str(config.project_dir)
        flags = popen.call_args.kwargs["creationflags"]
        assert flags & subprocess.DETACHED_PROCESS

    def test_the_request_is_spent_so_the_next_session_does_not_cross_again(self, config):
        request_handoff(config.paths.state_dir, DESKTOP)
        with patch.object(session_handoff.subprocess, "Popen"):
            hand_over_if_asked(config, MagicMock())
            assert hand_over_if_asked(config, MagicMock()) is None


class TestTheRelayEndToEnd:
    def _patched(self, *, let_go=True, reason=""):
        return (
            patch.object(session_handoff, "wait_for_the_session_to_let_go", return_value=let_go),
            patch.object(session_handoff, "start_the_session", return_value=MagicMock(pid=7)),
            patch.object(session_handoff, "wait_for_the_session_to_come_up", return_value=reason),
            patch.object(session_handoff, "report_a_failed_crossing"),
        )

    def test_a_good_crossing_starts_the_session_and_says_nothing(self, config):
        let_go, start, come_up, report = self._patched()
        with let_go, start, come_up, report as reported:
            assert run(VR, config) == 0
        reported.assert_not_called()

    def test_an_outgoing_session_that_never_let_go_is_reported(self, config):
        let_go, start, come_up, report = self._patched(let_go=False)
        with let_go, start as started, come_up, report as reported:
            assert run(VR, config) == 1
        started.assert_not_called()
        assert "never finished shutting down" in reported.call_args.args[0]

    def test_an_incoming_session_that_never_came_up_is_reported(self, config):
        let_go, start, come_up, report = self._patched(reason="FunTimeVR stopped.")
        with let_go, start, come_up, report as reported:
            assert run(VR, config) == 1
        assert reported.call_args.args[0] == "FunTimeVR stopped."
        assert reported.call_args.args[1] == config.paths.state_dir / VR.launcher_log
