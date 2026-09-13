"""A session's whole lifecycle, from the first cover to the last child killed.

Runs the startup phases, launches the AHK hotkey script, starts the dispatch
loop, holds the session open until the hotkeys exit, then shuts every child
down.  Both ends of that happen under a cover over every monitor, so the
session's windows are never watched arriving or leaving one at a time.
"""
from __future__ import annotations

import configparser
import contextlib
import datetime
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from player_core.file_channel import append_command

from .config import load_config
from .event_log import EventLogHandler, start_event_log
from .hud_transport import HudPublisher
from .library_handles import build_library_handles
from .loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from .lock_hud import prime_group_indexes
from .loopback_server import ThreadingHTTPServer, serve_loopback
from .manifest import CommandFiles, LaunchManifest
from .modes import collect_video_files
from .overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    SHUTDOWN_PHASES,
    SHUTDOWN_PROGRESS_FILENAME,
    NullProgress,
    PhaseProgress,
    ProgressReporter,
    StartupCancelled,
    ready_file_for,
)
from .players import Player
from .process_identity import NAMER
from .role_windows import ChildPids, WindowRoles
from .runtime_flow import write_flag_file
from .satellites_mode import CLOSE_SHOWS, origenerator_shows
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .session_handoff import (
    HandoffTarget,
    crossing_progress_path,
    drop_crossing_cover,
    forget_the_kept_origenerator,
    keep_the_origenerator,
    kept_origenerator,
    launch_crossing_cover,
    pending_handoff,
    release_the_headset,
    returning_from_a_crossing,
)
from .shared_state import shared_state_path
from .shortcuts import Shortcut, resolve_shortcut
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, prewarm_thumbnails
from .voice_control import VOICE_AVAILABLE, VoiceController, voice_import_error
from .win32 import (
    close_window,
    find_window_by_pid,
    find_window_for_process,
    hide_window,
    iter_zorder,
    set_always_on_top,
    wait_for_window_by_title,
    windows_obscuring,
)
from .win32_process import get_process_creation_time
from .window_roles import ORIGENERATOR_ROLE_TITLES
from .windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from .windows_bridge_sequencer import (
    StartupResult,
    apply_startup_window_state,
    apply_topmost_bands,
    keep_the_cover_up,
    release_the_players,
    run_startup_sequence,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

logger = logging.getLogger(__name__)


# Every child a session launches, grouped the way teardown reports it to the
# closing screen.  The groups are the source of truth: a child added to one is
# recorded at startup and killed at shutdown by the same edit, so a child
# cannot quietly outlive the session.
_CHILD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("players", ("nau_pid", "portrait_pid", "landscape_pid")),
    ("companions", ("dashboard_pid", "genau_pid", "audio_pid", "origenerator_pid")),
)

_CHILD_PID_KEYS = tuple(key for _, keys in _CHILD_GROUPS for key in keys)


@dataclass(frozen=True)
class ChildProcess:
    """A child we launched, named by the pair that survives PID recycling.

    ``created_at`` is the process's creation FILETIME, read while the child was
    known to be alive.  A PID alone is not an identity — Windows hands a freed
    PID back out within seconds — so every deferred kill compares the recorded
    creation time against the live one first.  ``created_at`` is 0 for a child
    that was never launched (pid 0) or had already exited when it was recorded;
    no live process can match that, so it is never killed.
    """

    pid: int
    created_at: int


def identify_children(result: StartupResult) -> dict[str, ChildProcess]:
    """Pin each freshly-launched child PID to the process now holding it.

    Called seconds after launch, with startup having just driven the children
    (the satellites' status files appeared, as did Nau's and Genau's windows), so
    the creation time read here is the one our child was born with.  Everything that
    kills a child later compares against it, and a PID Windows has since handed
    to someone else no longer matches.

    A child that has already exited has no creation time to read; recording 0
    means nothing alive can ever match it, so it is never killed.
    """
    children: dict[str, ChildProcess] = {}
    for key in _CHILD_PID_KEYS:
        pid = getattr(result, key)
        children[key] = ChildProcess(pid=pid, created_at=get_process_creation_time(pid) or 0)
    return children


def write_pids_file(path: Path, children: dict[str, ChildProcess]) -> None:
    """Record this session's children so its teardown can find them again.

    Both sections are keyed by child name: ``[pids]`` alone cannot be trusted at
    kill time, so ``[created_at]`` carries the creation time that pins each PID
    to the process we launched.
    """
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["pids"] = {key: str(child.pid) for key, child in children.items()}
    parser["created_at"] = {key: str(child.created_at) for key, child in children.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


SESSION_END_MARKER = "session_end.txt"


def _describe_session_end(state_dir: Path, exit_code: int) -> str:
    """In one phrase, what ended the session -- read from the marker the quit
    chord leaves, then removed so the next session starts with none.

    An unexpected end is worth saying loudly precisely because everything else
    about it looks ordinary: the same closing screen, the same exit code, the
    same "AHK exited" line.  Without this the only evidence a session died on
    its own was the user noticing it was gone.
    """
    marker = state_dir / SESSION_END_MARKER
    try:
        reason = marker.read_text(encoding="utf-8").strip()
    except OSError:
        reason = ""
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        # Something else is at that path, or holding it.  The line below is
        # still worth logging, and a shutdown never fails over a note.
        logger.warning("Could not clear %s", marker, exc_info=True)
    if reason:
        return f"asked to end: {reason}"
    if exit_code:
        return "the hotkey script FAILED -- see its own MsgBox/exit path"
    return "UNEXPECTED -- nothing asked it to end; the hotkey script exited on its own"


def kill_recorded_child(child: ChildProcess) -> None:
    """Kill *child* and its descendants, but only if its PID still names it."""
    if not child.pid:
        return
    created_at = get_process_creation_time(child.pid)
    if created_at is None:
        logger.info("Not killing PID %d: process already exited", child.pid)
        return
    if created_at != child.created_at:
        logger.warning(
            "Not killing PID %d: Windows recycled it to a process created at %d, "
            "not the child we launched at %d",
            child.pid, created_at, child.created_at,
        )
        return
    kill_process_tree(child.pid)


def kill_process_tree(pid: int) -> None:
    """Kill *pid* and its descendants via ``taskkill /T /F``.

    Unconditional.  A bare PID is evidence of nothing — Windows hands freed PIDs
    straight back out — so the caller must first establish that *pid* is theirs
    to kill: kill_recorded_child() checks the recorded creation time, and the
    integration reap checks the image name of a window it found on its own
    desktop.
    """
    if not pid:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    except OSError:
        pass


def _close_origenerator_gracefully(child: ChildProcess | None) -> None:
    """WM_CLOSE the hosted Origenerator and give its close a moment to finish.

    Its closeEvent is where the session persists and the absence experiments are
    handed to ComfyUI — a taskkill loses both.  A hanging close falls through to
    the companions sweep, which kills the tree.
    """
    if child is None or not child.pid:
        return
    hwnd = find_window_for_process(child.pid, "Origenerator", include_hidden=True)
    if not hwnd:
        return
    close_window(hwnd)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if get_process_creation_time(child.pid) != child.created_at:
            return  # exited (or the pid was never ours) — nothing left to wait on
        time.sleep(0.2)
    logger.warning("Origenerator did not close within 5s; the kill sweep takes it")


def _park_the_hosted_origenerator(
    state_dir: Path, child: ChildProcess | None, command_file: Path,
) -> bool:
    """Park the hosted app for the arriving session; whether it took it."""
    if child is None or not child.pid:
        return False
    hwnd = find_window_for_process(child.pid, "Origenerator", include_hidden=True)
    if not hwnd:
        return False
    append_command(command_file, CLOSE_SHOWS)
    hide_window(hwnd)
    keep_the_origenerator(state_dir, pid=child.pid, created_at=child.created_at)
    logger.info("Leaving the hosted Origenerator running (pid=%d)", child.pid)
    return True


def close_a_kept_origenerator(state_dir: Path) -> None:
    """Close a hosted app a crossing left running that nothing will adopt."""
    kept = kept_origenerator(state_dir)
    forget_the_kept_origenerator(state_dir)
    if kept is None:
        return
    pid, created_at = kept
    if get_process_creation_time(pid) != created_at:
        return  # gone already, or that pid is somebody else's now
    child = ChildProcess(pid=pid, created_at=created_at)
    _close_origenerator_gracefully(child)
    kill_recorded_child(child)


def _shutdown_children(
    rfb_hwnd: int,
    children: dict[str, ChildProcess],
    progress: ProgressReporter,
    *,
    state_dir: Path,
    keep_origenerator_via: Path | None = None,
) -> None:
    """Kill all child processes launched during startup.

    Reports each group as it starts, so the closing screen can say which windows
    are on their way out — and, if a kill wedges, which one it wedged on.
    *keep_origenerator_via*, the hosted app's command file, parks it for the
    session crossing in after this one rather than closing it (docs/entering-vr.md).
    """
    progress.advance("browser")
    close_window(rfb_hwnd)
    kept = keep_origenerator_via is not None and _park_the_hosted_origenerator(
        state_dir, children.get("origenerator_pid"), keep_origenerator_via)
    if not kept:
        forget_the_kept_origenerator(state_dir)
        _close_origenerator_gracefully(children.get("origenerator_pid"))
    for phase, keys in _CHILD_GROUPS:
        progress.advance(phase)
        for key in keys:
            if kept and key == "origenerator_pid":
                continue
            kill_recorded_child(children[key])


# A fresh python + tkinter process is up in well under a second; the rest is
# slack for a loaded machine, and a ceiling nobody normally pays.
CLOSING_SCREEN_READY_TIMEOUT_S = 5.0


def _wait_for_closing_screen(ready_file: Path, proc: subprocess.Popen) -> None:
    """Block until the cover is painted over every monitor.  Two ways out
    besides the flag: the screen died, so none is coming; or waiting has come to
    cost more than the flicker would."""
    deadline = time.monotonic() + CLOSING_SCREEN_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if ready_file.exists():
            return
        if proc.poll() is not None:
            logger.warning("Closing screen exited before it was ready")
            return
        time.sleep(0.05)
    logger.warning(
        "Closing screen not ready after %.1fs; closing anyway",
        CLOSING_SCREEN_READY_TIMEOUT_S,
    )


@contextlib.contextmanager
def _closing_screen(
    state_dir: Path, *, enabled: bool, crossing: HandoffTarget | None = None,
) -> Iterator[ProgressReporter]:
    """Cover every monitor while the session comes down, then uncover it.

    Yields the reporter the teardown steps report through: up and painted before
    the body runs, down once it has finished.  Off for an integration run.

    *crossing* is the session this one is ending FOR: the cover then says so,
    and is left standing for the arriving session (docs/entering-vr.md).
    """
    if not enabled:
        yield NullProgress()
        return

    progress_file = (
        crossing_progress_path(state_dir) if crossing is not None
        else state_dir / SHUTDOWN_PROGRESS_FILENAME
    )
    ready_file = ready_file_for(progress_file)
    # A flag left by a previous session would let this teardown start with
    # nothing yet covering the screen.
    ready_file.unlink(missing_ok=True)
    if crossing is not None:
        progress: ProgressReporter = NullProgress()  # the wording is the crossing's
        proc = launch_crossing_cover(state_dir, crossing)
    else:
        progress = PhaseProgress(progress_file, phases=SHUTDOWN_PHASES)
        # Written before the screen is launched so it has something to read from
        # its first poll, and so its staleness clock starts here, not never.
        progress.advance("controls")
        proc = subprocess.Popen([
            NAMER.named_exe(sys.executable, "ClosingScreen"),
            "-m", "fun_time.closing_screen", str(progress_file),
        ])
    logger.info("Teardown cover launched (pid=%d)", proc.pid)
    _wait_for_closing_screen(ready_file, proc)
    try:
        yield progress
    finally:
        if crossing is None:
            progress.finish()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                logger.warning("Closing screen did not exit, killed")
            progress_file.unlink(missing_ok=True)
            ready_file.unlink(missing_ok=True)
        else:
            logger.info("Leaving the cover up for %s", crossing.app_name)


# Cancelling from the loading screen is a clean, user-initiated exit.
_CANCELLED_EXIT_CODE = 0


def stop_hotkey_script(proc: subprocess.Popen, ahk_cmd_file: Path) -> None:
    """Bring the hotkey script down through its own mailbox, then insist.

    ``exit`` is what every other end of a session uses, and it lets AHK release
    its keyboard hook on the way out.  One that ignored it would outlive the
    launch it was hooked into and go on swallowing every key it binds — Esc
    above all — with nothing left to hand them to.
    """
    try:
        ahk_cmd_file.write_text("exit", encoding="utf-8")
    except OSError:
        logger.warning("Could not ask the hotkey script to exit", exc_info=True)
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("Hotkey script did not exit after cancel, killed")


def _take_down_the_startup(
    reason: str,
    *,
    pids: list[int],
    rfb_hwnd: int,
    cover: _Cover,
    ahk_proc: subprocess.Popen,
    ahk_cmd_file: Path,
) -> int:
    """Tear down a startup that is not becoming a session, then exit.

    Kills every child so far and closes the browser *before* the overlay comes
    down; these were launched seconds ago, so their PIDs are still theirs.  The
    hotkey script first: left running, it would go on swallowing every key it binds.
    """
    logger.info("%s; tearing down %d launched child(ren)", reason, len(pids))
    stop_hotkey_script(ahk_proc, ahk_cmd_file)
    for pid in pids:
        kill_process_tree(pid)
    close_window(rfb_hwnd)
    # Only now that the windows under it are gone: drop the overlay.
    cover.progress.finish()
    if cover.process is not None:
        try:
            cover.process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            cover.process.kill()
            logger.warning("Loading screen did not exit, killed")
    cover.progress_file.unlink(missing_ok=True)
    cover.cancel_file.unlink(missing_ok=True)
    drop_crossing_cover(cover.progress_file.parent)  # else: no way out of an empty machine
    return _CANCELLED_EXIT_CODE


class _AppendOnWriteHandler(logging.Handler):
    """Logging handler that opens/closes the file on each write.

    AHK's Log() function uses FileAppend which also opens/closes per write.
    Using a persistent file handle (like RotatingFileHandler) would hold a
    Windows file lock and block AHK from writing to the same log file.
    """

    def __init__(self, log_path: Path):
        super().__init__()
        self.log_path = log_path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = f"{ts} {record.getMessage()}\r\n"
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(msg)
        except Exception:  # logging must never take the app down
            pass


def add_dispatch_file_handler(log_path: Path) -> None:
    """Add a file handler to bridge-related loggers.

    This ensures log messages from Python-dispatched commands appear in the
    windows bridge log file — the same file AHK writes to.  Public because the
    VR session runs the same dispatch loop and voice controller and needs the
    same durable record.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = _AppendOnWriteHandler(log_path)
    for name in ("fun_time.command_dispatch",
                  "fun_time.windows_bridge_dispatch_loop", "fun_time.voice_control",
                  "fun_time.windows_bridge_orchestrator"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)


# The orchestrator's logger owns the console and so does not propagate; it has
# to be enrolled in the event log by name.  Every other fun_time.* logger reaches
# the handler on the package logger by propagation.
_NON_PROPAGATING_LOGGERS = ("fun_time.orchestrator",)


def open_event_log(state_dir: Path) -> None:
    """Start this session's event log and feed every fun_time logger into it.

    The package logger's level is opened all the way to DEBUG because the log
    panel — not the writer — is where verbosity is chosen: the file carries
    everything the session says, and the panel shows the slice you asked for.

    Re-opening replaces the previous handler rather than stacking a second one.
    """
    handler = EventLogHandler(start_event_log(state_dir))
    handler.setLevel(logging.DEBUG)
    for name in ("fun_time", *_NON_PROPAGATING_LOGGERS):
        target = logging.getLogger(name)
        for existing in [h for h in target.handlers if isinstance(h, EventLogHandler)]:
            target.removeHandler(existing)
        target.addHandler(handler)
    logging.getLogger("fun_time").setLevel(logging.DEBUG)


# What the finishing pass may spend, all of it under the cover.  The cover comes
# down on DONE, which is written at the end of it, so these bound how long the
# progress file can sit unchanged while it runs — and the cover takes ITSELF
# down if that goes past ``loading_screen.STALE_TIMEOUT_S``, which would put the
# room's z-order back in front of the user.  A test pins the sum.
HUD_PRIME_TIMEOUT_S = 20.0
POST_LOADING_RESOLVE_TIMEOUT_S = 3.0
SETTLE_PASSES = 8
SETTLE_WAIT_S = 1.5


def _log_window_obstruction(name: str, hwnd: int, *, expected_over: int = 0,
                            ignore: int = 0) -> None:
    """Record which windows, if any, cover *name* once the bands are re-applied.

    The topmost flag reads ``True`` here, yet a player can still be reported
    "not on top" — a window may carry WS_EX_TOPMOST and remain buried under
    another overlapping window (a user's own always-on-top app, or a
    promotion-order slip).  ``is_window_topmost`` cannot see that; only the
    real z-order can, so this walks it and names the covering window instead
    of guessing.  Run for the satellites as well as Nau: "the landscape player
    is under other windows on startup" was undiagnosable while only Nau's
    coverage was logged.

    *expected_over* is the one window that belongs above the target in every
    mode — Genau's over Nau, which in video mode is the transparent HUD layer
    over Nau's video and in genau mode is the display itself.  Warning on the
    session's own by-design layering toasted every startup with a "covering"
    window that covers nothing you can see; anything else over the player
    still warns.  *ignore* is the loading
    overlay while this runs under it, which covers everything by design.
    """
    if not hwnd:
        logger.warning("%s window unresolved after loading; cannot check z-order", name)
        return
    covering = [
        w for w in windows_obscuring(hwnd, iter_zorder())
        if w.hwnd not in (expected_over, ignore)
    ]
    if covering:
        desc = "; ".join(
            f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}" for w in covering
        )
        logger.warning("%s (hwnd=%d) is covered at startup by: %s", name, hwnd, desc)
    else:
        logger.info("%s (hwnd=%d) is frontmost over its rect at startup", name, hwnd)


def _fix_post_loading_windows(result: StartupResult, *,
                              overlay_hwnd: int = 0) -> dict[str, int]:
    """Resolve every managed window, band it, and settle the z-order until each
    player is actually frontmost — returning the role hwnds it resolved.

    For the mode the session actually opened in, not for nau: on a resumed genau
    session this pass would otherwise promote Nau over Genau and un-park it, one
    pass after the sequencer parked it.

    ``overlay_hwnd`` is the loading screen's own window when this runs UNDER the
    curtain, which is where it belongs: the bands decide what the reveal looks
    like, so applying them afterwards is watching the room sort itself out.
    Handed the overlay, this keeps it on top across the pass (``HWND_TOPMOST``
    inserts at the top of the band, so each promotion lands over it until it is
    put back) and leaves it out of the "is this player buried?" test.
    """
    dash_hwnd = 0
    if result.dashboard_pid:
        dash_hwnd = find_window_by_pid(result.dashboard_pid)
        if not dash_hwnd:
            # Also the wait that keeps the cover up until the dashboard has shown
            # itself: it reveals on the last startup phase (see
            # ``startup_still_building``) and hides from these lookups until it
            # does, so resolving it here is what stops the cover leaving without
            # it.  A dashboard that never arrives costs the wait and no more.
            dash_hwnd = wait_for_window_by_title(
                "Fun Time", timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True)

    nau_hwnd = find_window_by_pid(result.nau_pid) or wait_for_window_by_title(
        "Nau", timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True
    )
    genau_hwnd = wait_for_window_by_title("Genau", timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S)
    # By title as well as pid, like Nau above: python_exe is the venv's pythonw
    # SHIM, so the recorded satellite pid is the launcher's rather than the
    # interpreter that owns the SDL window, and the by-pid lookup finds
    # nothing.  This pass is the only banding the satellites get on a
    # loading-screen startup — with hwnd 0 it silently skipped them, and every
    # session opened with both players out of the topmost band (the startup
    # topmost log said so each time), buried by the first window raised over
    # their rects.
    portrait_hwnd = find_window_by_pid(result.portrait_pid) or wait_for_window_by_title(
        SATELLITE_PORTRAIT_TITLE, timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True
    )
    landscape_hwnd = find_window_by_pid(result.landscape_pid) or wait_for_window_by_title(
        SATELLITE_LANDSCAPE_TITLE, timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True
    )
    # A session opening in origenerator mode has its hosted window restored
    # under the overlay already (the sequencer held the reveal for it); this
    # pass is where it joins the topmost band, over the RFB it covers.  Its two
    # REGION shows join with it, over the players they cover: they are managed
    # roles promoted after the players precisely so they end up on top, and
    # leaving them out of this pass is what put two blacked players over them.
    hosted = result.origenerator_pid and origenerator_shows(result.satellites_mode)
    origenerator_hwnd = (
        find_window_for_process(
            result.origenerator_pid, "Origenerator", include_hidden=True)
        if hosted else 0
    )
    show_hwnds = {
        role: (find_window_for_process(result.origenerator_pid, title,
                                       include_hidden=True) if hosted else 0)
        for role, title in ORIGENERATOR_ROLE_TITLES.items()
        if role != "origenerator"
    }
    role_hwnds = apply_startup_window_state(
        rfb_hwnd=result.rfb_hwnd,
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=genau_hwnd,
        nau_hwnd=nau_hwnd,
        dashboard_hwnd=dash_hwnd,
        origenerator_hwnd=origenerator_hwnd,
        origenerator_portrait_hwnd=show_hwnds["origenerator_portrait"],
        origenerator_landscape_hwnd=show_hwnds["origenerator_landscape"],
        mode=result.main_mode,
        satellites_mode=result.satellites_mode,
        beneath=overlay_hwnd,
    )
    keep_the_cover_up(overlay_hwnd)
    logger.info("Post-loading window state corrected")
    # The banding above can silently miss a player: SetWindowPos waits on the
    # target's own thread, the satellites are at their busiest now, and a
    # promotion that times out leaves the player under whatever was on that
    # monitor.  So walk the real z-order for a few seconds and re-promote whoever
    # is still buried -- settled on whoever OWNS each satellite rect in this
    # mode, since in origenerator mode the hosted app's shows cover the players
    # on purpose and re-promoting a "buried" player would bury the show.
    owners = satellite_rect_owners(result, portrait_hwnd, landscape_hwnd)
    _settle_the_players(owners, overlay_hwnd=overlay_hwnd)
    portrait_owner, landscape_owner = (hwnd for _name, hwnd in owners())
    # Genau's window sits over Nau on purpose in both modes — the transparent
    # HUD layer, or the display itself — so it is not a covering worth a
    # warning.
    _log_window_obstruction("Nau", nau_hwnd, expected_over=genau_hwnd)
    _log_window_obstruction("Portrait satellite", portrait_owner, ignore=overlay_hwnd)
    _log_window_obstruction("Landscape satellite", landscape_owner, ignore=overlay_hwnd)
    return role_hwnds


def satellite_rect_owners(result, portrait_hwnd: int, landscape_hwnd: int):
    """A callable answering who owns each satellite rect in this session's mode.

    The players in video mode.  In origenerator mode the hosted app's two
    region shows: they cover the players on purpose, and the players are
    blacked and held for the whole mode, so "the player is covered" is the
    normal state there rather than a burial to undo.

    A callable rather than a pair: a show can still arrive mid-settle where
    the hosted boot outran the reveal's wait for it, and one resolved as 0 up
    front was never settled — it stayed under the player promoted a moment
    earlier, a picture and then a black rectangle wearing the player's own HUD.
    """
    hosted = bool(result.origenerator_pid) and origenerator_shows(result.satellites_mode)

    def owners() -> list[tuple[str, int]]:
        if not hosted:
            return [("portrait", portrait_hwnd), ("landscape", landscape_hwnd)]
        return [
            (role.removeprefix("origenerator_"),
             find_window_for_process(result.origenerator_pid, title,
                                     include_hidden=True))
            for role, title in ORIGENERATOR_ROLE_TITLES.items()
            if role != "origenerator"
        ]

    return owners


def _settle_the_players(owners, *, overlay_hwnd: int = 0, passes: int = SETTLE_PASSES,
                        wait_s: float = SETTLE_WAIT_S) -> None:
    """Re-promote whoever owns each satellite rect until it is genuinely
    frontmost over it — the players in video mode, the hosted app's region
    shows in origenerator mode, where the players are blacked underneath them.

    *owners* is called for each pass and answers ``[(name, hwnd), ...]``, so a
    window that appears mid-settle is settled too and one that has gone is
    dropped.

    The banding above can silently miss one: SetWindowPos waits on the target's
    own thread, and the satellites are at their busiest exactly now (first clips
    decoding), so a promotion can time out through the stalled-window guard and
    leave the player under whatever the user had on that monitor — a maximized
    Chrome sat over the landscape player until the next full re-band.  So walk
    the real z-order and re-promote whoever is still buried.

    The loading overlay covers everything on purpose, so it is not a burial:
    left in the test, this loop would spend every pass re-promoting windows
    that are exactly where they belong — and it is put back on top after every
    single promotion, since each one lands above it.
    """
    for _ in range(passes):
        stack = iter_zorder()
        buried = [
            (name, hwnd) for name, hwnd in owners()
            if hwnd and _covering(hwnd, stack, ignore=overlay_hwnd)
        ]
        if not buried:
            break
        for name, hwnd in buried:
            logger.info("The %s region is still buried; re-asserting its band", name)
            set_always_on_top(hwnd, True)
            # After each one, not after the batch: the promotion lands above the
            # cover, and anything left there until the next window's turn shows
            # through it.
            keep_the_cover_up(overlay_hwnd)
        time.sleep(wait_s)


def _covering(hwnd: int, stack, *, ignore: int = 0) -> list:
    """What is over *hwnd*, minus the one window allowed to be."""
    return [w for w in windows_obscuring(hwnd, stack) if w.hwnd != ignore]


def _main_browse_stills(bridge_config) -> list[str]:
    """One video per library-browser tile, off the cheapest rendition of each.

    Warmed with the satellites' clips so the browser opens on a full grid rather
    than filling in under the user.  Per handle rather than per file, and off the
    smallest item: an upscale and the original it came from make the same
    picture, and only one of them is seconds rather than minutes to open.
    """
    return [
        handle.preview
        for handle in build_library_handles(
            bridge_config.main_sources, bridge_config.regen_metadata_root
        )
    ]


def start_hud_priming(
    bridge_config, manifest, *, enabled: bool
) -> tuple[HudPublisher | None, threading.Event]:
    """Build the HUD publisher and warm what it needs, off the startup thread.

    Two costs sit under the first map: indexing each library's seed families and
    action groups, and extracting a still frame per clip.  Both used to run in the
    separate HUD process; with the model here they run on this daemon thread, so
    startup keeps going while they finish.  The returned event fires once the
    indexes are ready — startup waits on it before revealing Fun Time, so the maps
    are never blank on screen.  The far longer thumbnail warm continues under it;
    those fill in as they land.
    """
    primed = threading.Event()
    if not enabled:
        primed.set()
        return None, primed
    sources = (bridge_config.portrait_sources, bridge_config.landscape_sources)
    cache_dir = bridge_config.state_dir / THUMBNAIL_CACHE_DIRNAME
    publisher = HudPublisher(
        {
            **{player.label: Path(manifest.commands.side_file(player.label, "hud"))
               for player in Player.SATELLITES},
            # Nau's console rides the same publisher as the satellites' maps.
            "nau": Path(manifest.commands.nau_console_file),
        },
        cache_dir,
    )

    def _warm() -> None:
        try:
            prime_group_indexes(sources, bridge_config.regen_metadata_root)
        finally:
            primed.set()
        for source in sources:
            if source:
                prewarm_thumbnails(collect_video_files(source), cache_dir)
        prewarm_thumbnails(_main_browse_stills(bridge_config), cache_dir)

    threading.Thread(target=_warm, daemon=True, name="hud-warm").start()
    return publisher, primed


@dataclass(frozen=True)
class _Cover:
    """The loading screen, or the absence of one on the path without a curtain.
    Its window is resolved as it opens, not at the reveal: startup's phases
    raise windows long before then (``keep_the_cover_up``)."""

    process: subprocess.Popen | None
    progress: ProgressReporter
    hwnd: int
    progress_file: Path
    cancel_file: Path


def _clear_last_sessions_leftovers(
    ahk_cmd_file: Path, pids_file: Path, dashboard_cmd_file: Path, dashboard_state_file: Path,
) -> None:
    """Drop the files a previous session left, before the hotkey script goes up
    and starts reading two of them.

    The shared state file is deliberately NOT among them: startup writes this
    session's opening state to it (see ``session_resume.resume_shared_state``),
    including whatever the resumed playlists were built under, and deleting it
    here would drop all of that back to defaults — the crashed-session
    leftovers that delete was for are cleared by that write instead.

    The pids file matters most: its appearance is what tells the hotkey script
    the session is up and its keys have something to reach, so a dead session's
    copy would put every key live over one that is still assembling.
    """
    for stale in (ahk_cmd_file, pids_file, dashboard_cmd_file,
                  dashboard_cmd_file.with_suffix(".processing"), dashboard_state_file):
        stale.unlink(missing_ok=True)


def _open_the_cover(state_dir: Path, *, show_overlays: bool) -> _Cover:
    """The loading screen over every monitor, its window resolved."""
    # A return is uncancellable: cancelling here closed the app under him.
    returning = returning_from_a_crossing(state_dir)
    progress_file = state_dir / PROGRESS_FILENAME
    cancel_file = state_dir / CANCEL_FILENAME
    # Clear a cancel flag left over from a previous session so it can't abort
    # this one before the user has touched anything.
    cancel_file.unlink(missing_ok=True)
    if not show_overlays:
        return _Cover(None, NullProgress(), 0, progress_file, cancel_file)

    loading_proc = subprocess.Popen(
        [
            NAMER.named_exe(sys.executable, "LoadingScreen"),
            "-m", "fun_time.loading_screen", str(progress_file),
        ],
    )
    logger.info("Loading screen launched (pid=%d)", loading_proc.pid)
    overlay_hwnd = wait_for_window_by_title(
        LOADING_SCREEN_TITLE, timeout_s=5.0, exact=True, include_hidden=True,
    )
    if overlay_hwnd:
        logger.info("Loading cover resolved (hwnd=%d)", overlay_hwnd)
    else:
        logger.warning("The loading cover's window did not appear; startup "
                       "will show through whatever it raises")
    # Handed over here, not at the reveal, which it would sit on top of.
    drop_crossing_cover(state_dir)
    return _Cover(
        loading_proc,
        PhaseProgress(progress_file, cancel_file=None if returning else cancel_file),
        overlay_hwnd, progress_file, cancel_file,
    )


def _reveal_the_room(
    result: StartupResult,
    *,
    manifest: LaunchManifest,
    cover: _Cover,
    hud_publisher,
    hud_primed,
) -> None:
    """Take the curtain down on a session that is finished under it.

    The sequencer already positioned every window in phase 4; what is left is
    the sorting phase 4 deliberately left off, then the cover, then the players.
    """
    # Held until the HUD's group indexes are primed, so Fun Time is not
    # revealed with the maps blank.  Capped: a slow scan fills in late instead.
    if hud_publisher is not None and not hud_primed.wait(timeout=HUD_PRIME_TIMEOUT_S):
        logger.warning("HUD indexes not primed after %.0fs; revealing anyway",
                       HUD_PRIME_TIMEOUT_S)
    # Band the room and settle its z-order UNDER the curtain.  Phase 4 left the
    # bands off (each promotion inserts above the overlay), so nothing of the
    # session is topmost yet: revealing here would show players sitting under
    # whatever was on those monitors, climbing over it a second later.
    role_hwnds = _fix_post_loading_windows(result, overlay_hwnd=cover.hwnd)

    cover.progress.finish()
    if cover.process:
        try:
            cover.process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            cover.process.kill()
            logger.warning("Loading screen did not exit, killed")
    cover.progress_file.unlink(missing_ok=True)

    # The cover is off the screen: NOW the players may run.  The phase walk
    # deliberately leaves this to us (see ``release_the_players``) — released
    # with the phases, Nau's video and Genau's audio would have been running
    # for the whole finishing pass, under a cover he cannot see or hear
    # through, and the opening seconds of the video would be gone by the time
    # it lifted.
    release_the_players(manifest, result.main_mode)
    # And the headset, where a player may have been holding its cover.
    release_the_headset(cover.progress_file.parent)

    # The overlay's own teardown hands activation to whatever is next in
    # the z-order, so the bands are asserted once more over the finished
    # room — cheap, since every window is already resolved and in place.
    owners = satellite_rect_owners(
        result, role_hwnds.get("portrait", 0), role_hwnds.get("landscape", 0))
    # A show that came up after the pass under the curtain has a handle
    # now, and this band is what puts it back above the player it covers:
    # the role order promotes it last for exactly that reason, and with a
    # zero in the map it was simply skipped.
    for name, hwnd in owners():
        if hwnd and origenerator_shows(result.satellites_mode):
            role_hwnds[f"origenerator_{name}"] = hwnd
    apply_topmost_bands(role_hwnds, result.main_mode, result.satellites_mode)
    _settle_the_players(owners, passes=3, wait_s=0.4)


def _serve_loopback(port: int, dispatch_runner: DispatchLoopRunner) -> ThreadingHTTPServer | None:
    """Serve the Provider autofill userscript so Tampermonkey can auto-update it
    instead of needing a hand-paste after every edit, and answer the RFB tab
    pages when they ask whether the session is paused.  A busy port (a leftover
    server) is not worth failing startup over."""
    try:
        server = serve_loopback(port=port, omni_paused=lambda: dispatch_runner.state.omni_paused)
    except OSError:
        logger.warning("Loopback server not started (port %d busy)", port, exc_info=True)
        return None
    logger.info("Loopback server started on 127.0.0.1:%d", port)
    return server


def _start_voice_control(
    config_path: str, *, dashboard_cmd_file: Path, dispatch_runner: DispatchLoopRunner,
) -> tuple[VoiceController | None, threading.Thread | None]:
    """Start listening, when the config asks for it and the import took.

    Every failure here is logged and swallowed: a session without voice is a
    session, and one that refuses to open because a microphone stack did not
    import is not.
    """
    voice_controller: VoiceController | None = None
    voice_thread: threading.Thread | None = None
    try:
        cfg = load_config(config_path)
        voice_diag = (
            f"VOICE_AVAILABLE={VOICE_AVAILABLE}, "
            f"enabled={cfg.voice_control.enabled}, "
            f"model={cfg.voice_control.model_path}, "
            f"device_name={cfg.voice_control.device_name}"
        )
        logger.info("Voice control check: %s", voice_diag)
        if VOICE_AVAILABLE and cfg.voice_control.enabled:
            voice_controller = VoiceController(
                cmd_file=dashboard_cmd_file,
                model_path=cfg.voice_control.model_path,
                confidence_threshold=cfg.voice_control.confidence_threshold,
                device_name=cfg.voice_control.device_name,
                sample_rate=cfg.voice_control.sample_rate,
            )
            dispatch_runner.voice_controller = voice_controller
            voice_controller.active_side = lambda: dispatch_runner.state.active_side
            voice_thread = threading.Thread(target=voice_controller.run, daemon=True, name="voice-control")
            voice_thread.start()
            logger.info("Voice control thread launched")
        elif cfg.voice_control.enabled:
            logger.warning("Voice control enabled but import failed: %s", voice_import_error())
        else:
            logger.info("Voice control disabled in config")
    except Exception:
        logger.exception("Voice control setup failed")
    return voice_controller, voice_thread


def _start_the_dispatch_loop(
    result: StartupResult,
    *,
    manifest: LaunchManifest,
    manifest_path: Path,
    bridge_config,
    state_dir: Path,
    dashboard_cmd_file: Path,
    ahk_cmd_file: Path,
    dashboard_enabled: bool,
    hud_publisher,
    env: SessionEnvironment,
) -> tuple[DispatchLoopRunner, threading.Thread]:
    """Hand the finished session to the loop that runs it, on its own thread.

    Genau startup detection rides the loop's first sync tick: if the broker has
    already written genau_mode.txt = "1" (it infers auto mode within ~4 s from
    BPM and motion), the sync sees the entering transition and hands the main
    player over to Genau naturally.
    """
    rfb_shortcut = Shortcut()
    if manifest.random_favs_browser.enabled:
        rfb_shortcut = resolve_shortcut(manifest.random_favs_browser.shortcut_path)

    dispatch_runner = DispatchLoopRunner(
        config=bridge_config,
        dashboard_cmd_file=dashboard_cmd_file,
        manifest_path=manifest_path,
        shared_state_file=shared_state_path(state_dir),
        ahk_cmd_file=ahk_cmd_file,
        windows=WindowRoles(
            pids=ChildPids(
                nau=result.nau_pid,
                portrait=result.portrait_pid,
                landscape=result.landscape_pid,
                dashboard=result.dashboard_pid,
                origenerator=result.origenerator_pid,
            ),
            rfb_hwnd=result.rfb_hwnd,
            role_hwnds=result.role_hwnds,
        ),
        dashboard_enabled=dashboard_enabled,
        env=env,
        hud_publisher=hud_publisher,
        rfb_shortcut=rfb_shortcut,
    )
    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    logger.info("Background dispatch loop started")

    return dispatch_runner, dispatch_thread


def silence_the_players(commands: CommandFiles) -> None:
    for paused_file in (
        commands.nau_paused_file, commands.audio_paused_file,
        commands.portrait_paused_file, commands.landscape_paused_file,
        commands.origenerator_paused_file,
    ):
        if paused_file.strip():
            write_flag_file(paused_file, True)


def _run_until_the_hotkeys_exit(
    ahk_proc: subprocess.Popen,
    *,
    state_dir: Path,
    commands: CommandFiles,
    show_overlays: bool,
    rfb_hwnd: int,
    children: dict,
    origenerator_cmd_file: Path | None,
    voice: tuple[VoiceController | None, threading.Thread | None],
    dispatch: tuple[DispatchLoopRunner, threading.Thread],
    loopback_server: ThreadingHTTPServer | None,
) -> int:
    """Hold the session open, then take it down — in that order, always.

    The hotkey script's exit IS the session ending, so this is where the
    session lives out its life; the teardown is in a ``finally`` because an
    interrupt has to bring the children down exactly as a quit does.
    """
    voice_controller, voice_thread = voice
    dispatch_runner, dispatch_thread = dispatch
    try:
        exit_code = ahk_proc.wait()
        # WHY the session is ending, which the log could not say before.  A
        # session that vanishes and one the user quit produce the same lines
        # from here down -- the hotkey script exits either way, the closing
        # screen goes up either way, and the orchestrator returns 0 either way
        # -- so a report of "it crashed" had nothing in the log to confirm or
        # deny it.  The quit chord stamps a marker on its way out (see
        # windows_bridge_hotkeys.ahk, EndSession); no marker and the script is
        # gone anyway means it went down on its own.
        logger.info("Hotkey script exited with code %s (%s)", exit_code,
                    _describe_session_end(state_dir, exit_code))
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        exit_code = 1
    finally:
        silence_the_players(commands)
        # Then the cover, up before anything closes and through all of it: the
        # controls stopping, the browser closing, and every child being killed.
        with _closing_screen(state_dir, enabled=show_overlays,
                             crossing=pending_handoff(state_dir)) as shutdown_progress:
            if voice_controller is not None:
                voice_controller.stop()
            if voice_thread is not None:
                voice_thread.join(timeout=2.0)
            dispatch_runner.stop()
            dispatch_thread.join(timeout=2.0)
            if loopback_server is not None:
                # shutdown() blocks until serve_forever returns, so it belongs
                # here under the cover rather than out in the open — and the
                # port is machine-wide, so a server left listening is a port the
                # next session cannot have.
                loopback_server.shutdown()
                loopback_server.server_close()
            logger.info("AHK exited — shutting down child processes")
            crossing_over = pending_handoff(state_dir) is not None
            _shutdown_children(
                rfb_hwnd, children, shutdown_progress, state_dir=state_dir,
                keep_origenerator_via=origenerator_cmd_file if crossing_over else None,
            )

    return exit_code


def run_session(
    *,
    manifest_path: str | Path,
    ahk_exe: str,
    hotkey_script: str,
    state_dir: str | Path,
    project_dir: str | Path,
    env: SessionEnvironment = ORDINARY_SESSION,
) -> int:
    """Open a session, hold it, and close it.

    1. Cover every monitor and put the hotkey script up under it
    2. Run startup sequencer (core session + window positioning + UI companions)
    3. Write the PIDs file, which is also what tells AHK the session is up
    4. Wait for AHK to exit
    5. Shut down all child processes
    """
    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)
    project_dir = Path(project_dir)

    # Before anything else logs, and before the dashboard launches the panel that
    # tails it: this session's event log starts empty and starts collecting.
    open_event_log(state_dir)

    manifest = LaunchManifest.read(manifest_path)
    bridge_config = build_bridge_config_from_manifest(manifest)
    dashboard_enabled = manifest.dashboard_enabled

    # Route dispatch log messages to the windows bridge log file so they
    # appear alongside AHK log entries (integration tests read this file).
    # Before the hotkey script goes up, so the line naming what was launched
    # lands in the same file the script itself starts writing to.
    add_dispatch_file_handler(Path(manifest.runtime.windows_bridge_log_file))

    dashboard_cmd_file = Path(manifest.commands.dashboard_cmd_file)
    ahk_cmd_file = state_dir / "ahk_cmd.txt"
    pids_file = state_dir / "bridge_pids.ini"
    _clear_last_sessions_leftovers(ahk_cmd_file, pids_file, dashboard_cmd_file,
                                   Path(manifest.commands.dashboard_state_file))

    # --- Launch loading screen (normal mode only) ---
    cover = _open_the_cover(state_dir, show_overlays=env.show_overlays)
    progress, overlay_hwnd = cover.progress, cover.hwnd

    if env.integration:
        ahk_cmd_file.write_text("suspend_hotkeys", encoding="utf-8")
        logger.info("Pre-wrote suspend_hotkeys for integration test run")

    # --- The hotkey script, up before the session it drives ---
    # Esc has to reach us from the cover onward, and AHK's hooks are the only
    # keys here that do not care which window holds the focus (see
    # overlay_progress).  The script holds its other keys until the pids file.
    command = [ahk_exe, hotkey_script, str(manifest_path), str(pids_file)]
    logger.info("Launching AHK hotkey script: %s", " ".join(command))
    ahk_proc = subprocess.Popen(command, cwd=project_dir)

    # The lock HUD's model is built here and published for each satellite player
    # to draw into its own video.  It rides the dashboard's enable gate, so an
    # integration run stays free of library scans and frame grabs.
    hud_publisher, hud_primed = start_hud_priming(
        bridge_config, manifest, enabled=dashboard_enabled)

    logger.info("Running startup sequence")
    try:
        result = run_startup_sequence(
            manifest_path=manifest_path,
            state_dir=state_dir,
            progress=progress,
            hide_windows=env.show_overlays,
            cover_hwnd=overlay_hwnd,
            env=env,
        )
    except StartupCancelled as cancelled:
        # Esc during a phase: the sequence handed back exactly what it had
        # launched.  Tear it down, take the hotkey script back out, and exit
        # before the dispatch loop ever starts.
        return _take_down_the_startup(
            "Startup cancelled by user", pids=cancelled.launched_pids,
            rfb_hwnd=cancelled.rfb_hwnd, cover=cover, ahk_proc=ahk_proc, ahk_cmd_file=ahk_cmd_file,
        )

    launched = [getattr(result, key) for key in _CHILD_PID_KEYS]

    # Esc can also land in the sliver after the last checkpoint but before the
    # reveal: the sequence finished, yet the flag is set.  Tear the full result
    # down rather than reveal a session the user asked to abort.
    if progress.cancelled:
        return _take_down_the_startup(
            "Startup cancelled by user", pids=launched,
            rfb_hwnd=result.rfb_hwnd, cover=cover, ahk_proc=ahk_proc, ahk_cmd_file=ahk_cmd_file,
        )

    logger.info(
        "Startup complete: nau=%d portrait=%d landscape=%d dashboard=%d genau=%d audio=%d",
        result.nau_pid, result.portrait_pid, result.landscape_pid,
        result.dashboard_pid, result.genau_pid, result.audio_pid,
    )

    try:
        # The sequencer already positioned all windows in Phase 4 (the reveal).
        if env.show_overlays:
            _reveal_the_room(result, manifest=manifest, cover=cover,
                             hud_publisher=hud_publisher, hud_primed=hud_primed)

        # The session is up and its windows are placed.  Writing this file records
        # the children for teardown and, by appearing, hands the keyboard over: the
        # hotkey script watches for it and takes its startup hold off, so the keys
        # go live exactly when there is a session for them to drive.
        children = identify_children(result)
        write_pids_file(pids_file, children)

        dispatch_runner, dispatch_thread = _start_the_dispatch_loop(
            result,
            manifest=manifest,
            manifest_path=manifest_path,
            bridge_config=bridge_config,
            state_dir=state_dir,
            dashboard_cmd_file=dashboard_cmd_file,
            ahk_cmd_file=ahk_cmd_file,
            dashboard_enabled=dashboard_enabled,
            hud_publisher=hud_publisher,
            env=env,
        )
        loopback_server = _serve_loopback(manifest.loopback_port, dispatch_runner)
        voice_controller, voice_thread = _start_voice_control(
            manifest.runtime.config_path,
            dashboard_cmd_file=dashboard_cmd_file,
            dispatch_runner=dispatch_runner,
        )
    except BaseException:
        _take_down_the_startup(
            "The session failed while opening", pids=launched,
            rfb_hwnd=result.rfb_hwnd, cover=cover, ahk_proc=ahk_proc, ahk_cmd_file=ahk_cmd_file,
        )
        raise

    return _run_until_the_hotkeys_exit(
        ahk_proc,
        state_dir=state_dir,
        commands=manifest.commands,
        show_overlays=env.show_overlays,
        rfb_hwnd=result.rfb_hwnd,
        children=children,
        origenerator_cmd_file=bridge_config.origenerator_cmd_file,
        voice=(voice_controller, voice_thread),
        dispatch=(dispatch_runner, dispatch_thread),
        loopback_server=loopback_server,
    )
