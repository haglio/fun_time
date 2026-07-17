"""Python orchestrator for the Windows bridge.

Runs the full startup sequence, launches the minimal AHK hotkey script,
starts the background dispatch loop, waits for AHK to exit, then shuts
down all child processes.
"""
from __future__ import annotations

import configparser
import datetime
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .event_log import EventLogHandler, start_event_log
from .startup_progress import NullProgress, PROGRESS_FILENAME, StartupProgress
from .lock_hud import HUD_READY_FILENAME, wait_for_hud_ready
from .userscript_server import USERSCRIPT_PORT, serve_userscript_updates
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
from .win32 import (
    close_window,
    find_window_by_pid,
    get_process_creation_time,
    iter_zorder,
    wait_for_window_by_title,
    windows_obscuring,
)

logger = logging.getLogger(__name__)


_CHILD_PID_KEYS = (
    "nau_pid",
    "portrait_pid",
    "landscape_pid",
    "dashboard_pid",
    "lock_hud_pid",
    "genau_pid",
    "audio_pid",
)


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
    (VLC's HTTP interface answered, Nau's and Genau's windows appeared), so the
    creation time read here is the one our child was born with.  Everything that
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


# Number of progress steps reported by run_startup_sequence in hide_windows
# mode (the only mode with a loading screen).
_STARTUP_PROGRESS_STEPS = 6


def _shutdown_children(rfb_hwnd: int, children: dict[str, ChildProcess]) -> None:
    """Kill all child processes launched during startup."""
    close_window(rfb_hwnd)
    for child in children.values():
        kill_recorded_child(child)


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
    for name in ("fun_time.command_dispatch", "fun_time.vlc_actions",
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


def _log_nau_obstruction(nau_hwnd: int) -> None:
    """Record which windows, if any, cover Nau once the bands are re-applied.

    The topmost flag reads ``True`` here, yet Nau can still be reported "not on
    top" — a window may carry WS_EX_TOPMOST and remain buried under another
    overlapping window (a user's own always-on-top app, or a promotion-order
    slip).  ``is_window_topmost`` cannot see that; only the real z-order can, so
    this walks it and names the covering window instead of guessing.
    """
    if not nau_hwnd:
        logger.warning("Nau window unresolved after loading; cannot check z-order")
        return
    covering = windows_obscuring(nau_hwnd, iter_zorder())
    if covering:
        desc = "; ".join(
            f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}" for w in covering
        )
        logger.warning("Nau (hwnd=%d) is covered at startup by: %s", nau_hwnd, desc)
    else:
        logger.info("Nau (hwnd=%d) is frontmost over its rect at startup", nau_hwnd)


def _fix_post_loading_windows(result: StartupResult) -> None:
    """Re-assert the topmost policy + nau-mode visibility after the loading
    screen overlay is destroyed (its teardown can shuffle activation, and
    the dashboard may only become resolvable this late)."""
    dash_hwnd = 0
    if result.dashboard_pid:
        dash_hwnd = find_window_by_pid(result.dashboard_pid)
        if not dash_hwnd:
            dash_hwnd = wait_for_window_by_title("Fun Time", timeout_s=3.0, exact=True)

    nau_hwnd = find_window_by_pid(result.nau_pid) or wait_for_window_by_title(
        "Nau", timeout_s=3.0, exact=True
    )
    _apply_startup_window_state(
        rfb_hwnd=result.rfb_hwnd,
        portrait_hwnd=find_window_by_pid(result.portrait_pid),
        landscape_hwnd=find_window_by_pid(result.landscape_pid),
        genau_hwnd=wait_for_window_by_title("Genau", timeout_s=3.0),
        nau_hwnd=nau_hwnd,
        dashboard_hwnd=dash_hwnd,
    )
    logger.info("Post-loading window state corrected")
    _log_nau_obstruction(nau_hwnd)


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
    show_loading = not integration_mode

    # --- Launch loading screen (normal mode only) ---
    loading_proc = None
    progress_file = state_dir / PROGRESS_FILENAME
    if show_loading:
        progress = StartupProgress(progress_file, total_steps=_STARTUP_PROGRESS_STEPS)
        python_exe = sys.executable
        loading_proc = subprocess.Popen(
            [python_exe, "-m", "fun_time.loading_screen", str(progress_file)],
        )
        logger.info("Loading screen launched (pid=%d)", loading_proc.pid)
    else:
        progress = NullProgress()

    # Clear any stale ready flag from a prior session before the HUD (launched
    # inside the sequence) writes a fresh one, so the wait below can't see an
    # old file and reveal Fun Time before this run's maps are primed.
    hud_ready_file = state_dir / HUD_READY_FILENAME
    hud_ready_file.unlink(missing_ok=True)

    logger.info("Running startup sequence")
    result = run_startup_sequence(
        manifest_path=manifest_path,
        state_dir=state_dir,
        progress=progress,
        hide_windows=show_loading,
    )

    logger.info(
        "Startup complete: nau=%d portrait=%d landscape=%d dashboard=%d genau=%d audio=%d",
        result.nau_pid, result.portrait_pid, result.landscape_pid,
        result.dashboard_pid, result.genau_pid, result.audio_pid,
    )

    # --- Close loading screen (normal mode only) ---
    # The sequencer already positioned all windows in Phase 4 (the reveal).
    if show_loading:
        # Hold the loading screen until the lock HUD has primed its indexes, so
        # Fun Time isn't revealed with the maps still blank.  Capped so a HUD
        # that never signals (or was never launched) can't wedge startup.
        if result.lock_hud_pid:
            if wait_for_hud_ready(hud_ready_file, timeout_s=20.0):
                logger.info("Lock HUD ready; dropping loading screen")
            else:
                logger.warning("Lock HUD not ready after 20s; revealing anyway")
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

    # Clean stale state files from previous sessions so the dispatch loop
    # starts fresh (e.g. omni_paused=True left over from a crash).
    # Start background dispatch loop (dashboard polling + genau sync)
    manifest = configparser.ConfigParser()
    manifest.optionxform = str
    manifest.read(str(manifest_path), encoding="utf-8")
    bridge_config = build_bridge_config_from_manifest(manifest)
    dashboard_cmd_file = Path(manifest["commands"]["dashboard_cmd_file"])
    for stale in (
        state_dir / "shared_bridge_state.ini",
        state_dir / "ahk_cmd.txt",
        dashboard_cmd_file,
        dashboard_cmd_file.with_suffix(".processing"),
    ):
        stale.unlink(missing_ok=True)
    dashboard_enabled = manifest["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"}

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
        shared_state_file=state_dir / "shared_bridge_state.ini",
        ahk_cmd_file=state_dir / "ahk_cmd.txt",
        nau_pid=result.nau_pid,
        portrait_pid=result.portrait_pid,
        landscape_pid=result.landscape_pid,
        dashboard_pid=result.dashboard_pid,
        dashboard_enabled=dashboard_enabled,
        rfb_hwnd=result.rfb_hwnd,
        rfb_shortcut_target=rfb_target,
        rfb_shortcut_work_dir=rfb_work_dir,
        rfb_shortcut_args=rfb_args,
    )
    # Genau startup detection is handled by the dispatch loop's first
    # sync tick: if the broker has already written genau_mode.txt = "1"
    # (it detects auto mode within ~4s via BPM/stroke inference), the sync
    # will detect the entering transition and pause Primary VLC naturally.

    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    logger.info("Background dispatch loop started")

    # Serve the autofill userscript so Tampermonkey can auto-update it
    # instead of needing a hand-paste after every edit. A busy port (a second
    # Fun Time, a leftover server) is not worth failing startup over.
    try:
        serve_userscript_updates()
        logger.info("Userscript update server started on 127.0.0.1:%d", USERSCRIPT_PORT)
    except OSError:
        logger.warning("Userscript update server not started (port %d busy)", USERSCRIPT_PORT, exc_info=True)

    # --- Optional voice control ---
    voice_controller: VoiceController | None = None
    voice_thread: threading.Thread | None = None
    try:
        cfg = load_config(manifest["runtime"]["config_path"])
        voice_diag = (
            f"VOICE_AVAILABLE={VOICE_AVAILABLE}, "
            f"enabled={cfg.voice_control.enabled}, "
            f"model={cfg.voice_control.model_path}, "
            f"device={cfg.voice_control.device_index}"
        )
        logger.info("Voice control check: %s", voice_diag)
        if VOICE_AVAILABLE and cfg.voice_control.enabled:
            voice_controller = VoiceController(
                cmd_file=dashboard_cmd_file,
                model_path=cfg.voice_control.model_path,
                confidence_threshold=cfg.voice_control.confidence_threshold,
                device_index=cfg.voice_control.device_index,
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
        if voice_controller is not None:
            voice_controller.stop()
        if voice_thread is not None:
            voice_thread.join(timeout=2.0)
        dispatch_runner.stop()
        dispatch_thread.join(timeout=2.0)
        logger.info("AHK exited — shutting down child processes")
        _shutdown_children(result.rfb_hwnd, children)

    return exit_code
