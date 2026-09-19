"""The satellite side's origenerator mode, proven on a real session.

The unit tests prove the plans and ops; what they cannot prove is what the
room does with them — that the hosted app's parked window actually rises over
the RFB on the switch and parks again on the way back, that the two players
really play what the hosted app hands them and get their own lists back
afterwards, and that the satellites hold their topmost band.  Those are claims
about real HWNDs and real players, so they are tested by running a real
session against a STUB hosted app: a fabricated tkinter window that speaks
just enough of the ``--fun-time`` contract (the captions, the parked boot, the
players it is handed, the QUIT verb) for the session to manage it.  A stub
rather than the real Origenerator because the suite must not touch the
machine's one ComfyUI, GPU queue, or gallery database — the same reason
``isolate_shared_resources`` strips the config key outright.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from PIL import Image
from player_core.modes import MainMode
from player_core.playlist import read_playlist
from player_core.satellite_hud import parse_hud

from fun_time.event_log import event_log_path
from fun_time.players import Player
from fun_time.satellite_control import read_satellite_status
from fun_time.shared_state import (
    read_shared_state,
    shared_state_path,
)
from fun_time.win32 import (
    find_window_for_process,
    is_window_minimized,
    is_window_topmost,
    iter_zorder,
    set_always_on_top,
    wait_for_window_by_title,
    windows_obscuring,
)
from fun_time.windows_bridge_orchestrator import _fix_post_loading_windows, kill_process_tree
from fun_time.windows_bridge_sequencer import StartupResult
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    retire_temp_root,
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
# main window, parked (iconified) and topmost — and exits on QUIT.  It
# publishes the status file too, from the same poll and only once booted, which
# is what the real app's Fun Time bridge does and what a session reads to learn
# the app is up: a stub that stayed silent there would leave origenerator mode
# closed for the whole run.  Its shows are the real app's shape too: a list
# written for each player it was handed, the verb that makes that player read
# it, and a panel for the session to put on the player.  Launched without
# --fun-time it opens on its own, as he opens the real one, offers itself to a
# session and answers a takeover.
_STUB_MAIN = textwrap.dedent(
    """
    import argparse
    import ctypes
    import json
    import os
    import tkinter as tk
    from ctypes import wintypes
    from pathlib import Path

    from player_core.file_channel import append_command, publish_whole
    from player_core.playlist import PlaylistItem, write_playlist
    from player_core.satellite_hud import HudModel, hud_text

    SIDES = ("portrait", "landscape")
    PICTURES = Path(__file__).resolve().parent.parent / "pictures"

    parser = argparse.ArgumentParser()
    parser.add_argument("--fun-time", action="store_true")
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
    state_dir = Path(__file__).resolve().parent.parent / "state"
    offer = state_dir / "fun_time_offer.txt"
    takeover = state_dir / "fun_time_takeover.json"

    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    def pin_to_the_named_rect():
        # By the OUTER rect, the way the real app pins its frameless window: a
        # frame spilling past the rect it was named sits over a player.
        user32.SetWindowPos(int(root.wm_frame(), 16), None, args.x, args.y,
                            max(args.width, 120), max(args.height, 80),
                            0x0014)  # SWP_NOZORDER | SWP_NOACTIVATE

    def park_as_hosted():
        global booted
        root.title("Origenerator")
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
        global args
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
        args, _unused = parser.parse_known_args(asked["args"])
        park_as_hosted()

    def offer_itself():
        state_dir.mkdir(parents=True, exist_ok=True)
        offer.write_text(f"{os.getpid()} {this_process_created_at()}", encoding="utf-8")

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
            park_as_hosted()

        root.after(3000, finish_boot)
    else:
        root.title("Origenerator")
        root.geometry("640x480+40+40")
        root.deiconify()
        offer_itself()

    def open_shows():
        for side in SIDES:
            playlist = handed(side, "playlist")
            if side in held or playlist is None:
                continue
            write_playlist(playlist, [PlaylistItem(PICTURES / f"{side}.png")])
            append_command(handed(side, "cmd_file"), "RELOAD_PLAYLIST")
            publish_whole(handed(side, "hud_file"), hud_text(
                HudModel(player=side, lock_label=f"Stub {side} show")))
            held.add(side)

    def close_shows():
        for side in held:
            publish_whole(handed(side, "hud_file"), "")
        held.clear()

    def publish_status():
        if not args.status_file or not booted:
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
            if "RELEASE" in verbs:
                go_back_to_standalone()
        publish_status()
        root.after(150, poll)

    root.after(150, poll)
    root.mainloop()
    """
)

_PLAYERS = (Player.PORTRAIT, Player.LANDSCAPE)
_PLAYER_TITLES = (SATELLITE_PORTRAIT_TITLE, SATELLITE_LANDSCAPE_TITLE)


def _write_stub_checkout(destination: Path) -> Path:
    package = destination / "origenerator"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(_STUB_MAIN, encoding="utf-8")
    pictures = destination / "pictures"
    pictures.mkdir(exist_ok=True)
    for player, color in zip(_PLAYERS, ((40, 90, 160), (160, 90, 40)), strict=True):
        Image.new("RGB", (320, 240), color).save(pictures / f"{player.label}.png")
    return destination


def _host_stub(config_path: Path, stub_root: Path) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["paths"]["origenerator_dir"] = str(stub_root)
    # The stub is plain tkinter over player_core, so the suite's own
    # interpreter runs it.
    raw["paths"]["origenerator_python_exe"] = sys.executable
    config_path.write_text(json.dumps(raw), encoding="utf-8")


def _parked_main_window(pid: int) -> int:
    hwnd = find_window_for_process(pid, "Origenerator")
    return hwnd if hwnd and is_window_minimized(hwnd) else 0


@pytest.fixture(scope="module")
def hosted_session():
    """A real session hosting the stub app, plus that app's main HWND.

    One session for the whole module: launching a session is the expensive
    half of these tests, and every extra launch is GPU and decode churn the
    suite's perf-gated tests downstream then pay for.  The tests leave the
    session the way they found it (video mode, satellites banded).
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
        while not hwnd and time.monotonic() < deadline:
            hwnd = _parked_main_window(pid)
            time.sleep(0.2)
        stderr_file = session.config.paths.state_dir / "orchestrator_stderr.log"
        stderr_tail = stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:] \
            if stderr_file.exists() else "(no stderr file)"
        assert hwnd, f"the stub's parked main window never appeared; stderr: {stderr_tail}"
        yield session, hwnd
    finally:
        session.stop()
        retire_temp_root(temp_root)


def _wait(predicate, *, timeout: float, desc: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {desc} (last={last!r})")


def _playing(session, player: Player) -> str:
    return read_satellite_status(session.config.satellite(player).status_file).video


def _shows_the_stub(session, player: Player) -> bool:
    return Path(_playing(session, player)).name == f"{player.label}.png"


def _own_clips(session, player: Player) -> list[Path]:
    """What *player*'s playlist holds, once that is the session's own clips
    again rather than anything the hosted app wrote there."""
    folders = (session.config.paths.portrait_dirs if player is Player.PORTRAIT
               else session.config.paths.landscape_dirs)
    listed = [item.path for item in read_playlist(session.config.satellite(player).playlist_file)]
    if listed and all(path.parent in folders for path in listed):
        return listed
    return []


def _wait_for_the_shows(session, *, timeout: float = 20) -> None:
    """Wait for the hosted app to have put its picture on both players."""
    for player in _PLAYERS:
        _wait(lambda player=player: _shows_the_stub(session, player),
              timeout=timeout,
              desc=f"the {player.label} player to show the hosted app's picture")


def _leave_the_mode(session) -> None:
    """Press the way back once the app has both players, and wait for them home.

    The wait below cannot tell a player handed its list back from one the app
    never took, so the press has to come after the app has answered the
    OPEN_SHOWS the way in sent it: pressed before that, the app writes its
    lists onto the players after this test has ended, and the next one reads a
    playlist holding the app's picture (tests/test_origenerator_mode_way_back.py).
    """
    _wait_for_the_shows(session)
    session.write_dashboard_command("satellites_video_activate")
    for player in _PLAYERS:
        _wait(lambda player=player: _own_clips(session, player)
              and not _shows_the_stub(session, player),
              timeout=20, desc=f"the {player.label} player to play its own clips again")


def _enter_the_mode(session) -> None:
    """Press the mode button, once the hosted app is up to answer it."""
    state_file = shared_state_path(session.config.paths.state_dir)
    # The room opens in video mode and the hosted app boots on out of sight,
    # so the switch has to wait for it — pressed any earlier it is refused,
    # which is the whole point of the button being dim until then.
    _wait(lambda: read_shared_state(state_file).origenerator_ready,
          timeout=90, desc="the hosted app to publish a status")
    session.write_dashboard_command("origenerator_activate")
    _wait(lambda: read_shared_state(state_file).satellites_mode == "origenerator",
          timeout=30, desc="the session to enter origenerator mode")


def test_the_switch_raises_the_parked_window_and_the_way_back_parks_it(hosted_session):
    """The user-visible contract of the mode pair, on real windows: the hosted
    app boots parked; origenerator mode restores it over the RFB's rect and
    into the topmost band; video mode parks it again.  Driven through an
    OmniPause cycle first, because that is the sequence the demo failed in —
    the pause demotes every managed window, and the switch afterwards has to
    promote this one back itself."""
    session, hwnd = hosted_session
    assert is_window_minimized(hwnd)  # booted parked

    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: entering")
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: leaving")

    _enter_the_mode(session)
    session.wait_for_log("Satellites switched to origenerator mode")
    _wait(lambda: not is_window_minimized(hwnd),
          timeout=10, desc="the hosted window to be restored")
    _wait(lambda: is_window_topmost(hwnd),
          timeout=10, desc="the hosted window to join the topmost band")

    _leave_the_mode(session)
    session.wait_for_log("Satellites switched to video mode")
    _wait(lambda: is_window_minimized(hwnd),
          timeout=10, desc="the hosted window to park again")


def test_the_players_play_the_hosted_apps_shows_and_come_back_to_their_own(hosted_session):
    """What the mode is for, on the real players: each one plays the list the
    hosted app writes for it, and leaving the mode hands it back the session's
    own list — the same clips, not the app's picture and not an empty list."""
    session, _hwnd = hosted_session
    own_lists = {player: sorted(_own_clips(session, player)) for player in _PLAYERS}
    assert all(own_lists.values()), "the players had no lists of their own to begin with"

    _enter_the_mode(session)
    _wait_for_the_shows(session)

    _leave_the_mode(session)
    for player in _PLAYERS:
        assert sorted(_own_clips(session, player)) == own_lists[player]


def test_the_post_overlay_pass_rebands_satellites_recorded_under_shim_pids(hosted_session):
    """The demo's 'landscape player under other windows': the post-overlay
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
        main_player_pid=pids["main_player_pid"],
        portrait_pid=pids["portrait_pid"],
        landscape_pid=pids["landscape_pid"],
        dashboard_pid=0,
        genau_pid=pids["genau_pid"],
        audio_pid=0,
        main_mode=MainMode.VIDEO,
    ))

    assert is_window_topmost(portrait)
    assert is_window_topmost(landscape)


def test_entering_the_mode_on_a_real_session_leaves_its_shows_on_top():
    """The one he kept reporting: the mode puts a picture on each side and then
    something buries it a few seconds later.

    A session of its own rather than the module's, because what buries a side
    is the settle pass and the bands a full launch lays down, so the mode has
    to be entered on a session that took the loading-screen path with every
    window real — and with the dashboard enabled, since that is what publishes
    the panels the players wear.  Asked over ten seconds rather than once: the
    burial arrived a few seconds late every time he saw it, so a single look
    right after the switch is exactly the check that kept passing.
    """
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    # Faked side-by-side monitors for the same reason the loading-screen test
    # fakes them: on the hidden desktop's single screen the real layout
    # collapses every window onto it and the players legitimately overlap,
    # which makes "is this player frontmost over its rect" unanswerable.
    overlay_env = {
        "FUN_TIME_INTEGRATION_OVERLAYS": "1",
        "FUN_TIME_DISABLE_DASHBOARD": "0",
        "FUN_TIME_FAKE_MONITORS": "0,0,1280,720;1280,0,720,1440",
    }
    try:
        session.start(wait_seconds=180.0, env_overrides=overlay_env)
        _enter_the_mode(session)
        events = event_log_path(session.config.paths.state_dir)
        text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
        assert "Loading screen launched" in text, (
            "the session did not take the loading-screen path, which is where "
            "the reveal and the settle pass live"
        )

        _wait_for_the_shows(session, timeout=30)
        for player in _PLAYERS:
            hud_file = session.config.satellite(player).hud_file
            worn = _wait(
                lambda hud_file=hud_file, player=player: _panel_of(hud_file, player),
                timeout=10, desc=f"the {player.label} player to wear the hosted app's panel")
            assert [button.command for button in worn.rows[0]][:2] == [
                "satellites_video_activate", "origenerator_activate"]
            assert worn.rows[0][1].lit

        players = {title: wait_for_window_by_title(title, timeout_s=10, exact=True)
                   for title in _PLAYER_TITLES}
        # Ten seconds of it, because the burial he saw landed seconds after the
        # picture did.  The first cover wins: report it and stop.
        # Each other is not a burial: the faked monitors are small enough that
        # the two players' rects overlap here, which they never do on his.
        siblings = set(players.values())
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            stack = iter_zorder()
            for title, hwnd in players.items():
                covering = [w for w in windows_obscuring(hwnd, stack)
                            if w.hwnd not in siblings]
                assert not covering, (
                    f"{title} (hwnd={hwnd}) was covered after the switch by: "
                    + "; ".join(
                        f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}"
                        for w in covering
                    )
                )
            time.sleep(0.5)
    finally:
        session.stop()
        retire_temp_root(temp_root)


def _panel_of(hud_file: Path, player: Player):
    """The panel published for *player*, once it is the hosted app's own."""
    if not hud_file.exists():
        return None
    panel = parse_hud(hud_file.read_text(encoding="utf-8"))
    return panel if panel is not None and panel.lock_label == f"Stub {player.label} show" else None


def test_an_origenerator_already_open_is_taken_into_the_session_rather_than_doubled():
    temp_root = build_integration_temp_root()
    stub_root = _write_stub_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    # By path rather than -m: a session's start reaps every `-m origenerator`
    # an earlier run left on this desktop, and this one is meant to be open.
    open_app = subprocess.Popen(
        [sys.executable, str(stub_root / "origenerator" / "__main__.py")], cwd=str(stub_root))
    session = FunTimeIntegrationSession(config_path)
    try:
        offer = stub_root / "state" / "fun_time_offer.txt"
        # The pid the app names for itself, not the Popen's: a venv's python.exe
        # is a launcher, and the app is the interpreter it starts.
        offered_pid = int(_wait(lambda: offer.exists() and offer.read_text(
            encoding="utf-8").split(), timeout=20, desc="the open app to offer itself")[0])
        session.start()

        assert session.read_child_pids().get("origenerator_pid") == offered_pid
        hwnd = _wait(lambda: _parked_main_window(offered_pid),
                     timeout=20, desc="the open app's window to be parked by the takeover")
        session.write_dashboard_command("origenerator_activate")
        session.wait_for_log("Satellites switched to origenerator mode")
        _wait(lambda: not is_window_minimized(hwnd),
              timeout=10, desc="the taken-over window to be restored")
        _wait(lambda: is_window_topmost(hwnd),
              timeout=10, desc="the taken-over window to join the topmost band")

        session.quit_gracefully()

        _wait(offer.exists, timeout=10, desc="the handed-back app to offer itself again")
        assert open_app.poll() is None, "the session closed the app it was meant to hand back"
        # Waited for, not read once: the app offers itself again as it comes
        # back, and the window manager takes it out of the band and out of the
        # taskbar in its own time after that.
        _wait(lambda: not is_window_minimized(hwnd), timeout=10,
              desc="the handed-back window to stand open again")
        _wait(lambda: not is_window_topmost(hwnd), timeout=10,
              desc="the handed-back window to leave the topmost band")
    finally:
        session.stop()
        kill_process_tree(open_app.pid)
        retire_temp_root(temp_root)
