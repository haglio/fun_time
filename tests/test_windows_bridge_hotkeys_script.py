from __future__ import annotations

import re

from fun_time.overlay_progress import CANCEL_FILENAME
from fun_time.session_handoff import CROSSING_PROGRESS_NAME, LOADING_SCREEN_STALE_S
from tests.ahk_script import function_source, script_text, suspend_exempt_block, suspend_exempt_keys


def test_a_lifted_function_holds_no_line_of_another():
    opened_here = [line for line in function_source("EndSession").splitlines()
                   if line[:1].isalpha() and line.endswith("{")]

    assert opened_here == ["EndSession() {"]


def test_script_suppresses_the_tray_icon():
    assert "#NoTrayIcon" in script_text()


def test_script_builds_no_tray_menu():
    text = script_text()
    for call in ("TraySetIcon", "A_IconTip", "A_TrayMenu"):
        assert call not in text, f"{call} dresses a tray icon the script does not show"


def test_ctrl_alt_q_ends_the_whole_session():
    assert "^!q::EndSession()" in script_text(), (
        "nothing binds Ctrl+Alt+Q to ending the session"
    )
    assert "EndTheSession(" in function_source("EndSession"), (
        "the chord no longer ends the session, so nothing releases the orchestrator"
    )


def test_the_way_out_survives_omnipause():
    assert "^!q::EndSession()" in suspend_exempt_block(), (
        "the quit is suspendable — OmniPause can trap a session"
    )


def test_the_pause_leaves_exactly_three_keys_live():
    assert suspend_exempt_keys() == {"Esc", "^!q", "+Esc"}


def test_the_hotkeys_stay_live_whichever_window_has_the_keyboard():
    conditions = [line for line in script_text().splitlines() if line.startswith("#HotIf ")]

    assert conditions == []


def test_it_is_told_which_orchestrator_launched_it():
    text = script_text()

    assert "if (A_Args.Length < 3)" in text
    assert "A_Args[3]" in text


class TestTheSessionEndsButTheScriptStays:
    def test_end_session_on_the_mailbox_ends_the_session_without_exiting(self):
        body = function_source("ProcessAhkCommand")
        assert '(action = "end_session")' in body, "the mailbox has no end_session"
        branch = body[body.index('(action = "end_session")'):]
        branch = branch[:branch.index("}")]

        assert "EndTheSession(" in branch
        assert "ExitApp" not in branch

    def test_esc_over_the_closing_screen_drops_the_cancel_flag(self):
        body = function_source("PauseOrCancelStartup")
        guard = body[:body.index('RequestStartupCancel("cancel")')]

        assert "EndingPhase" in guard

    def test_the_quit_chord_over_the_closing_screen_says_quit_in_the_flag(self):
        body = function_source("EndSession")
        guard = body[:body.index('RequestStartupCancel("quit")')]

        assert "EndingPhase" in guard

    def test_ending_the_session_starts_watching_for_nothing_left_to_serve(self):
        assert "SetTimer(WatchEnding" in function_source("EndTheSession")

    def test_the_watch_waits_out_both_the_session_and_its_crossing(self):
        body = function_source("WatchEnding")
        before_exiting = body[:body.index("ExitApp")]

        assert "OrchestratorGone()" in before_exiting
        assert "CrossingUnderWay()" in before_exiting

    def test_the_crossing_it_watches_is_the_one_the_sessions_keep(self):
        text = script_text()

        assert f'"\\{CROSSING_PROGRESS_NAME}"' in text
        assert f"CROSSING_STALE_S := {LOADING_SCREEN_STALE_S:g}" in text

    def test_ending_the_session_clears_a_cancel_flag_from_before_it_ended(self):
        body = function_source("EndTheSession")

        assert "FileDelete(STARTUP_CANCEL_FILE)" in body
        assert body.index("FileDelete(STARTUP_CANCEL_FILE)") < body.index("EndingPhase := true")

    def test_the_exit_that_stops_the_script_marks_only_a_live_session(self):
        body = function_source("ProcessAhkCommand")
        exit_branch = body[body.index('(action = "exit")'):]
        guard = exit_branch[:exit_branch.index("MarkSessionEnd(")]

        assert "StartupPhase" in guard
        assert "EndingPhase" in guard


class TestStartupPhase:
    def test_esc_calls_the_launch_off_before_it_pauses_a_session(self):
        assert "Esc::PauseOrCancelStartup()" in suspend_exempt_block()

        body = function_source("PauseOrCancelStartup")
        assert 'RequestStartupCancel("cancel")' in body
        assert 'QueueCommand("omnipause_toggle")' in body

    def test_the_quit_chord_calls_it_off_too_rather_than_exiting(self):
        assert 'RequestStartupCancel("quit")' in function_source("EndSession")

    def test_the_flag_it_drops_is_the_one_the_orchestrator_watches(self):
        assert f'"\\{CANCEL_FILENAME}"' in script_text(), (
            f"the script does not drop {CANCEL_FILENAME}, so its Esc cancels nothing"
        )

    def test_the_keys_that_drive_a_session_are_held_until_there_is_one(self):
        body = function_source("QueueCommand")
        held = body.index("if (StartupPhase)")
        assert held < body.index("AppendWithRetry"), (
            "QueueCommand writes before it checks the startup hold"
        )

    def test_the_hold_lifts_when_the_session_is_up(self):
        body = function_source("WatchStartup")
        assert "FileExist(PIDS_FILE_PATH)" in body
        assert "StartupPhase := false" in body

    def test_the_held_keys_pass_through_rather_than_being_swallowed(self):
        assert "\nSuspend true\n" in script_text(), (
            "the script does not start suspended, so it eats keys during a launch"
        )
        assert "StartupSuspended := false" in function_source("WatchStartup")
        assert "ApplyHolds()" in function_source("WatchStartup")

    def test_a_suspend_anything_else_set_survives_the_handover(self):
        assert "Suspend(StartupSuspended || PauseHold || HeadsetOff || EndingPhase)" in (
            function_source("ApplyHolds"))


class TestWithTheHeadsetOff:
    def test_the_mailbox_takes_every_line_it_was_sent(self):
        body = function_source("ProcessAhkCommand")
        assert "Loop Parse" in body
        assert "ApplyHolds()" in body

    def test_the_headset_coming_off_and_going_back_on_are_a_hold_of_their_own(self):
        body = function_source("ProcessAhkCommand")
        off = body[body.index('(action = "headset_off")'):]
        on = body[body.index('(action = "headset_on")'):]
        assert off.index("HeadsetOff := true") < off.index("}")
        assert on.index("HeadsetOff := false") < on.index("}")

    def test_the_pauses_own_let_go_leaves_the_headsets_hold_alone(self):
        body = function_source("ProcessAhkCommand")
        let_go = body[body.index('(action = "unsuspend_hotkeys")'):]
        assert let_go.index("PauseHold := false") < let_go.index("}")
        assert "Suspend false" not in body

    def test_with_the_headset_off_the_only_key_left_is_the_quit(self):
        body = function_source("ApplyHolds")
        assert 'Hotkey("Esc", ' in body
        assert 'Hotkey("+Esc", ' in body
        assert "^!q" not in body

    def test_over_the_closing_screen_esc_calls_a_quit_off_whatever_the_headset_said(self):
        body = function_source("ApplyHolds")
        assert "HeadsetOff && !EndingPhase" in body
        assert "ApplyHolds()" in function_source("EndTheSession")


def test_the_keys_let_go_within_a_twentieth_of_a_second_of_a_pause():
    interval = re.search(r"SetTimer\(ProcessAhkCommand, (\d+)\)", script_text())

    assert interval is not None
    assert int(interval.group(1)) <= 50
