"""The hotkey script runs headless — no tray icon, no tray menu — and owns the
one chord that ends a session."""
from __future__ import annotations

from pathlib import Path

from fun_time.overlay_progress import CANCEL_FILENAME

_SCRIPT = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"


def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    """The source of one AHK function, up to its closing brace.

    The script is not importable and has no test harness of its own, so what
    can be checked here is its text.  Reading a whole function rather than
    grepping the file keeps an assertion about one function from passing on a
    line that happens to sit in another.
    """
    text = _script_text()
    for line in text.splitlines():
        if line.startswith(f"{name}(") and line.endswith("{"):
            start = text.index(f"\n{line}\n")
            return text[start:text.index("\n}\n", start)]
    raise AssertionError(f"the script defines no function named {name}")


def _suspend_exempt_block() -> str:
    text = _script_text()
    return text.split("#SuspendExempt true", 1)[1].split("#SuspendExempt false", 1)[0]


def test_script_suppresses_the_tray_icon():
    """AutoHotkey gives a persistent script a tray icon unless told otherwise.

    The directive is the only thing between this process and an icon in the
    notification area, so dropping the line silently puts it back.
    """
    assert "#NoTrayIcon" in _script_text()


def test_script_builds_no_tray_menu():
    """Nothing dresses an icon that is never shown.

    A suppressed icon still accepts ``TraySetIcon``/``A_TrayMenu`` calls without
    complaint, so the menu could sit here indefinitely as code that runs and
    reaches no one.
    """
    text = _script_text()
    for call in ("TraySetIcon", "A_IconTip", "A_TrayMenu"):
        assert call not in text, f"{call} dresses a tray icon the script does not show"


def test_ctrl_alt_q_ends_the_whole_session():
    """The way out of a session, and the reason no player has one of its own.

    Every window a session opens comes down together, and this line is the whole
    mechanism: the orchestrator sits on ``ahk_proc.wait()``, so the script
    exiting is what releases ``_shutdown_children``.  Take the binding away and
    each player is on its own again — which is the failure the satellites' "no
    key here ends this player" comment and genau's ``quits_this_player`` are both
    written against.

    The integration suite cannot stand in for this: ``quit_gracefully`` puts
    ``exit`` in the AHK mailbox rather than pressing anything, so it exercises
    the teardown and never the chord that is supposed to start it.
    """
    assert "^!q::EndSession()" in _script_text(), (
        "nothing binds Ctrl+Alt+Q to ending the session"
    )
    assert "ExitApp()" in _function_body("EndSession"), (
        "the chord no longer exits the script, so nothing releases the orchestrator"
    )


def test_the_way_out_survives_omnipause():
    """OmniPause suspends the hotkeys wholesale, and a paused session still has
    to be closable — so the quit is inside the exempt block, with Esc and the
    sensation emergency.  Suspended, the chord would reach whatever window had
    focus instead, and a session could be paused into having no way out."""
    assert "^!q::EndSession()" in _suspend_exempt_block(), (
        "the quit is suspendable — OmniPause can trap a session"
    )


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

        body = _function_body("PauseOrCancelStartup")
        assert "RequestStartupCancel()" in body
        assert 'QueueCommand("omnipause_toggle")' in body

    def test_the_quit_chord_calls_it_off_too_rather_than_exiting(self):
        """Exiting mid-launch would leave the orchestrator building a session it
        has been told to end and only take that session down once it was fully
        up — and it would take Esc's cancel with it, since a script that has
        exited hooks nothing."""
        assert "RequestStartupCancel()" in _function_body("EndSession")

    def test_the_flag_it_drops_is_the_one_the_orchestrator_watches(self):
        """Two processes drop this flag — this script and the loading screen —
        and the orchestrator's progress checkpoints watch for one name.  Nothing
        else pins the AHK-side spelling to the Python-side constant."""
        assert f'"\\{CANCEL_FILENAME}"' in _script_text(), (
            f"the script does not drop {CANCEL_FILENAME}, so its Esc cancels nothing"
        )

    def test_the_keys_that_drive_a_session_are_held_until_there_is_one(self):
        """Queued at the loading screen they would go into a file no dispatch
        loop is draining yet, to be acted on whenever one starts."""
        body = _function_body("QueueCommand")
        held = body.index("if (StartupPhase)")
        assert held < body.index("AppendWithRetry"), (
            "QueueCommand writes before it checks the startup hold"
        )

    def test_the_hold_lifts_when_the_session_is_up(self):
        """The orchestrator writes the pids file once every window is placed.
        Polled rather than announced down the command mailbox: that mailbox is
        one slot with several writers, and a handover lost there would leave
        every hotkey dead for the rest of the session."""
        body = _function_body("WatchStartup")
        assert "FileExist(PIDS_FILE_PATH)" in body
        assert "StartupPhase := false" in body

    def test_the_held_keys_pass_through_rather_than_being_swallowed(self):
        """A gated hotkey still consumes its key; a suspended one does not.
        During a launch the focus may well be on an app of the user's own — that
        is the premise of the whole change — so what they type there has to reach
        it rather than vanish into a script with nothing to do with it."""
        assert "\nSuspend true\n" in _script_text(), (
            "the script does not start suspended, so it eats keys during a launch"
        )
        assert "Suspend false" in _function_body("WatchStartup")

    def test_a_suspend_anything_else_set_survives_the_handover(self):
        """An integration run pre-writes suspend_hotkeys and OmniPause suspends
        mid-session.  Releasing the startup hold must not undo either — the flag
        is what says the hold is still ours to let go of."""
        assert "StartupSuspended := false" in _function_body("ProcessAhkCommand")
        assert "if (StartupSuspended)" in _function_body("WatchStartup")
