"""The hotkey script runs headless — no tray icon, no tray menu — and owns the
one chord that ends a session."""
from __future__ import annotations

from fun_time.overlay_progress import CANCEL_FILENAME
from fun_time.session_handoff import COVER_STALE_S, CROSSING_PROGRESS_NAME
from tests.ahk_script import function_code, function_source, script_text


def _suspend_exempt_block() -> str:
    text = script_text()
    return text.split("#SuspendExempt true", 1)[1].split("#SuspendExempt false", 1)[0]


def test_script_suppresses_the_tray_icon():
    """AutoHotkey gives a persistent script a tray icon unless told otherwise.

    The directive is the only thing between this process and an icon in the
    notification area, so dropping the line silently puts it back.
    """
    assert "#NoTrayIcon" in script_text()


def test_script_builds_no_tray_menu():
    """Nothing dresses an icon that is never shown.

    A suppressed icon still accepts ``TraySetIcon``/``A_TrayMenu`` calls without
    complaint, so the menu could sit here indefinitely as code that runs and
    reaches no one.
    """
    text = script_text()
    for call in ("TraySetIcon", "A_IconTip", "A_TrayMenu"):
        assert call not in text, f"{call} dresses a tray icon the script does not show"


def test_ctrl_alt_q_ends_the_whole_session():
    """The way out of a session, and the reason no player has one of its own.

    Every window a session opens comes down together, and this line is the whole
    mechanism: the orchestrator waits for the session-end marker (or the script
    going), so the chord marking the end is what releases ``_shutdown_children``.
    Take the binding away and each player is on its own again — which is the
    failure the satellites' "no key here ends this player" comment and genau's
    ``quits_this_player`` are both written against.

    The integration suite cannot stand in for this: ``quit_gracefully`` puts
    ``exit`` in the AHK mailbox rather than pressing anything, so it exercises
    the teardown and never the chord that is supposed to start it.
    """
    assert "^!q::EndSession()" in script_text(), (
        "nothing binds Ctrl+Alt+Q to ending the session"
    )
    assert "EndTheSession(" in function_source("EndSession"), (
        "the chord no longer ends the session, so nothing releases the orchestrator"
    )


def test_the_way_out_survives_omnipause():
    """OmniPause suspends the hotkeys wholesale, and a paused session still has
    to be closable — so the quit is inside the exempt block, with Esc and the
    sensation emergency.  Suspended, the chord would reach whatever window had
    focus instead, and a session could be paused into having no way out."""
    assert "^!q::EndSession()" in _suspend_exempt_block(), (
        "the quit is suspendable — OmniPause can trap a session"
    )


def test_the_letter_hotkeys_yield_while_origenerator_has_the_keyboard():
    """The hosted Origenerator's MAIN window is a typing app and these hotkeys
    are bare letters, so while it is focused the keyboard is its — except the
    exempt trio (quit and the omnipause pair), which are session gestures
    wherever the focus sits and must stay above the gate."""
    text = script_text()
    gate = text.index("#HotIf !OrigeneratorHasKeyboard\n")
    gate_close = text.index("#HotIf", gate + 1)
    # Exact-title matching, never a title that merely contains the name:
    # "Origenerator" appears in plenty of other window titles (an Explorer at
    # the checkout, a terminal on a branch), and a substring match killed every
    # hotkey while one of those was focused.
    watch = function_code("WatchWhoHasTheKeyboard")
    assert '= "Origenerator")' in watch
    assert "InStr(" not in watch
    for exempt in ("^!q::", "Esc::QueueCommand", "+Esc::QueueCommand"):
        assert text.index(exempt) < gate, exempt
    for gated in ('x::QueueCommand("satellites_toggle")',
                  'a::QueueCommand("landscape_prev")',
                  'g::QueueCommand("genau_activate")',
                  'Left::QueueCommand("portrait_prev")'):
        position = text.index(gated)
        assert gate < position < gate_close, gated


class TestTheGateIsAFlagNotAQuestion:
    """AutoHotkey evaluates a ``#HotIf`` expression on the script's main thread
    while the keyboard hook HOLDS the key, and that hook sees every key pressed
    anywhere on the machine -- not only the session's.  So a gate that asks
    Windows anything delays the user's typing in whatever app he is in, which
    from his chair is a dead keyboard.  A timer does the asking; the gate reads
    what it wrote."""

    def test_the_gate_reads_a_flag_rather_than_asking_windows(self):
        text = script_text()
        gate = text[text.index("#HotIf !OrigeneratorHasKeyboard"):]

        assert gate[:gate.index("\n")] == "#HotIf !OrigeneratorHasKeyboard", (
            "the gate calls something, so the hook holds each key for an answer"
        )

    def test_a_timer_keeps_the_flag_fresh(self):
        """Nothing else writes it, so a missing timer gates the letters off --
        or on -- for the whole session.  The interval is how long the answer may
        be wrong, and wrong one way his typing in Origenerator runs as session
        commands: w throws away the clip on the landscape side."""
        text = script_text()
        assert "SetTimer(WatchWhoHasTheKeyboard, " in text, "nothing refreshes the flag"

        started = text.index("SetTimer(WatchWhoHasTheKeyboard, ")
        interval = int(text[started:text.index(")", started)].rsplit(",", 1)[1])

        assert interval <= 100, (
            f"{interval}ms is longer than it takes him to start typing after switching windows"
        )

    def test_the_flag_is_written_before_the_first_hotkey_line(self):
        """The auto-execute section ends at the first hotkey, so an assignment
        below that never runs -- and a ``#HotIf`` reading an unset variable
        throws, with no call site to catch it, putting AutoHotkey's own error
        dialog on the screen once per key pressed."""
        text = script_text()

        assert text.index("OrigeneratorHasKeyboard := false") < text.index("^!q::EndSession()")

    def test_the_refresher_never_waits_on_the_window_it_reads(self):
        """It runs on the same thread that answers the gate, so a call that
        waits for another app to reply would stall every keystroke exactly as
        the question it replaced did.  Windows documents ``GetWindowTextW`` as
        handing back the caption it already holds for another process's window,
        asking that process nothing; AutoHotkey's window functions make no such
        promise."""
        watch = function_code("WatchWhoHasTheKeyboard")

        assert "GetWindowTextW" in watch
        for waits in ("WinGetTitle", "WinGetText", "WinWait", "SendMessage"):
            assert waits not in watch, f"{waits} can wait on the app that is focused"


def test_the_region_shows_do_not_gate_the_hotkeys():
    """The arrows and WASD drive the portrait and landscape regions by SIDE,
    exactly as they drive the players — wherever the focus sits, a show's
    included.  A show has no text field, so only the main window (the typing
    app) may take the keyboard away; gating on the show captions left a
    focused slideshow answering its own arrows instead of the side's."""
    watch = function_code("WatchWhoHasTheKeyboard")
    assert '"Origenerator Portrait"' not in watch
    assert '"Origenerator Landscape"' not in watch


def test_it_is_told_which_orchestrator_launched_it():
    """Both orchestrators pass their own process id third: after a crossing the
    script outlives that process, and has to know when it is gone."""
    text = script_text()

    assert "if (A_Args.Length < 3)" in text
    assert "A_Args[3]" in text


class TestTheSessionEndsButTheScriptStays:
    """Esc over the closing cover is how a quit gets called off, and a script
    that has exited hooks nothing -- so ending a session leaves it running,
    with only Esc and the quit chord live, until the orchestrator stops it."""

    def test_end_session_on_the_mailbox_ends_the_session_without_exiting(self):
        body = function_source("ProcessAhkCommand")
        assert '(action = "end_session")' in body, "the mailbox has no end_session"
        branch = body[body.index('(action = "end_session")'):]
        branch = branch[:branch.index("}")]

        assert "EndTheSession(" in branch
        assert "ExitApp" not in branch

    def test_esc_over_the_closing_cover_drops_the_cancel_flag(self):
        body = function_source("PauseOrCancelStartup")
        guard = body[:body.index('RequestStartupCancel("cancel")')]

        assert "EndingPhase" in guard

    def test_the_quit_chord_over_the_closing_cover_says_quit_in_the_flag(self):
        """So a teardown can tell "end everything" from a cancel."""
        body = function_source("EndSession")
        guard = body[:body.index('RequestStartupCancel("quit")')]

        assert "EndingPhase" in guard

    def test_ending_the_session_starts_watching_for_nothing_left_to_serve(self):
        """Over a crossing the orchestrator exits first and nothing stops this
        script; left running, Esc and the quit chord would be dead everywhere."""
        assert "SetTimer(WatchEnding" in function_source("EndTheSession")

    def test_the_watch_waits_out_both_the_session_and_its_crossing(self):
        """Over the closing cover the orchestrator is alive; over a crossing the
        cover's file is fresh.  Only with neither is there nothing left."""
        body = function_source("WatchEnding")
        before_exiting = body[:body.index("ExitApp")]

        assert "OrchestratorGone()" in before_exiting
        assert "CrossingUnderWay()" in before_exiting

    def test_the_crossing_it_watches_is_the_one_the_sessions_keep(self):
        """One file and one timeout, spelled in two languages."""
        text = script_text()

        assert f'"\\{CROSSING_PROGRESS_NAME}"' in text
        assert f"CROSSING_STALE_S := {COVER_STALE_S:g}" in text

    def test_ending_the_session_clears_a_cancel_flag_from_before_it_ended(self):
        """Nothing in a live session drops the flag, so one lying there when the
        session ends is from before it was up, and would read as Esc calling
        this end off."""
        body = function_source("EndTheSession")

        assert "FileDelete(STARTUP_CANCEL_FILE)" in body
        assert body.index("FileDelete(STARTUP_CANCEL_FILE)") < body.index("EndingPhase := true")

    def test_the_exit_that_stops_the_script_marks_only_a_live_session(self):
        """The orchestrator stops the script after a cancelled launch and after
        every teardown; a marker written then is found by the next session and
        read as its own end."""
        body = function_source("ProcessAhkCommand")
        exit_branch = body[body.index('(action = "exit")'):]
        guard = exit_branch[:exit_branch.index("MarkSessionEnd(")]

        assert "StartupPhase" in guard and "EndingPhase" in guard


class TestStartupPhase:
    """The script goes up with the loading screen, ahead of every window the
    session opens, because its hotkeys are the only keys in a launch that do not
    care what holds the focus.  Until the session is there, though, it holds the
    keys that drive one and turns the two that mean "stop" into a cancel."""

    def test_esc_calls_the_launch_off_before_it_pauses_a_session(self):
        """The whole reason the script goes up this early.  Esc on the loading
        screen is the one way to abort a launch, and the overlay's own binding
        only works while the overlay holds the focus — which is exactly what a
        launch cannot guarantee, since something else taking it mid-launch is
        what left a launch uncancellable."""
        assert "Esc::PauseOrCancelStartup()" in _suspend_exempt_block()

        body = function_source("PauseOrCancelStartup")
        assert 'RequestStartupCancel("cancel")' in body
        assert 'QueueCommand("omnipause_toggle")' in body

    def test_the_quit_chord_calls_it_off_too_rather_than_exiting(self):
        """Exiting mid-launch would leave the orchestrator building a session it
        has been told to end and only take that session down once it was fully
        up — and it would take Esc's cancel with it, since a script that has
        exited hooks nothing.

        It says which key asked, because the two mean opposite things about who
        takes the monitors back and the orchestrator cannot tell them apart any
        other way: crossing over leaves a session-end marker of its own, so
        reading THAT made every Esc look like a quit."""
        assert 'RequestStartupCancel("quit")' in function_source("EndSession")

    def test_the_flag_it_drops_is_the_one_the_orchestrator_watches(self):
        """Two processes drop this flag — this script and the loading screen —
        and the orchestrator's progress checkpoints watch for one name.  Nothing
        else pins the AHK-side spelling to the Python-side constant."""
        assert f'"\\{CANCEL_FILENAME}"' in script_text(), (
            f"the script does not drop {CANCEL_FILENAME}, so its Esc cancels nothing"
        )

    def test_the_keys_that_drive_a_session_are_held_until_there_is_one(self):
        """Queued at the loading screen they would go into a file no dispatch
        loop is draining yet, to be acted on whenever one starts."""
        body = function_source("QueueCommand")
        held = body.index("if (StartupPhase)")
        assert held < body.index("AppendWithRetry"), (
            "QueueCommand writes before it checks the startup hold"
        )

    def test_the_hold_lifts_when_the_session_is_up(self):
        """The orchestrator writes the pids file once every window is placed.
        Polled rather than announced down the command mailbox: that mailbox is
        one slot with several writers, and a handover lost there would leave
        every hotkey dead for the rest of the session."""
        body = function_source("WatchStartup")
        assert "FileExist(PIDS_FILE_PATH)" in body
        assert "StartupPhase := false" in body

    def test_the_held_keys_pass_through_rather_than_being_swallowed(self):
        """A gated hotkey still consumes its key; a suspended one does not.
        During a launch the focus may well be on an app of the user's own — that
        is the premise of the whole change — so what they type there has to reach
        it rather than vanish into a script with nothing to do with it."""
        assert "\nSuspend true\n" in script_text(), (
            "the script does not start suspended, so it eats keys during a launch"
        )
        assert "Suspend false" in function_source("WatchStartup")

    def test_a_suspend_anything_else_set_survives_the_handover(self):
        """An integration run pre-writes suspend_hotkeys and OmniPause suspends
        mid-session.  Releasing the startup hold must not undo either — the flag
        is what says the hold is still ours to let go of."""
        assert "StartupSuspended := false" in function_source("ProcessAhkCommand")
        assert "if (StartupSuspended)" in function_source("WatchStartup")
