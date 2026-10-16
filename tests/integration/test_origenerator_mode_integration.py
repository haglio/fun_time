"""The satellite side's origenerator mode, proven on a real session.

The unit tests prove the plans and ops; what they cannot prove is what the
room does with them — that the hosted app's parked window actually rises over
the RFB on the switch and parks again on the way back, that the two Funestras
really play what the hosted app hands them and get their own lists back
afterwards, and that the satellites hold their topmost band.  Those are claims
about real HWNDs and real Funestras, so they are tested by running a real
session against a STUB hosted app: a fabricated tkinter window that speaks
just enough of the ``--fun-time`` contract (the captions, the parked boot, the
Funestras it is handed, the QUIT verb) for the session to manage it.  A stub
rather than the real Origenerator because the suite must not touch the
machine's one ComfyUI, GPU queue, or gallery database — the same reason
``isolate_shared_resources`` strips the config key outright.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
from funestra.modes import MainMode, SatellitesMode
from funestra.playlist import read_playlist
from funestra.satellite_hud import parse_hud
from PIL import Image
from shared_ui.preview import Preview, window_title

from fun_time.event_log import event_log_path
from fun_time.origenerator_switches import RfbSlot
from fun_time.process_tree import kill_process_tree
from fun_time.role_windows import find_origenerators_window
from fun_time.room_funestras import RoomFunestra
from fun_time.satellite_control import read_satellite_status
from fun_time.shared_state import (
    read_shared_state,
    shared_state_path,
    write_shared_state,
)
from fun_time.win32 import (
    describe_windows,
    is_window_minimized,
    is_window_topmost,
    iter_zorder,
    set_always_on_top,
    windows_obscuring,
)
from fun_time.window_roles import ORIGENERATOR_TITLE
from fun_time.windows_bridge_orchestrator import (
    ORIGENERATOR_BOOT_BUDGET_S,
    _fix_post_loading_windows,
)
from fun_time.windows_bridge_sequencer import StartupResult
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

from .integration_support import (
    COMMAND_BUDGET_S,
    QUIT_BUDGET_S,
    START_BUDGET_S,
    FunTimeIntegrationSession,
    await_window,
    build_integration_config,
    build_integration_temp_root,
    seen_within,
    wait_for,
)

pytestmark = [
    # Real Funestras on a real desktop: the post-loading pass has to be given
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
# main window, parked (iconified) and topmost — and exits on QUIT.  It
# publishes the status file too, from the same poll and only once booted, which
# is what the real app's Fun Time bridge does and what a session reads to learn
# the app is up: a stub that stayed silent there would leave origenerator mode
# closed for the whole run.  Its shows are the real app's shape too: a list
# written for each Funestra it was handed, the verb that makes that Funestra read
# it, and a panel for the session to put on the Funestra.  Launched without
# --fun-time it opens on its own, as he opens the real one, offers itself to a
# session and answers a takeover -- holding its first status back afterwards,
# as a busy app would, so a session that waited to hear from an app already
# open would show the mode closed.
_STUB_MAIN = textwrap.dedent(
    """
    import argparse
    import ctypes
    import json
    import os
    import time
    import tkinter as tk
    from ctypes import wintypes
    from pathlib import Path

    from funestra.file_channel import (
        append_command,
        consume_command_file,
        publish_whole,
    )
    from funestra.playlist import PlaylistItem, write_playlist
    from funestra.satellite_hud import HudModel, hud_text

    SIDES = ("portrait", "landscape")
    PICTURES = Path(__file__).resolve().parent.parent / "pictures"

    parser = argparse.ArgumentParser()
    parser.add_argument("--fun-time", action="store_true")
    parser.add_argument("--showing", action="store_true")
    parser.add_argument("--caption", default="Origenerator")
    for flag in ("--x", "--y", "--width", "--height"):
        parser.add_argument(flag, type=int, default=0)
    parser.add_argument("--command-file")
    parser.add_argument("--status-file")
    for side in SIDES:
        for name in ("playlist", "cmd-file", "status-file", "hud-file"):
            parser.add_argument(f"--{side}-{name}")
    args, _rest = parser.parse_known_args()

    root = tk.Tk()
    root.withdraw()  # the main window arrives only after the "boot"

    booted = False
    taken_over = False
    state_dir = Path(__file__).resolve().parent.parent / "state"
    offer = state_dir / "fun_time_offer.txt"
    showing = state_dir / "fun_time_showing.txt"
    showing_a_slideshow = args.showing
    takeover = state_dir / "fun_time_takeover.json"

    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    def pin_to_the_named_rect():
        # By the OUTER rect, the way the real app pins its frameless window: a
        # frame spilling past the rect it was named sits over a Funestra.
        user32.SetWindowPos(int(root.wm_frame(), 16), None, args.x, args.y,
                            max(args.width, 120), max(args.height, 80),
                            0x0014)  # SWP_NOZORDER | SWP_NOACTIVATE

    def park_as_hosted():
        global booted
        root.geometry(
            f"{max(args.width, 120)}x{max(args.height, 80)}+{args.x}+{args.y}")
        root.attributes("-topmost", True)
        root.deiconify()
        root.update_idletasks()
        pin_to_the_named_rect()
        root.iconify()  # boots parked, like the real app
        booted = True  # only now does the real app's bridge start publishing

    held = set()

    def handed(side, name):
        value = getattr(args, f"{side}_{name}")
        return Path(value) if value else None

    def this_process_created_at():
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4)
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel32.GetProcessTimes(kernel32.GetCurrentProcess(),
                                 *[ctypes.byref(t) for t in times])
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def answer_a_takeover():
        global args, taken_over, showing_a_slideshow
        if booted or not takeover.exists():
            return
        try:
            asked = json.loads(takeover.read_text(encoding="utf-8"))
            takeover.unlink()
        except (OSError, ValueError):
            return
        if asked.get("pid") != os.getpid():
            return
        offer.unlink(missing_ok=True)
        showing.unlink(missing_ok=True)
        showing_a_slideshow = False
        args, _unused = parser.parse_known_args(asked["args"])
        taken_over = True
        park_as_hosted()

    def offer_itself():
        state_dir.mkdir(parents=True, exist_ok=True)
        offered = f"{os.getpid()} {this_process_created_at()}"
        offer.write_text(offered, encoding="utf-8")
        if showing_a_slideshow:
            showing.write_text(offered, encoding="utf-8")

    def go_back_to_standalone():
        global booted
        close_shows()
        root.attributes("-topmost", False)
        root.geometry("640x480+40+40")
        root.deiconify()
        booted = False
        offer_itself()

    if args.fun_time:
        splash = tk.Toplevel(root)
        splash.title("Origenerator")  # the caption twin the session must survive
        splash.geometry("200x80+10+10")

        def finish_boot():
            splash.destroy()
            root.title("Origenerator")
            park_as_hosted()

        root.after(3000, finish_boot)
    else:
        # Kept through a takeover, as the real app keeps a preview's.
        root.title(args.caption)
        root.geometry("640x480+40+40")
        root.deiconify()
        offer_itself()

    def note(line):
        # In the session's own state dir, which a failed run keeps: a dropped
        # verb or a refused write shows in no other file.
        where = Path(args.status_file).parent if args.status_file else state_dir
        where.mkdir(parents=True, exist_ok=True)
        with (where / "origenerator_stub.log").open("a", encoding="utf-8") as handle:
            print(f"{time.time():.3f} {line}", file=handle)

    def open_show(side):
        playlist = handed(side, "playlist")
        if side in held or playlist is None:
            return
        write_playlist(playlist, [PlaylistItem(PICTURES / f"{side}.png")])
        queued = append_command(handed(side, "cmd_file"), "RELOAD_PLAYLIST")
        note(f"{side}: list written, reload queued={queued}")
        publish_whole(handed(side, "hud_file"), hud_text(
            HudModel(funestra=side, lock_label=f"Stub {side} show")))
        held.add(side)

    def close_show(side):
        if side in held:
            publish_whole(handed(side, "hud_file"), "")
            held.discard(side)

    def close_shows():
        for side in list(held):
            close_show(side)

    def publish_status():
        if not args.status_file or not booted or taken_over:
            return
        lines = []
        for side in SIDES:
            lines.append(f"{side}_active={'1' if side in held else '0'}")
            lines.append(f"{side}_video=")
            lines.append(f"{side}_locked=0")
        Path(args.status_file).write_text(
            "".join(f"{line}\\n" for line in lines), encoding="utf-8")

    def poll():
        answer_a_takeover()
        command_file = Path(args.command_file) if args.command_file else None
        if command_file is not None:
            # Claimed by rename, like every Funestra's: read-then-unlink destroys
            # a verb the session appends between the two, and an open show lost
            # that way reads as a Funestra that never took the app's list.
            verbs = consume_command_file(command_file)
            if "QUIT" in verbs:
                root.destroy()
                return
            for side in SIDES:
                if f"{side}_open_show".upper() in verbs:
                    open_show(side)
                if f"{side}_close_show".upper() in verbs:
                    close_show(side)
            if "CLOSE_SHOWS" in verbs:
                close_shows()
            if "RELEASE" in verbs:
                go_back_to_standalone()
        publish_status()
        root.after(150, poll)

    root.after(150, poll)
    root.mainloop()
    """
)

_FUNESTRAS = (RoomFunestra.PORTRAIT, RoomFunestra.LANDSCAPE)
_A_START_WITH_EVERY_CORE_BUSY_S = 2 * START_BUDGET_S
# The session that comes back to the mode holds its own startup open for the
# hosted app, so its start is that much longer than any other test's.
_A_START_THAT_WAITS_FOR_THE_APP_S = (
    _A_START_WITH_EVERY_CORE_BUSY_S + ORIGENERATOR_BOOT_BUDGET_S)
# Every wait the two-session check makes, at its own budget: both starts and
# both Funestras' pictures in the second one.
_TWO_SESSIONS_AND_EVERY_WAIT_THEY_MAKE_S = (
    _A_START_WITH_EVERY_CORE_BUSY_S + _A_START_THAT_WAITS_FOR_THE_APP_S
    + 2 * COMMAND_BUDGET_S)
_A_SESSION_A_QUIT_AND_FIVE_WAITS_S = (
    _A_START_WITH_EVERY_CORE_BUSY_S + QUIT_BUDGET_S + 5 * COMMAND_BUDGET_S)
_THAT_AND_THE_SHARED_SESSIONS_QUIT_S = _A_SESSION_A_QUIT_AND_FIVE_WAITS_S + QUIT_BUDGET_S
_FUNESTRA_TITLES = (SATELLITE_PORTRAIT_TITLE, SATELLITE_LANDSCAPE_TITLE)


def _write_stub_checkout(destination: Path) -> Path:
    package = destination / "origenerator"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(_STUB_MAIN, encoding="utf-8")
    pictures = destination / "pictures"
    pictures.mkdir(exist_ok=True)
    for funestra, color in zip(_FUNESTRAS, ((40, 90, 160), (160, 90, 40)), strict=True):
        Image.new("RGB", (320, 240), color).save(pictures / f"{funestra.label}.png")
    return destination


def _host_stub(config_path: Path, stub_root: Path) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["paths"]["origenerator_dir"] = str(stub_root)
    # The stub is plain tkinter over funestra, so the suite's own
    # interpreter runs it.
    raw["paths"]["origenerator_python_exe"] = sys.executable
    config_path.write_text(json.dumps(raw), encoding="utf-8")


def _parked_main_window(pid: int) -> int:
    hwnd = find_origenerators_window(pid)
    return hwnd if hwnd and is_window_minimized(hwnd) else 0


@pytest.fixture(scope="module")
def hosted_session():
    """A real session hosting the stub app, plus that app's main HWND.

    One session for the whole module: launching a session is the expensive
    half of these tests, and every extra launch is GPU and decode churn the
    suite's perf-gated tests downstream then pay for.  The tests leave the
    session the way they found it (kino mode, satellites banded).
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
        hwnd = seen_within(lambda: _parked_main_window(pid), seconds=20.0)
        stderr_file = session.config.paths.state_dir / "orchestrator_stderr.log"
        stderr_tail = stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:] \
            if stderr_file.exists() else "(no stderr file)"
        assert hwnd, f"the stub's parked main window never appeared; stderr: {stderr_tail}"
        yield session, hwnd
    finally:
        session.stop()


def _playing(session, funestra: RoomFunestra) -> str:
    return read_satellite_status(session.config.satellite(funestra).status_file).video


def _shows_the_stub(session, funestra: RoomFunestra) -> bool:
    return Path(_playing(session, funestra)).name == f"{funestra.label}.png"


def _own_clips(session, funestra: RoomFunestra) -> list[Path]:
    """What *Funestra*'s playlist holds, once that is the session's own clips
    again rather than anything the hosted app wrote there."""
    folders = (session.config.paths.portrait_dirs if funestra is RoomFunestra.PORTRAIT
               else session.config.paths.landscape_dirs)
    listed = [item.path for item in read_playlist(session.config.satellite(funestra).playlist_file)]
    if listed and all(path.parent in folders for path in listed):
        return listed
    return []


def _wait_for_the_shows(session) -> None:
    """Wait for the hosted app to have put its picture on both Funestras."""
    for funestra in _FUNESTRAS:
        wait_for(lambda funestra=funestra: _shows_the_stub(session, funestra),
                 desc=f"the {funestra.label} Funestra to show the hosted app's picture")


def _bring_the_satellites_home(session) -> None:
    """Press each satellite's Kino once the app has both Funestras, and wait for
    them home.

    The wait below cannot tell a Funestra handed its list back from one the app
    never took, so the press has to come after the app has answered the open
    the way in sent it: pressed before that, the app writes its lists onto the
    Funestras after this test has ended, and the next one reads a playlist
    holding the app's picture (tests/test_origenerator_mode_way_back.py).
    """
    _wait_for_the_shows(session)
    for funestra in _FUNESTRAS:
        session.write_dashboard_command(f"{funestra.label}_kino_activate")
    for funestra in _FUNESTRAS:
        wait_for(lambda funestra=funestra: _own_clips(session, funestra)
                 and not _shows_the_stub(session, funestra),
                 desc=f"the {funestra.label} Funestra to play its own clips again")


def _press_once_origenerator_is_up(session, *commands: str) -> Path:
    """Press *commands* once the hosted app is up to answer them: the room
    opens with the app booting on out of sight, and every Origenerator button
    is dim until it answers."""
    state_file = shared_state_path(session.config.paths.state_dir)
    wait_for(lambda: read_shared_state(state_file).origenerator_ready,
             timeout=START_BUDGET_S, desc="the hosted app to publish a status")
    for command in commands:
        session.write_dashboard_command(command)
    return state_file


def _put_origenerator_on_the_satellites(session) -> None:
    state_file = _press_once_origenerator_is_up(
        session, *(f"{funestra.label}_origenerator_activate" for funestra in _FUNESTRAS))
    wait_for(lambda: _both_in_origenerator_mode(read_shared_state(state_file)),
             desc="both satellites to enter their Origenerator mode")


def _both_in_origenerator_mode(state) -> bool:
    return all(state.satellite(funestra).mode is SatellitesMode.ORIGENERATOR
               for funestra in _FUNESTRAS)


def _put_origenerator_in_the_browsers_place(session) -> None:
    state_file = _press_once_origenerator_is_up(session, "origenerator_activate")
    wait_for(lambda: read_shared_state(state_file).rfb_slot is RfbSlot.ORIGENERATOR,
             desc="Origenerator to take the Random Favs Browser's place")


def test_the_strip_raises_the_parked_window_and_the_browsers_button_parks_it(hosted_session):
    """The user-visible contract of the strip under the dashboard's log, on real
    windows: the hosted app boots parked; its Origenerator restores it over the
    RFB's rect and into the topmost band; its Random Favs Browser parks it
    again.  Driven through an OmniPause cycle first, because that is the
    sequence the demo failed in — the pause demotes every managed window, and
    the switch afterwards has to promote this one back itself."""
    session, hwnd = hosted_session
    assert is_window_minimized(hwnd)  # booted parked

    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: entering")
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: leaving")

    _put_origenerator_in_the_browsers_place(session)
    wait_for(lambda: not is_window_minimized(hwnd),
             desc="the hosted window to be restored")
    wait_for(lambda: is_window_topmost(hwnd),
             desc="the hosted window to join the topmost band")

    session.write_dashboard_command("rfb_activate")
    session.wait_for_new_log("The Random Favs Browser back in its place")
    wait_for(lambda: is_window_minimized(hwnd),
             desc="the hosted window to park again")


def test_the_funestras_play_the_hosted_apps_shows_and_come_back_to_their_own(hosted_session):
    """What the mode is for, on the real Funestras: each one plays the list the
    hosted app writes for it, and leaving the mode hands it back the session's
    own list — the same clips, not the app's picture and not an empty list."""
    session, _hwnd = hosted_session
    own_lists = {funestra: sorted(_own_clips(session, funestra)) for funestra in _FUNESTRAS}
    assert all(own_lists.values()), "the Funestras had no lists of their own to begin with"

    _put_origenerator_on_the_satellites(session)
    _wait_for_the_shows(session)

    _bring_the_satellites_home(session)
    for funestra in _FUNESTRAS:
        assert sorted(_own_clips(session, funestra)) == own_lists[funestra]


def test_one_satellite_plays_the_hosted_apps_show_while_the_other_and_the_browser_stay_put(
        hosted_session):
    session, hwnd = hosted_session
    portrait, landscape = _FUNESTRAS
    landscape_own = sorted(_own_clips(session, landscape))
    assert landscape_own, "the Landscape Funestra had no list of its own to begin with"

    state_file = _press_once_origenerator_is_up(
        session, f"{portrait.label}_origenerator_activate")
    wait_for(lambda: _shows_the_stub(session, portrait),
             desc="the Portrait Funestra to show the hosted app's picture")

    state = read_shared_state(state_file)
    assert state.satellite(landscape).mode is SatellitesMode.KINO
    assert state.rfb_slot is RfbSlot.RFB
    assert is_window_minimized(hwnd)
    assert not _shows_the_stub(session, landscape)
    assert sorted(_own_clips(session, landscape)) == landscape_own

    session.write_dashboard_command(f"{portrait.label}_kino_activate")
    wait_for(lambda: _own_clips(session, portrait) and not _shows_the_stub(session, portrait),
             desc="the Portrait Funestra to play its own clips again")


def test_a_switch_clicked_while_paused_hands_the_funestras_over_and_the_resume_bands_the_window(
        hosted_session):
    session, hwnd = hosted_session
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_new_log("OmniPause: entering")

    _put_origenerator_on_the_satellites(session)
    _put_origenerator_in_the_browsers_place(session)
    _wait_for_the_shows(session)

    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_new_log("OmniPause: leaving")
    wait_for(lambda: is_window_topmost(hwnd),
             desc="the hosted window to join the topmost band once the pause lifts")

    _bring_the_satellites_home(session)
    session.write_dashboard_command("rfb_activate")
    wait_for(lambda: is_window_minimized(hwnd), desc="the hosted window to park again")


def test_the_post_overlay_pass_rebands_satellites_recorded_under_shim_pids(hosted_session):
    """The demo's 'Landscape Funestra under other windows': the post-overlay
    pass resolved the satellites by pid, python_exe is the venv's pythonw
    shim, and both lookups found nothing — so the pass silently skipped the
    only banding the satellites get on a loading-screen startup.  Reproduced
    here with the session's real recorded pids and real windows: demote both
    Funestras (what the overlay's teardown can leave), run the pass, and the
    title fallback must put both back in the band."""
    session, _hwnd = hosted_session
    pids = session.read_child_pids()
    portrait = await_window("Portrait AI Funestra", 10, exact=True)
    landscape = await_window("Landscape AI Funestra", 10, exact=True)
    assert portrait and landscape

    # Every band read here is waited for rather than taken once: a Funestra
    # reloading its own list after a mode switch takes no messages for a
    # moment, and a SetWindowPos that timed out on it is not dropped -- it
    # lands when that Funestra pumps again.
    set_always_on_top(portrait, False)
    set_always_on_top(landscape, False)
    for funestra, hwnd in (("portrait", portrait), ("landscape", landscape)):
        wait_for(lambda hwnd=hwnd: not is_window_topmost(hwnd),
                 desc=f"the {funestra} Funestra to leave the topmost band")

    _fix_post_loading_windows(StartupResult(
        main_funestra_pid=pids["main_funestra_pid"],
        portrait_pid=pids["portrait_pid"],
        landscape_pid=pids["landscape_pid"],
        dashboard_pid=0,
        audio_pid=0,
        main_mode=MainMode.KINO,
    ))

    for funestra, hwnd in (("portrait", portrait), ("landscape", landscape)):
        wait_for(lambda hwnd=hwnd: is_window_topmost(hwnd),
                 desc=f"the {funestra} Funestra to be put back in the topmost band")


@pytest.mark.timeout(_A_SESSION_A_QUIT_AND_FIVE_WAITS_S)
def test_entering_the_mode_on_a_real_session_leaves_its_shows_on_top():
    """The one he kept reporting: the mode puts a picture on each side and then
    something buries it a few seconds later.

    A session of its own rather than the module's, because what buries a side
    is the settle pass and the bands a full launch lays down, so the mode has
    to be entered on a session that took the loading-screen path with every
    window real — and with the dashboard enabled, since that is what publishes
    the panels the Funestras wear.  Asked over ten seconds rather than once: the
    burial arrived a few seconds late every time he saw it, so a single look
    right after the switch is exactly the check that kept passing.
    """
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    overlay_env = {
        "FUN_TIME_INTEGRATION_OVERLAYS": "1",
        "FUN_TIME_DISABLE_DASHBOARD": "0",
    }
    try:
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S, env_overrides=overlay_env)
        _put_origenerator_on_the_satellites(session)
        events = event_log_path(session.config.paths.state_dir)
        text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
        assert "Loading screen launched" in text, (
            "the session did not take the loading-screen path, which is where "
            "the reveal and the settle pass live"
        )

        _wait_for_the_shows(session)
        for funestra in _FUNESTRAS:
            hud_file = session.config.satellite(funestra).hud_file
            worn = wait_for(
                lambda hud_file=hud_file, funestra=funestra: _panel_of(hud_file, funestra),
                desc=f"the {funestra.label} Funestra to wear the hosted app's panel")
            assert [button.command for button in worn.rows[0]][:2] == [
                f"{funestra.label}_kino_activate", f"{funestra.label}_origenerator_activate"]
            assert worn.rows[0][1].lit

        funestras = {title: await_window(title, 10, exact=True) for title in _FUNESTRA_TITLES}
        # Ten seconds of it, because the burial he saw landed seconds after the
        # picture did.  The first cover wins: report it and stop.
        # Each other is not a burial: the faked monitors are small enough that
        # the two Funestras' rects overlap here, which they never do on his.
        siblings = set(funestras.values())
        burial = seen_within(lambda: _first_burial(funestras, siblings), seconds=10.0, every_s=0.5)
        assert not burial, burial
    finally:
        session.stop()


def _first_burial(funestras: dict[str, int], siblings: set[int]) -> str:
    stack = iter_zorder()
    for title, hwnd in funestras.items():
        if covering := [w for w in windows_obscuring(hwnd, stack) if w.hwnd not in siblings]:
            return f"{title} (hwnd={hwnd}) was covered after the switch by: {describe_windows(covering)}"
    return ""


def _panel_of(hud_file: Path, funestra: RoomFunestra):
    """The panel published for *Funestra*, once it is the hosted app's own."""
    if not hud_file.exists():
        return None
    panel = parse_hud(hud_file.read_text(encoding="utf-8"))
    return panel if panel is not None and panel.lock_label == f"Stub {funestra.label} show" else None


@pytest.mark.timeout(_TWO_SESSIONS_AND_EVERY_WAIT_THEY_MAKE_S)
def test_a_room_left_in_the_mode_opens_in_it_as_it_was_left():
    """A room left in origenerator mode opens in it, with no switch pressed: the
    session holds its own cover up for the hosted app and takes the mode up under
    it, so it is already in the mode when the room is shown.

    A real session runs first because the state file and the playlists the next
    one reads are what that session leaves on disk -- a session opening on a state
    dir with neither builds fresh ones and carries nothing.  The mode goes into
    that file the way a session left in the mode leaves it; pressing the switch to
    get it there costs an app boot and a wait on it, and the test above is what
    covers the press.  That first session is ended the way a crash ends one, so it
    hands its Origenerator to nobody: handed back, the app goes standalone and the
    next session takes it over, which is the slowest way it can come up.
    """
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)

    first = FunTimeIntegrationSession(config_path)
    try:
        first.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)
    finally:
        first.stop()
    state_file = shared_state_path(first.config.paths.state_dir)
    left = replace(read_shared_state(state_file), rfb_slot=RfbSlot.ORIGENERATOR, max_intensity=38)
    for funestra in _FUNESTRAS:
        left = left.with_satellite(funestra, mode=SatellitesMode.ORIGENERATOR)
    write_shared_state(state_file, left)

    second = FunTimeIntegrationSession(config_path)
    try:
        second.start(wait_seconds=_A_START_THAT_WAITS_FOR_THE_APP_S)
        _wait_for_the_shows(second)
        opened_in = read_shared_state(state_file)
        assert _both_in_origenerator_mode(opened_in)
        assert opened_in.rfb_slot is RfbSlot.ORIGENERATOR
        assert opened_in.max_intensity == 38
    finally:
        second.stop()


@pytest.mark.timeout(_A_SESSION_A_QUIT_AND_FIVE_WAITS_S)
def test_an_origenerator_open_on_a_slideshow_is_taken_into_a_room_that_opens_in_its_mode():
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    open_app = subprocess.Popen(
        [sys.executable, str(stub_root / "origenerator" / "__main__.py"), "--showing"],
        cwd=str(stub_root))
    session = FunTimeIntegrationSession(config_path)
    try:
        wait_for((stub_root / "state" / "fun_time_showing.txt").exists,
                 desc="the open app to say it is showing a slideshow")
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)

        _wait_for_the_shows(session)
        state_file = shared_state_path(session.config.paths.state_dir)
        assert _both_in_origenerator_mode(read_shared_state(state_file))
        assert read_shared_state(state_file).rfb_slot is RfbSlot.ORIGENERATOR
    finally:
        session.stop()
        kill_process_tree(open_app.pid)


@pytest.mark.timeout(_THAT_AND_THE_SHARED_SESSIONS_QUIT_S)
def test_an_origenerator_already_open_is_taken_into_the_session_rather_than_doubled():
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    # By path rather than -m: a session's start reaps every `-m origenerator`
    # an earlier run left on this desktop, and this one is meant to be open.
    # Open as a preview, the copy he most often has open when a room starts.
    open_app = subprocess.Popen(
        [sys.executable, str(stub_root / "origenerator" / "__main__.py"), "--caption",
         window_title(ORIGENERATOR_TITLE, Preview(feature="a fabricated feature"))],
        cwd=str(stub_root))
    session = FunTimeIntegrationSession(config_path)
    try:
        offer = stub_root / "state" / "fun_time_offer.txt"
        # The pid the app names for itself, not the Popen's: a venv's python.exe
        # is a launcher, and the app is the interpreter it starts.
        offered_pid = int(wait_for(lambda: offer.exists() and offer.read_text(
            encoding="utf-8").split(), desc="the open app to offer itself")[0])
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)

        assert session.read_child_pids().get("origenerator_pid") == offered_pid
        state_file = shared_state_path(session.config.paths.state_dir)
        wait_for(lambda: read_shared_state(state_file).origenerator_ready, timeout=5,
                 desc="the mode to open without waiting to hear from the app")
        hwnd = wait_for(lambda: _parked_main_window(offered_pid),
                        desc="the open app's window to be parked by the takeover")
        session.write_dashboard_command("origenerator_activate")
        session.wait_for_log("Origenerator in the Random Favs Browser's place")
        wait_for(lambda: not is_window_minimized(hwnd),
                 desc="the taken-over window to be restored")
        wait_for(lambda: is_window_topmost(hwnd),
                 desc="the taken-over window to join the topmost band")

        session.quit_gracefully()

        wait_for(offer.exists, desc="the handed-back app to offer itself again")
        assert open_app.poll() is None, "the session closed the app it was meant to hand back"
        # Waited for, not read once: the app offers itself again as it comes
        # back, and the window manager takes it out of the band and out of the
        # taskbar in its own time after that.
        wait_for(lambda: not is_window_minimized(hwnd),
                 desc="the handed-back window to stand open again")
        wait_for(lambda: not is_window_topmost(hwnd),
                 desc="the handed-back window to leave the topmost band")
    finally:
        session.stop()
        kill_process_tree(open_app.pid)
