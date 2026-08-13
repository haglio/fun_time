"""The hotkey script runs headless — no tray icon, no tray menu — and owns the
one chord that ends a session."""
from __future__ import annotations

from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"


def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


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
    assert "^!q::ExitApp()" in _script_text(), (
        "nothing binds Ctrl+Alt+Q to ending the session"
    )


def test_the_way_out_survives_omnipause():
    """OmniPause suspends the hotkeys wholesale, and a paused session still has
    to be closable — so the quit is inside the exempt block, with Esc and the
    sensation emergency.  Suspended, the chord would reach whatever window had
    focus instead, and a session could be paused into having no way out."""
    text = _script_text()
    exempt = text.split("#SuspendExempt true", 1)[1].split("#SuspendExempt false", 1)[0]

    assert "^!q::ExitApp()" in exempt, "the quit is suspendable — OmniPause can trap a session"
