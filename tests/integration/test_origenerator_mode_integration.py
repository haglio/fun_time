"""The satellite side's origenerator mode, proven on a real session.

The unit tests prove the plans and ops; what they cannot prove is the window
choreography — that the hosted app's parked window actually rises over the RFB
on the switch and parks again on the way back, and that the satellites hold
their topmost band.  Those are claims about real HWNDs, so they are tested by
running a real session against a STUB hosted app: a fabricated tkinter window
that speaks just enough of the ``--fun-time`` contract (the captions, the
parked boot, the QUIT verb) for the session to manage it.  A stub rather than
the real Origenerator because the suite must not touch the machine's one
ComfyUI, GPU queue, or gallery database — the same reason
``isolate_shared_resources`` strips the config key outright.
"""
from __future__ import annotations

import json
import shutil
import sys
import textwrap
import time
from pathlib import Path

import pytest

from fun_time.event_log import event_log_path
from fun_time.win32 import (
    find_window_for_process,
    is_window_minimized,
    is_window_topmost,
    iter_zorder,
    set_always_on_top,
    wait_for_window_by_title,
    windows_obscuring,
)
from fun_time.shared_state import (
    read_shared_state,
    shared_state_path,
    write_shared_state,
)
from fun_time.shared_state import BridgeState
from fun_time.windows_bridge_orchestrator import _fix_post_loading_windows
from fun_time.windows_bridge_sequencer import StartupResult

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = [
    # Real players on a real desktop: the post-loading pass has to be given
    # the real resolve budget, not the unit conftest's zeroed one.
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
),
]

# A fabricated stand-in for the hosted app: parses the --fun-time contract
# loosely, boots the way the real one does — a short-lived splash wearing the
# app's caption FIRST (the twin that once got cached as the app), then the
# main window, parked (iconified) and topmost — and exits on QUIT.
_STUB_MAIN = textwrap.dedent(
    """
    import argparse
    import tkinter as tk
    from pathlib import Path

    parser = argparse.ArgumentParser()
    for flag in ("--x", "--y", "--width", "--height"):
        parser.add_argument(flag, type=int, default=0)
    for side in ("portrait", "landscape"):
        for field in ("x", "y", "width", "height"):
            parser.add_argument(f"--{side}_{field}", type=int, default=0)
    parser.add_argument("--command-file")
    args, _rest = parser.parse_known_args()

    root = tk.Tk()
    root.withdraw()  # the main window arrives only after the "boot"

    splash = tk.Toplevel(root)
    splash.title("Origenerator")  # the caption twin the session must survive
    splash.geometry("200x80+10+10")

    def finish_boot():
        splash.destroy()
        root.title("Origenerator")
        root.geometry(
            f"{max(args.width, 120)}x{max(args.height, 80)}+{args.x}+{args.y}")
        root.attributes("-topmost", True)
        root.deiconify()
        root.iconify()  # boots parked, like the real app

    shows = {}

    def open_shows():
        # The region shows: frameless, topmost, on the rects the session named
        # — the same shape the real app gives them.
        for side, title in (("portrait", "Origenerator Portrait"),
                            ("landscape", "Origenerator Landscape")):
            if side in shows:
                continue
            rect = [getattr(args, f"{side}_{field}")
                    for field in ("x", "y", "width", "height")]
            show = tk.Toplevel(root)
            show.title(title)
            show.overrideredirect(False)
            show.geometry(f"{max(rect[2], 120)}x{max(rect[3], 80)}+{rect[0]}+{rect[1]}")
            show.attributes("-topmost", True)
            shows[side] = show

    def close_shows():
        for show in shows.values():
            show.destroy()
        shows.clear()

    def poll():
        command_file = Path(args.command_file) if args.command_file else None
        if command_file is not None and command_file.exists():
            try:
                text = command_file.read_text(encoding="utf-8")
                command_file.unlink()
            except OSError:
                text = ""
            verbs = text.upper()
            if "QUIT" in verbs:
                root.destroy()
                return
            if "OPEN_SHOWS" in verbs:
                open_shows()
            if "CLOSE_SHOWS" in verbs:
                close_shows()
        root.after(150, poll)

    root.after(3000, finish_boot)
    root.after(150, poll)
    root.mainloop()
    """
)


def _write_stub_checkout(destination: Path) -> Path:
    package = destination / "origenerator"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(_STUB_MAIN, encoding="utf-8")
    return destination


def _host_stub(config_path: Path, stub_root: Path) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["paths"]["origenerator_dir"] = str(stub_root)
    # The stub is plain tkinter, so the suite's own interpreter runs it.
    raw["paths"]["origenerator_python_exe"] = sys.executable
    config_path.write_text(json.dumps(raw), encoding="utf-8")


@pytest.fixture(scope="module")
def hosted_session():
    """A real session hosting the stub app, plus that app's main HWND.

    One session for the whole module: launching a session is the expensive
    half of these tests, and every extra launch is GPU and decode churn the
    suite's perf-gated tests downstream then pay for.  The tests leave the
    session the way they found it (player mode, satellites banded).
    """
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        pid = session.read_child_pids().get("origenerator_pid", 0)
        assert pid, "the session did not record a hosted origenerator child"
        # Wait past the splash decoy for the PARKED main window — the decoy
        # wears the same caption but is a normal visible window.
        deadline = time.monotonic() + 20.0
        hwnd = 0
        while time.monotonic() < deadline:
            hwnd = find_window_for_process(pid, "Origenerator")
            if hwnd and is_window_minimized(hwnd):
                break
            hwnd = 0
            time.sleep(0.2)
        stderr_file = session.config.paths.state_dir / "orchestrator_stderr.log"
        stderr_tail = stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:] \
            if stderr_file.exists() else "(no stderr file)"
        assert hwnd, f"the stub's parked main window never appeared; stderr: {stderr_tail}"
        yield session, hwnd
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


def _wait(predicate, *, timeout: float, desc: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {desc} (last={last!r})")


def test_the_switch_raises_the_parked_window_and_the_way_back_parks_it(hosted_session):
    """The user-visible contract of the mode pair, on real windows: the hosted
    app boots parked; origenerator mode restores it over the RFB's rect and
    into the topmost band; player mode parks it again.  Driven through an
    OmniPause cycle first, because that is the sequence the demo failed in —
    the pause demotes every managed window, and the switch afterwards has to
    promote this one back itself."""
    session, hwnd = hosted_session
    assert is_window_minimized(hwnd)  # booted parked

    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: entering")
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: leaving")

    session.write_dashboard_command("origenerator_activate")
    session.wait_for_log("Satellites switched to origenerator mode")
    _wait(lambda: not is_window_minimized(hwnd),
          timeout=10, desc="the hosted window to be restored")
    _wait(lambda: is_window_topmost(hwnd),
          timeout=10, desc="the hosted window to join the topmost band")

    session.write_dashboard_command("players_activate")
    session.wait_for_log("Satellites switched to player mode")
    _wait(lambda: is_window_minimized(hwnd),
          timeout=10, desc="the hosted window to park again")


def test_the_post_overlay_pass_rebands_satellites_recorded_under_shim_pids(hosted_session):
    """The demo's 'landscape player behind other windows': the post-overlay
    pass resolved the satellites by pid, python_exe is the venv's pythonw
    shim, and both lookups found nothing — so the pass silently skipped the
    only banding the satellites get on a loading-screen startup.  Reproduced
    here with the session's real recorded pids and real windows: demote both
    players (what the overlay's teardown can leave), run the pass, and the
    title fallback must put both back in the band."""
    session, _hwnd = hosted_session
    pids = session.read_child_pids()
    portrait = wait_for_window_by_title("Portrait AI Player", timeout_s=10, exact=True)
    landscape = wait_for_window_by_title("Landscape AI Player", timeout_s=10, exact=True)
    assert portrait and landscape

    set_always_on_top(portrait, False)
    set_always_on_top(landscape, False)
    assert not is_window_topmost(portrait)
    assert not is_window_topmost(landscape)

    _fix_post_loading_windows(StartupResult(
        nau_pid=pids["nau_pid"],
        portrait_pid=pids["portrait_pid"],
        landscape_pid=pids["landscape_pid"],
        dashboard_pid=0,
        genau_pid=pids["genau_pid"],
        audio_pid=0,
        main_mode="nau",
    ))

    assert is_window_topmost(portrait)
    assert is_window_topmost(landscape)


def test_a_session_that_opens_in_the_mode_leaves_its_shows_over_the_players():
    """The one he kept reporting: a session that OPENS in origenerator mode
    shows a picture on each region and then a black rectangle wearing the
    satellite's own HUD — the blacked player, back on top of the show it is
    supposed to be under.

    A session of its own rather than the module's, because the fault is in the
    startup path: the mode has to be the one the session RESUMES into (the
    shared state seeded before launch), and the loading-screen path has to be
    the one it takes, since that is where the reveal and the settle pass live.
    Asked over ten seconds after the reveal rather than once: the burial
    arrived a few seconds late every time he saw it, so a single look right
    after startup is exactly the check that kept passing.
    """
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    # Faked side-by-side monitors for the same reason the loading-screen test
    # fakes them: on the hidden desktop's single screen the real layout
    # collapses every window onto it and the regions legitimately overlap,
    # which makes "is this show frontmost over its rect" unanswerable.
    overlay_env = {
        "FUN_TIME_INTEGRATION_OVERLAYS": "1",
        "FUN_TIME_FAKE_MONITORS": "0,0,1280,720;1280,0,720,1440",
    }
    try:
        # Two launches, because a session only carries its satellites mode
        # forward when it RESUMED the playlists — a first run builds fresh and
        # opens on the defaults, whatever the state file says.  So the first
        # launch is the one that leaves a session to come back to, and the
        # second is the one under test.  Which is also how he hits it.
        session.start(wait_seconds=120.0, env_overrides=overlay_env)
        session.stop()
        state_file = shared_state_path(session.config.paths.state_dir)
        write_shared_state(state_file, BridgeState(satellites_mode="origenerator"))

        session.start(wait_seconds=120.0, env_overrides=overlay_env)
        assert read_shared_state(state_file).satellites_mode == "origenerator", (
            "the session did not come back in origenerator mode, so this test "
            "is not exercising the startup path at all"
        )
        events = event_log_path(session.config.paths.state_dir)
        text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
        assert "Loading screen launched" in text, (
            "the session did not take the loading-screen path, which is where "
            "the reveal and the settle pass live"
        )

        shows = {
            title: wait_for_window_by_title(title, timeout_s=30, exact=True)
            for title in ("Origenerator Portrait", "Origenerator Landscape")
        }
        for title, hwnd in shows.items():
            assert hwnd, (
                f"{title} never appeared — a session opening in the mode has to "
                "send OPEN_SHOWS, and the hosted app answers it by filling both "
                "regions"
            )

        # Ten seconds of it, because the burial he saw landed seconds after the
        # picture did.  The first cover wins: report it and stop.
        # Each other is not a burial: the faked monitors are small enough that
        # the two region rects overlap here, which they never do on his.
        siblings = set(shows.values())
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            stack = iter_zorder()
            for title, hwnd in shows.items():
                covering = [w for w in windows_obscuring(hwnd, stack)
                            if w.hwnd not in siblings]
                assert not covering, (
                    f"{title} (hwnd={hwnd}) was covered after the reveal by: "
                    + "; ".join(
                        f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}"
                        for w in covering
                    )
                )
            time.sleep(0.5)
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)
