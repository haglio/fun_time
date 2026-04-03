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
from pathlib import Path

from .config import load_config
from .startup_progress import NullProgress, StartupProgress
from .voice_control import VOICE_AVAILABLE, VoiceController, _VOICE_IMPORT_ERROR
from .windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from .windows_bridge_sequencer import StartupResult, run_startup_sequence
from .win32 import (
    activate_window,
    close_window,
    find_window_by_pid,
    get_foreground_window,
    lock_set_foreground_window,
    minimize_window,
    unlock_set_foreground_window,
)

logger = logging.getLogger(__name__)


def write_pids_file(path: Path, result: StartupResult) -> None:
    """Write a pids INI file that the AHK hotkey script reads on startup."""
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["pids"] = {
        "primary_pid": str(result.primary_pid),
        "mfp_pid": str(result.mfp_pid),
        "portrait_pid": str(result.portrait_pid),
        "landscape_pid": str(result.landscape_pid),
        "dashboard_pid": str(result.dashboard_pid),
        "genau_pid": str(result.genau_pid),
        "audio_pid": str(result.audio_pid),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def kill_process_tree(pid: int) -> None:
    """Kill a process and its children via taskkill."""
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


def _minimize_all_windows(result: StartupResult) -> None:
    """Minimize all Fun Time windows so integration tests don't take over the display."""
    for pid in [
        result.primary_pid,
        result.mfp_pid,
        result.portrait_pid,
        result.landscape_pid,
        result.genau_pid,
    ]:
        if not pid:
            continue
        hwnd = find_window_by_pid(pid)
        if hwnd:
            minimize_window(hwnd)
    logger.info("Minimized all windows for integration test run")


# Number of progress steps reported by run_startup_sequence.
# Number of progress steps in hide_windows mode (the only mode with a loading screen).
_STARTUP_PROGRESS_STEPS = 7


def _shutdown_children(result: StartupResult) -> None:
    """Kill all child processes launched during startup."""
    close_window(result.rfb_hwnd)
    for pid in [
        result.primary_pid,
        result.mfp_pid,
        result.portrait_pid,
        result.landscape_pid,
        result.dashboard_pid,
        result.genau_pid,
        result.audio_pid,
    ]:
        kill_process_tree(pid)


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

    integration_mode = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    show_loading = not integration_mode

    # --- Launch loading screen (normal mode only) ---
    loading_proc = None
    progress_file = state_dir / "startup_progress.txt"
    if show_loading:
        progress = StartupProgress(progress_file, total_steps=_STARTUP_PROGRESS_STEPS)
        python_exe = sys.executable
        loading_proc = subprocess.Popen(
            [python_exe, "-m", "fun_time.loading_screen", str(progress_file)],
        )
        logger.info("Loading screen launched (pid=%d)", loading_proc.pid)
    else:
        progress = NullProgress()

    saved_foreground = 0
    if integration_mode:
        saved_foreground = get_foreground_window()
        lock_set_foreground_window()
        logger.info("Locked foreground window (hwnd=%d) for integration startup", saved_foreground)

    logger.info("Running startup sequence")
    try:
        result = run_startup_sequence(
            manifest_path=manifest_path,
            state_dir=state_dir,
            progress=progress,
            hide_windows=show_loading,
        )
    finally:
        if integration_mode:
            unlock_set_foreground_window()

    logger.info(
        "Startup complete: primary=%d mfp=%d portrait=%d landscape=%d dashboard=%d genau=%d audio=%d",
        result.primary_pid, result.mfp_pid, result.portrait_pid, result.landscape_pid,
        result.dashboard_pid, result.genau_pid, result.audio_pid,
    )

    # --- Close loading screen (normal mode only) ---
    # The sequencer already positioned all windows in Phase 4 (the reveal).
    if show_loading:
        progress.finish()
        if loading_proc:
            try:
                loading_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                loading_proc.kill()
                logger.warning("Loading screen did not exit, killed")
        progress_file.unlink(missing_ok=True)

    if integration_mode:
        _minimize_all_windows(result)
        if saved_foreground:
            activate_window(saved_foreground)
            logger.info("Restored foreground window (hwnd=%d) after integration startup", saved_foreground)

    pids_file = state_dir / "bridge_pids.ini"
    write_pids_file(pids_file, result)

    # Clean stale state files from previous sessions so the dispatch loop
    # starts fresh (e.g. omni_paused=True left over from a crash).
    # Start background dispatch loop (dashboard polling + robot hand sync)
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

    dispatch_runner = DispatchLoopRunner(
        config=bridge_config,
        dashboard_cmd_file=Path(manifest["commands"]["dashboard_cmd_file"]),
        shared_state_file=state_dir / "shared_bridge_state.ini",
        ahk_cmd_file=state_dir / "ahk_cmd.txt",
        primary_pid=result.primary_pid,
        mfp_pid=result.mfp_pid,
        portrait_pid=result.portrait_pid,
        landscape_pid=result.landscape_pid,
        dashboard_pid=result.dashboard_pid,
        dashboard_enabled=dashboard_enabled,
        rfb_hwnd=result.rfb_hwnd,
        genau_pid=result.genau_pid,
    )
    # Genau startup detection is handled by the dispatch loop's first
    # sync tick: if the broker has already written genau_mode.txt = "1"
    # (it detects auto mode within ~4s via BPM/stroke inference), the sync
    # will detect the entering transition and pause Primary VLC naturally.

    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    logger.info("Background dispatch loop started")

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
        _shutdown_children(result)

    return exit_code
