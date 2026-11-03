from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from shared_ui.testing import alert_shown

from fun_time import session_handoff
from fun_time.config import load_config
from fun_time.orchestrator import STARTUP_MARKER_NAME
from fun_time.overlay_progress import CANCEL_FILENAME, CANCELING, parse_progress
from fun_time.session_handoff import (
    DESKTOP,
    STARTUP_TIMEOUT_S,
    VR,
    a_session_is_playing,
    clear_handoff_request,
    crossing_progress_path,
    drop_transition_screen,
    forget_the_kept_origenerator,
    hand_over_if_asked,
    handoff_request_path,
    headset_hold_asked,
    headset_hold_stops_the_runtime,
    headset_is_held,
    hold_the_headset,
    keep_the_origenerator,
    kept_origenerator,
    last_lines_of,
    launch_transition_screen,
    pending_handoff,
    raise_transition_screen,
    release_the_headset,
    report_a_failed_crossing,
    report_the_headset_held,
    request_handoff,
    run,
    start_the_session,
    take_handoff_request,
    this_session,
    wait_for_the_session_to_come_up,
    wait_for_the_session_to_let_go,
)
from fun_time.single_instance import let_the_session_go
from fun_time_vr.orchestrator import VR_STARTUP_MARKER_NAME
from tests.test_launch_smoke import LAUNCHED


@pytest.fixture
def config(cfg_factory):
    return load_config(cfg_factory())


class TestTheRequestFile:
    def test_a_request_is_taken_off_the_disk_as_it_is_read(self, tmp_path: Path):
        request_handoff(tmp_path, VR)
        assert take_handoff_request(tmp_path).target is VR
        assert not handoff_request_path(tmp_path).exists()
        assert take_handoff_request(tmp_path) is None

    def test_a_teardown_may_read_the_request_without_spending_it(self, tmp_path: Path):
        request_handoff(tmp_path, VR)

        assert pending_handoff(tmp_path) is VR
        assert pending_handoff(tmp_path) is VR
        assert take_handoff_request(tmp_path).target is VR

    def test_a_session_nobody_asked_to_cross_reads_as_none(self, tmp_path: Path):
        assert take_handoff_request(tmp_path) is None

    def test_an_unrecognized_request_reads_as_none_and_is_still_cleared(self, tmp_path: Path):
        handoff_request_path(tmp_path).write_text("sideways\n", encoding="utf-8")
        assert take_handoff_request(tmp_path) is None
        assert not handoff_request_path(tmp_path).exists()

    def test_clearing_is_what_startup_does_to_a_request_a_crash_left(self, tmp_path: Path):
        request_handoff(tmp_path, DESKTOP)
        clear_handoff_request(tmp_path)
        clear_handoff_request(tmp_path)
        assert not handoff_request_path(tmp_path).exists()

    def test_a_way_back_is_asked_for_with_no_esc_of_its_own(self, tmp_path: Path):
        request_handoff(tmp_path, DESKTOP, cancelable=False)

        taken = take_handoff_request(tmp_path)

        assert taken.target is DESKTOP
        assert taken.cancelable is False

    def test_a_request_names_the_target_it_will_be_read_back_as(self, tmp_path: Path):
        for target in (DESKTOP, VR):
            request_handoff(tmp_path, target)
            assert handoff_request_path(tmp_path).read_text(encoding="utf-8").strip() == target.key


class TestTheLoadingScreenThatSpansTheCrossing:
    @pytest.mark.parametrize(
        ("target", "says"), [(VR, "Entering VR"), (DESKTOP, "Returning to Fun Time")],
    )
    def test_it_names_the_session_being_waited_for(self, tmp_path: Path, target, says):
        raise_transition_screen(tmp_path, target)

        assert says in crossing_progress_path(tmp_path).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("target", "offers"),
        [(VR, "Press Esc to cancel entering VR"), (DESKTOP, "Press Esc to cancel exiting VR")],
    )
    def test_it_says_what_esc_would_cancel(self, tmp_path: Path, target, offers):
        raise_transition_screen(tmp_path, target)

        line = parse_progress(crossing_progress_path(tmp_path).read_text(encoding="utf-8"))
        assert line.hint == offers

    def test_dropping_it_is_what_the_loading_screen_reads_as_finished(self, tmp_path: Path):
        raise_transition_screen(tmp_path, VR)

        drop_transition_screen(tmp_path)

        assert crossing_progress_path(tmp_path).read_text(encoding="utf-8").strip() == "DONE"

    def test_an_ordinary_startup_drops_nothing_and_leaves_nothing(self, tmp_path: Path):
        drop_transition_screen(tmp_path)

        assert not crossing_progress_path(tmp_path).exists()

    def test_it_is_launched_as_the_transition_screen_over_that_file(self, tmp_path: Path):
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            launch_transition_screen(tmp_path, VR, sibling_checkouts="")

        command = popen.call_args.args[0]
        assert command[1:] == [
            "-m", "fun_time.transition_screen", str(crossing_progress_path(tmp_path)),
        ]

    def test_the_way_back_goes_up_saying_it_is_canceling(self, tmp_path: Path):
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            session_handoff.launch_the_way_back_screen(tmp_path, sibling_checkouts="")

        line = parse_progress(crossing_progress_path(tmp_path).read_text(encoding="utf-8"))
        assert (line.message, line.hint) == (CANCELING, "")
        assert popen.call_args.args[0][1:] == [
            "-m", "fun_time.transition_screen", str(crossing_progress_path(tmp_path)),
        ]

    def test_a_crossing_that_never_happened_uncovers_the_monitors(self, config):
        raise_transition_screen(config.paths.state_dir, VR)
        with patch.object(session_handoff, "wait_for_the_session_to_let_go",
                          return_value=False), \
             patch.object(session_handoff, "report_a_failed_crossing"):
            assert run(VR, config) == 1

        assert crossing_progress_path(
            config.paths.state_dir
        ).read_text(encoding="utf-8").strip() == "DONE"


class TestTheHeadsetHold:
    def test_the_hold_carries_whether_to_stop_the_runtime(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=True)
        assert headset_hold_asked(tmp_path) is True
        assert headset_hold_stops_the_runtime(tmp_path) is True

        hold_the_headset(tmp_path, stop_runtime=False)
        assert headset_hold_stops_the_runtime(tmp_path) is False

    def test_the_funestra_answers_only_once_it_has_let_go(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=False)
        assert headset_is_held(tmp_path) is False

        report_the_headset_held(tmp_path)

        assert headset_is_held(tmp_path) is True

    def test_a_new_hold_never_reads_the_last_one_s_answer(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=False)
        report_the_headset_held(tmp_path)

        hold_the_headset(tmp_path, stop_runtime=False)

        assert headset_is_held(tmp_path) is False

    def test_releasing_it_is_what_ends_the_hold(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=True)
        report_the_headset_held(tmp_path)

        release_the_headset(tmp_path)

        assert headset_hold_asked(tmp_path) is False
        assert headset_is_held(tmp_path) is False

    def test_nothing_is_held_where_no_hold_was_asked(self, tmp_path: Path):
        assert headset_hold_asked(tmp_path) is False
        assert headset_hold_stops_the_runtime(tmp_path) is False
        release_the_headset(tmp_path)

    def test_a_crossing_that_never_happened_lets_the_headset_go(self, config):
        hold_the_headset(config.paths.state_dir, stop_runtime=False)
        with patch.object(session_handoff, "wait_for_the_session_to_let_go",
                          return_value=False), \
             patch.object(session_handoff, "report_a_failed_crossing"):
            assert run(DESKTOP, config) == 1

        assert headset_hold_asked(config.paths.state_dir) is False


class TestTheKeptOrigenerator:
    def test_the_record_is_the_pid_and_the_creation_time(self, tmp_path: Path):
        """Never the pid alone: Windows hands freed pids straight back out, and
        the adopting session checks the pair against the process still there."""
        keep_the_origenerator(tmp_path, pid=4321, created_at=99)

        assert kept_origenerator(tmp_path) == (4321, 99, False)

    def test_no_record_reads_as_nothing_kept(self, tmp_path: Path):
        assert kept_origenerator(tmp_path) is None

    def test_a_record_that_cannot_be_read_is_nothing_kept(self, tmp_path: Path):
        (tmp_path / "origenerator_kept.txt").write_text("torn", encoding="utf-8")

        assert kept_origenerator(tmp_path) is None

    def test_forgetting_it_is_safe_where_there_was_none(self, tmp_path: Path):
        keep_the_origenerator(tmp_path, pid=1, created_at=2)
        forget_the_kept_origenerator(tmp_path)
        forget_the_kept_origenerator(tmp_path)

        assert kept_origenerator(tmp_path) is None


class TestWhichSessionIsWhich:
    def test_a_session_hosting_its_main_funestra_in_vr_is_the_vr_one(self):
        assert this_session(vr_main_funestra=True) is VR
        assert this_session(vr_main_funestra=False) is DESKTOP

    def test_each_target_names_the_marker_its_orchestrator_really_writes(self):
        assert DESKTOP.ready_marker == STARTUP_MARKER_NAME
        assert VR.ready_marker == VR_STARTUP_MARKER_NAME

    def test_each_target_names_a_module_a_launcher_actually_starts(self):
        launched = {
            module for launcher, module in LAUNCHED if launcher.endswith(".vbs")
        }
        assert {DESKTOP.module, VR.module} <= launched


class TestWaitingForTheOutgoingSession:
    def test_it_returns_as_soon_as_no_session_is_playing(self):
        playing = iter([True, True, False])
        with patch.object(session_handoff, "a_session_is_playing", lambda _name: next(playing)):
            assert wait_for_the_session_to_let_go("m", poll_s=0) is True

    def test_it_gives_up_on_a_session_that_wedged_holding_it(self):
        with patch.object(session_handoff, "a_session_is_playing", return_value=True):
            assert wait_for_the_session_to_let_go("m", timeout_s=0, poll_s=0) is False

    def test_a_name_left_behind_by_a_session_that_is_gone_is_not_one_playing(self):
        """What blocked every launch on this machine on 2026-09-19: a session
        whose process Windows had not finished reaping still held the name, and
        nothing was playing."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        name = rf"Local\FunTimeTest.{uuid4().hex}"
        left_behind = kernel32.CreateMutexW(None, False, name)

        assert a_session_is_playing(name) is False

        let_the_session_go(left_behind)


class TestStartingTheIncomingSession:
    def test_the_stale_marker_goes_before_the_launch(self, tmp_path: Path):
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
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            start_the_session(
                VR, python_exe="py.exe", project_dir=tmp_path / "worktree",
                state_dir=tmp_path, config_path=tmp_path / "branch.json",
            )
        command = popen.call_args.args[0]
        assert command[:5] == [
            "py.exe", "-m", "fun_time_vr.orchestrator",
            "--config", str(tmp_path / "branch.json"),
        ]
        assert popen.call_args.kwargs["cwd"] == str(tmp_path / "worktree")

    def test_a_way_back_starts_the_session_offering_no_esc(self, tmp_path: Path):
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            start_the_session(
                DESKTOP, python_exe="py.exe", project_dir=tmp_path,
                state_dir=tmp_path, config_path=tmp_path / "c.json", cancelable=False,
            )

        assert popen.call_args.args[0][-1] == "--no-cancel"


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
        assert "stopped during startup" in reason
        assert "3" in reason

    def test_a_session_that_never_reports_is_a_wedge_rather_than_a_crash(self, tmp_path: Path):
        reason = wait_for_the_session_to_come_up(
            VR, self._session(), state_dir=tmp_path, timeout_s=0, poll_s=0
        )
        assert "did not finish starting" in reason


class TestReportingAFailedCrossing:
    def test_it_carries_the_tail_of_the_launcher_log(self, tmp_path: Path):
        log = tmp_path / "vr_launcher.log"
        log.write_text("\n".join(f"line {n}" for n in range(40)) + "\n\n\n", encoding="utf-8")
        with alert_shown() as alert:
            report_a_failed_crossing("FunTimeVR did not start.", log)
        message = alert.call_args.args[1]
        assert "FunTimeVR did not start." in message
        assert "line 39" in message
        assert "line 24" not in message

    def test_a_log_that_cannot_be_read_still_gets_a_dialog(self, tmp_path: Path):
        with alert_shown() as alert:
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

    def test_a_way_back_spawns_the_relay_offering_no_esc(self, config):
        request_handoff(config.paths.state_dir, DESKTOP, cancelable=False)
        with patch.object(session_handoff.subprocess, "Popen") as popen:
            hand_over_if_asked(config, MagicMock())

        assert popen.call_args.args[0][-1] == "--no-cancel"

    def test_the_request_is_spent_so_the_next_session_does_not_cross_again(self, config):
        request_handoff(config.paths.state_dir, DESKTOP)
        with patch.object(session_handoff.subprocess, "Popen"):
            hand_over_if_asked(config, MagicMock())
            assert hand_over_if_asked(config, MagicMock()) is None


class TestTheRelaysOwnArguments:
    def test_a_way_back_is_relayed_offering_no_esc(self, config):
        with patch.object(session_handoff, "load_config", return_value=config), \
             patch.object(session_handoff, "configure_logging"), \
             patch.object(session_handoff, "install_exception_logging"), \
             patch.object(session_handoff, "run", return_value=0) as relay:
            session_handoff.main(["--target", "vr", "--config", "c.json", "--no-cancel"])

        assert relay.call_args.kwargs["cancelable"] is False


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

    def test_a_way_back_starts_its_session_offering_no_esc(self, config):
        let_go, start, come_up, report = self._patched()
        with let_go, start as started, come_up, report:
            assert run(DESKTOP, config, cancelable=False) == 0

        assert started.call_args.kwargs["cancelable"] is False

    def test_esc_during_the_crossing_opens_the_session_he_left_offering_no_esc(
        self, config,
    ):
        state_dir = config.paths.state_dir
        raise_transition_screen(state_dir, VR)
        (state_dir / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")
        let_go, start, come_up, report = self._patched()
        with let_go, start as started, come_up, report as reported:
            assert run(VR, config) == 0

        assert started.call_args.args[0] is DESKTOP
        assert started.call_args.kwargs["cancelable"] is False
        reported.assert_not_called()

    def test_a_crossing_esc_turned_back_says_so_on_the_loading_screen(self, config):
        state_dir = config.paths.state_dir
        raise_transition_screen(state_dir, VR)
        (state_dir / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")
        let_go, start, come_up, report = self._patched()
        with let_go, start, come_up, report:
            run(VR, config)

        line = parse_progress(crossing_progress_path(state_dir).read_text(encoding="utf-8"))
        assert (line.message, line.hint) == (CANCELING, "")

    def test_the_esc_that_turned_it_back_is_spent(self, config):
        state_dir = config.paths.state_dir
        raise_transition_screen(state_dir, VR)
        flag = state_dir / CANCEL_FILENAME
        flag.write_text("cancel\n", encoding="utf-8")
        let_go, start, come_up, report = self._patched()
        with let_go, start, come_up, report:
            run(VR, config)

        assert not flag.exists()

    def test_the_quit_chord_during_the_crossing_opens_nothing(self, config):
        state_dir = config.paths.state_dir
        raise_transition_screen(state_dir, VR)
        (state_dir / CANCEL_FILENAME).write_text("quit\n", encoding="utf-8")
        let_go, start, come_up, report = self._patched()
        with let_go, start as started, come_up, report as reported:
            assert run(VR, config) == 0

        started.assert_not_called()
        reported.assert_not_called()

    def test_the_quit_chord_during_the_crossing_gives_everything_back(self, config):
        state_dir = config.paths.state_dir
        raise_transition_screen(state_dir, VR)
        hold_the_headset(state_dir, stop_runtime=False)
        (state_dir / CANCEL_FILENAME).write_text("quit\n", encoding="utf-8")
        let_go, start, come_up, report = self._patched()
        with let_go, start, come_up, report, \
             patch("fun_time.windows_bridge_orchestrator."
                   "let_go_of_a_kept_origenerator") as closed:
            run(VR, config)

        assert crossing_progress_path(state_dir).read_text(encoding="utf-8").strip() == "DONE"
        assert headset_hold_asked(state_dir) is False
        closed.assert_called_once_with(Path(state_dir), config.origenerator_cmd_file)

    def test_a_way_back_is_never_turned_around_by_a_second_esc(self, config):
        state_dir = config.paths.state_dir
        raise_transition_screen(state_dir, DESKTOP)
        (state_dir / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")
        let_go, start, come_up, report = self._patched()
        with let_go, start as started, come_up, report:
            run(DESKTOP, config, cancelable=False)

        assert started.call_args.args[0] is DESKTOP

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
