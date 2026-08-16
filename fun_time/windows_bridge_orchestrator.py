"""Python orchestrator for the Windows bridge.

Runs the full startup sequence, launches the minimal AHK hotkey script,
starts the background dispatch loop, waits for AHK to exit, then shuts
down all child processes.  Both ends of that happen behind a cover over
every monitor, so the session's windows are never watched arriving or
leaving one at a time.
"""
from __future__ import annotations

import configparser
import contextlib
import datetime
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .event_log import EventLogHandler, start_event_log
from .overlay_progress import (
    CANCEL_FILENAME,
    NullProgress,
    PROGRESS_FILENAME,
    SHUTDOWN_PHASES,
    SHUTDOWN_PROGRESS_FILENAME,
    PhaseProgress,
    ProgressReporter,
    StartupCancelled,
    ready_file_for,
)
from .hud_transport import HUD_FILENAME, HudPublisher
from .library_handles import build_library_handles
from .lock_hud import prime_group_indexes
from .loopback_server import serve_loopback
from .mode_plan import genau_active
from .modes import collect_video_files
from .process_identity import identified_python_exe
from .shared_state import shared_state_path
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, prewarm_thumbnails
from .voice_control import VOICE_AVAILABLE, VoiceController, _VOICE_IMPORT_ERROR
from .windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from .windows_bridge_sequencer import (
    StartupResult,
    _apply_startup_window_state,
    resolve_shortcut,
    run_startup_sequence,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)
from .win32 import (
    close_window,
    find_window_by_pid,
    find_window_by_pid_and_title,
    get_process_creation_time,
    iter_zorder,
    wait_for_window_by_title,
    windows_obscuring,
)

logger = logging.getLogger(__name__)


# Every child a session launches, grouped the way teardown reports it to the
# closing screen.  The groups are the source of truth: a child added to one is
# recorded at startup and killed at shutdown by the same edit, so there is no
# way to add a seventh child and have it quietly outlive the session.
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

    Its closeEvent is where the session persists and the absence experiments
    are handed to ComfyUI — a straight taskkill loses both.  Bounded: a close
    that hangs falls through to the companions sweep, which kills the tree the
    way it kills everything else.
    """
    if child is None or not child.pid:
        return
    hwnd = find_window_by_pid_and_title(child.pid, "Origenerator")
    if not hwnd:
        return
    close_window(hwnd)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if get_process_creation_time(child.pid) != child.created_at:
            return  # exited (or the pid was never ours) — nothing left to wait on
        time.sleep(0.2)
    logger.warning("Origenerator did not close within 5s; the kill sweep takes it")


def _shutdown_children(
    rfb_hwnd: int, children: dict[str, ChildProcess], progress: ProgressReporter
) -> None:
    """Kill all child processes launched during startup.

    Reports each group as it starts, so the closing screen covering all this can
    say which windows are on their way out — and, if a kill ever wedges, which
    one it wedged on.
    """
    progress.advance("browser")
    close_window(rfb_hwnd)
    _close_origenerator_gracefully(children.get("origenerator_pid"))
    for phase, keys in _CHILD_GROUPS:
        progress.advance(phase)
        for key in keys:
            kill_recorded_child(children[key])


# How long teardown holds for the closing screen to report itself painted.  A
# fresh python + tkinter process is up in well under a second; the rest is slack
# for a loaded machine, and it is a ceiling nobody normally pays.
CLOSING_SCREEN_READY_TIMEOUT_S = 5.0


def _wait_for_closing_screen(ready_file: Path, proc: subprocess.Popen) -> None:
    """Block until the cover is painted over every monitor.

    Killing before then defeats the whole point of it — the windows would be
    seen going out one at a time, which is what the screen exists to hide.  Two
    ways out besides the flag: the screen died, so no flag is ever coming; or it
    is taking so long that waiting costs more than the flicker would.
    """
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
def _closing_screen(state_dir: Path, *, enabled: bool) -> Iterator[ProgressReporter]:
    """Cover every monitor while the session comes down, then uncover it.

    Yields the reporter the teardown steps report through.  The cover is up and
    painted before the body runs and comes down only once the body has finished,
    so the moment between "quit" and an empty desktop shows one panel instead of
    six windows going out one by one.

    Disabled for an integration run, which has no eyes on it — the same reason
    such a run skips the loading screen.
    """
    if not enabled:
        yield NullProgress()
        return

    progress_file = state_dir / SHUTDOWN_PROGRESS_FILENAME
    ready_file = ready_file_for(progress_file)
    # A flag left by a previous session would let this teardown start with
    # nothing yet covering the screen.
    ready_file.unlink(missing_ok=True)
    progress = PhaseProgress(progress_file, phases=SHUTDOWN_PHASES)
    # Written before the screen is launched so it has something to read from its
    # first poll, and so its staleness clock starts here rather than never.
    progress.advance("controls")
    proc = subprocess.Popen(
        [
            identified_python_exe(sys.executable, "ClosingScreen"),
            "-m", "fun_time.closing_screen", str(progress_file),
        ],
    )
    logger.info("Closing screen launched (pid=%d)", proc.pid)
    _wait_for_closing_screen(ready_file, proc)
    try:
        yield progress
    finally:
        progress.finish()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            logger.warning("Closing screen did not exit, killed")
        progress_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


# Cancelling from the loading screen is a clean, user-initiated exit.
_CANCELLED_EXIT_CODE = 0


def _cancel_startup(
    *,
    pids: list[int],
    rfb_hwnd: int,
    loading_proc: subprocess.Popen | None,
    progress: ProgressReporter,
    progress_file: Path,
    cancel_file: Path,
) -> int:
    """Tear down a startup the user aborted from the loading screen, then exit.

    Kills every child launched so far and closes the browser window *before*
    bringing the overlay down, so nothing half-started ever flashes into view.
    These children were launched seconds ago, so their PIDs are still theirs —
    no creation-time pinning is needed the way a deferred teardown needs it.
    """
    logger.info("Startup cancelled by user; tearing down %d launched child(ren)", len(pids))
    for pid in pids:
        kill_process_tree(pid)
    close_window(rfb_hwnd)
    # Only now that the windows behind it are gone: drop the overlay.
    progress.finish()
    if loading_proc is not None:
        try:
            loading_proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            loading_proc.kill()
            logger.warning("Loading screen did not exit after cancel, killed")
    progress_file.unlink(missing_ok=True)
    cancel_file.unlink(missing_ok=True)
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
        except Exception:
            pass


def _add_dispatch_file_handler(log_path: Path) -> None:
    """Add a file handler to bridge-related loggers.

    This ensures log messages from Python-dispatched commands appear in the
    windows bridge log file — the same file AHK writes to.
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


def _open_event_log(state_dir: Path) -> None:
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


def _log_nau_obstruction(nau_hwnd: int, *, expected_over: int = 0) -> None:
    """Record which windows, if any, cover Nau once the bands are re-applied.

    The topmost flag reads ``True`` here, yet Nau can still be reported "not on
    top" — a window may carry WS_EX_TOPMOST and remain buried under another
    overlapping window (a user's own always-on-top app, or a promotion-order
    slip).  ``is_window_topmost`` cannot see that; only the real z-order can, so
    this walks it and names the covering window instead of guessing.

    *expected_over* is the one window that belongs above Nau in the mode the
    session opened in — Genau's, which in hybrid is the transparent HUD layer
    over Nau's video and in genau mode is the display itself.  Warning on the
    session's own by-design layering toasted every hybrid startup with a
    "covering" window that covers nothing you can see; anything else over Nau
    still warns.
    """
    if not nau_hwnd:
        logger.warning("Nau window unresolved after loading; cannot check z-order")
        return
    covering = [
        w for w in windows_obscuring(nau_hwnd, iter_zorder())
        if w.hwnd != expected_over
    ]
    if covering:
        desc = "; ".join(
            f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}" for w in covering
        )
        logger.warning("Nau (hwnd=%d) is covered at startup by: %s", nau_hwnd, desc)
    else:
        logger.info("Nau (hwnd=%d) is frontmost over its rect at startup", nau_hwnd)


def _fix_post_loading_windows(result: StartupResult) -> None:
    """Re-assert the topmost policy and the main slot's visibility after the
    loading screen overlay is destroyed (its teardown can shuffle activation, and
    the dashboard may only become resolvable this late).

    For the mode the session actually opened in, not for nau: on a resumed genau
    session this pass would otherwise promote Nau over Genau and un-park it, one
    pass after the sequencer parked it.
    """
    dash_hwnd = 0
    if result.dashboard_pid:
        dash_hwnd = find_window_by_pid(result.dashboard_pid)
        if not dash_hwnd:
            dash_hwnd = wait_for_window_by_title("Fun Time", timeout_s=3.0, exact=True)

    nau_hwnd = find_window_by_pid(result.nau_pid) or wait_for_window_by_title(
        "Nau", timeout_s=3.0, exact=True
    )
    genau_hwnd = wait_for_window_by_title("Genau", timeout_s=3.0)
    # By title as well as pid, like Nau above: python_exe is the venv's pythonw
    # SHIM, so the recorded satellite pid is the launcher's rather than the
    # interpreter that owns the SDL window, and the by-pid lookup finds
    # nothing.  This pass is the only banding the satellites get on a
    # loading-screen startup — with hwnd 0 it silently skipped them, and every
    # session opened with both players out of the topmost band (the startup
    # topmost log said so each time), buried by the first window raised over
    # their rects.
    portrait_hwnd = find_window_by_pid(result.portrait_pid) or wait_for_window_by_title(
        SATELLITE_PORTRAIT_TITLE, timeout_s=3.0, exact=True
    )
    landscape_hwnd = find_window_by_pid(result.landscape_pid) or wait_for_window_by_title(
        SATELLITE_LANDSCAPE_TITLE, timeout_s=3.0, exact=True
    )
    _apply_startup_window_state(
        rfb_hwnd=result.rfb_hwnd,
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=genau_hwnd,
        nau_hwnd=nau_hwnd,
        dashboard_hwnd=dash_hwnd,
        mode=result.main_mode,
    )
    logger.info("Post-loading window state corrected")
    # In hybrid and genau modes Genau's window sits over Nau on purpose — the
    # transparent HUD layer, or the display itself — so it is not a covering
    # worth a warning there.
    _log_nau_obstruction(
        nau_hwnd,
        expected_over=genau_hwnd if genau_active(result.main_mode) else 0,
    )


def _main_browse_stills(bridge_config) -> list[str]:
    """One video per library-browser tile, off the cheapest rendition of each.

    Warmed with the satellites' clips so the browser opens on a full grid rather
    than filling in under the user.  Per handle rather than per file, and off the
    smallest member: an upscale and the original it came from make the same
    picture, and only one of them is seconds rather than minutes to open.
    """
    return [
        handle.preview
        for handle in build_library_handles(
            bridge_config.main_sources, bridge_config.regen_metadata_root
        )
    ]


def _start_hud_priming(
    bridge_config, manifest, *, enabled: bool
) -> tuple[HudPublisher | None, threading.Event]:
    """Build the HUD publisher and warm what it needs, off the startup thread.

    Two costs sit behind the first map: indexing each library's seed families and
    action groups, and extracting a still frame per clip.  Both used to run in the
    separate HUD process; with the model here they run on this daemon thread, so
    startup keeps going while they finish.  The returned event fires once the
    indexes are ready — startup waits on it before revealing Fun Time, so the maps
    are never blank on screen.  The far longer thumbnail warm continues behind it;
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
            **{side: Path(manifest["commands"][f"{side}_hud_file"]) for side in HUD_FILENAME},
            # Nau's console rides the same publisher as the satellites' maps.
            "nau": Path(manifest["commands"]["nau_console_file"]),
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


def run_python_orchestrated_bridge(
    *,
    manifest_path: str | Path,
    ahk_exe: str,
    hotkey_script: str,
    state_dir: str | Path,
    project_dir: str | Path,
) -> int:
    """Run the full Python-orchestrated bridge lifecycle.

    1. Run startup sequencer (core session + window positioning + UI companions)
    2. Write PIDs file for AHK
    3. Launch AHK hotkey script
    4. Wait for AHK to exit
    5. Shut down all child processes
    """
    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)
    project_dir = Path(project_dir)

    # Before anything else logs, and before the dashboard launches the panel that
    # tails it: this session's event log starts empty and starts collecting.
    _open_event_log(state_dir)

    integration_mode = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    show_overlays = not integration_mode

    # --- Launch loading screen (normal mode only) ---
    loading_proc = None
    progress_file = state_dir / PROGRESS_FILENAME
    cancel_file = state_dir / CANCEL_FILENAME
    # Clear a cancel flag left over from a previous session so it can't abort
    # this one before the user has touched anything.
    cancel_file.unlink(missing_ok=True)
    if show_overlays:
        progress = PhaseProgress(progress_file, cancel_file=cancel_file)
        loading_proc = subprocess.Popen(
            [
                identified_python_exe(sys.executable, "LoadingScreen"),
                "-m", "fun_time.loading_screen", str(progress_file),
            ],
        )
        logger.info("Loading screen launched (pid=%d)", loading_proc.pid)
    else:
        progress = NullProgress()

    manifest = configparser.ConfigParser()
    manifest.optionxform = str
    manifest.read(str(manifest_path), encoding="utf-8")
    bridge_config = build_bridge_config_from_manifest(manifest)
    dashboard_enabled = manifest["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"}
    # The lock HUD's model is built here and published for each satellite player
    # to draw into its own video.  It rides the dashboard's enable gate, so an
    # integration run stays free of library scans and frame grabs.
    hud_publisher, hud_primed = _start_hud_priming(
        bridge_config, manifest, enabled=dashboard_enabled)

    logger.info("Running startup sequence")
    try:
        result = run_startup_sequence(
            manifest_path=manifest_path,
            state_dir=state_dir,
            progress=progress,
            hide_windows=show_overlays,
        )
    except StartupCancelled as cancelled:
        # Esc during a phase: the sequence handed back exactly what it had
        # launched.  Tear it down and exit before AHK or the dispatch loop start.
        return _cancel_startup(
            pids=cancelled.launched_pids,
            rfb_hwnd=cancelled.rfb_hwnd,
            loading_proc=loading_proc,
            progress=progress,
            progress_file=progress_file,
            cancel_file=cancel_file,
        )

    # Esc can also land in the sliver after the last checkpoint but before the
    # reveal: the sequence finished, yet the flag is set.  Tear the full result
    # down rather than reveal a session the user asked to abort.
    if progress.cancelled:
        return _cancel_startup(
            pids=[
                result.nau_pid, result.portrait_pid, result.landscape_pid,
                result.dashboard_pid, result.genau_pid, result.audio_pid,
            ],
            rfb_hwnd=result.rfb_hwnd,
            loading_proc=loading_proc,
            progress=progress,
            progress_file=progress_file,
            cancel_file=cancel_file,
        )

    logger.info(
        "Startup complete: nau=%d portrait=%d landscape=%d dashboard=%d genau=%d audio=%d",
        result.nau_pid, result.portrait_pid, result.landscape_pid,
        result.dashboard_pid, result.genau_pid, result.audio_pid,
    )

    # --- Close loading screen (normal mode only) ---
    # The sequencer already positioned all windows in Phase 4 (the reveal).
    if show_overlays:
        # Hold the loading screen until the HUD's group indexes are primed, so
        # Fun Time isn't revealed with the maps still blank.  Capped so a slow
        # library scan can't wedge startup — the maps just fill in late.
        if hud_publisher is not None and not hud_primed.wait(timeout=20.0):
            logger.warning("HUD indexes not primed after 20s; revealing anyway")
        progress.finish()
        if loading_proc:
            try:
                loading_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                loading_proc.kill()
                logger.warning("Loading screen did not exit, killed")
        progress_file.unlink(missing_ok=True)

        # Re-assert z-order AFTER the loading screen overlay is gone.
        # Phase 4 set topmost while the overlay was still covering everything;
        # destroying the overlay can rearrange z-order.  Correct it now.
        _fix_post_loading_windows(result)

    pids_file = state_dir / "bridge_pids.ini"
    children = identify_children(result)
    write_pids_file(pids_file, children)

    # Clean stale command files from previous sessions so the dispatch loop
    # starts fresh.  The shared state file is deliberately NOT among them:
    # startup already wrote this session's opening state to it (see
    # session_resume.resume_shared_state), including whatever the resumed
    # playlists were built under, and deleting it here would drop all of that
    # back to defaults — the crashed-session leftovers that delete was for are
    # cleared by that write instead.
    # Start background dispatch loop (dashboard polling + genau sync)
    dashboard_cmd_file = Path(manifest["commands"]["dashboard_cmd_file"])
    for stale in (
        state_dir / "ahk_cmd.txt",
        dashboard_cmd_file,
        dashboard_cmd_file.with_suffix(".processing"),
    ):
        stale.unlink(missing_ok=True)

    # Route dispatch log messages to the windows bridge log file so they
    # appear alongside AHK log entries (integration tests read this file).
    wb_log_path = Path(manifest["runtime"]["windows_bridge_log_file"])
    _add_dispatch_file_handler(wb_log_path)

    rfb_target, rfb_work_dir, rfb_args = "", "", ""
    if manifest["random_favs_browser"]["enabled"] == "1":
        rfb_shortcut_path = manifest["random_favs_browser"]["shortcut_path"]
        rfb_target, rfb_work_dir, rfb_args = resolve_shortcut(rfb_shortcut_path)

    dispatch_runner = DispatchLoopRunner(
        role_hwnds=result.role_hwnds,
        config=bridge_config,
        dashboard_cmd_file=Path(manifest["commands"]["dashboard_cmd_file"]),
        manifest_path=Path(manifest_path),
        shared_state_file=shared_state_path(state_dir),
        ahk_cmd_file=state_dir / "ahk_cmd.txt",
        nau_pid=result.nau_pid,
        portrait_pid=result.portrait_pid,
        landscape_pid=result.landscape_pid,
        dashboard_pid=result.dashboard_pid,
        origenerator_pid=result.origenerator_pid,
        dashboard_enabled=dashboard_enabled,
        hud_publisher=hud_publisher,
        rfb_hwnd=result.rfb_hwnd,
        rfb_shortcut_target=rfb_target,
        rfb_shortcut_work_dir=rfb_work_dir,
        rfb_shortcut_args=rfb_args,
    )
    # Genau startup detection is handled by the dispatch loop's first
    # sync tick: if the broker has already written genau_mode.txt = "1"
    # (it detects auto mode within ~4s via BPM/stroke inference), the sync
    # will detect the entering transition and hand the main player over
    # to Genau naturally.

    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    logger.info("Background dispatch loop started")

    # Serve the Provider autofill userscript so Tampermonkey can auto-update it
    # instead of needing a hand-paste after every edit, and answer the RFB tab
    # pages when they ask whether the session is paused. The port comes from
    # config so a session started alongside another can serve somewhere of its
    # own; a busy one (a leftover server) is not worth failing startup over.
    loopback_port = int(manifest["loopback"]["port"])
    try:
        serve_loopback(port=loopback_port, omni_paused=lambda: dispatch_runner.state.omni_paused)
        logger.info("Loopback server started on 127.0.0.1:%d", loopback_port)
    except OSError:
        logger.warning("Loopback server not started (port %d busy)", loopback_port, exc_info=True)

    # --- Optional voice control ---
    voice_controller: VoiceController | None = None
    voice_thread: threading.Thread | None = None
    try:
        cfg = load_config(manifest["runtime"]["config_path"])
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
            voice_thread = threading.Thread(target=voice_controller.run, daemon=True, name="voice-control")
            voice_thread.start()
            logger.info("Voice control thread launched")
        elif cfg.voice_control.enabled:
            logger.warning("Voice control enabled but import failed: %s", _VOICE_IMPORT_ERROR)
        else:
            logger.info("Voice control disabled in config")
    except Exception:
        logger.exception("Voice control setup failed")

    ahk_cmd_file = state_dir / "ahk_cmd.txt"
    if os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1":
        ahk_cmd_file.write_text("suspend_hotkeys", encoding="utf-8")
        logger.info("Pre-wrote suspend_hotkeys for integration test run")

    command = [ahk_exe, hotkey_script, str(manifest_path), str(pids_file)]
    logger.info("Launching AHK hotkey script: %s", " ".join(command))
    ahk_proc = subprocess.Popen(command, cwd=project_dir)

    try:
        exit_code = ahk_proc.wait()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        exit_code = 1
    finally:
        # The cover goes up first and stays up through everything below: the
        # controls stopping, the browser closing, and every child being killed.
        with _closing_screen(state_dir, enabled=show_overlays) as shutdown_progress:
            if voice_controller is not None:
                voice_controller.stop()
            if voice_thread is not None:
                voice_thread.join(timeout=2.0)
            dispatch_runner.stop()
            dispatch_thread.join(timeout=2.0)
            logger.info("AHK exited — shutting down child processes")
            _shutdown_children(result.rfb_hwnd, children, shutdown_progress)

    return exit_code
