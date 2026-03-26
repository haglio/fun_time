"""Python orchestrator for the Windows bridge.

Runs the full startup sequence, launches the minimal AHK hotkey script,
starts the background dispatch loop, waits for AHK to exit, then shuts
down all child processes.
"""
from __future__ import annotations

import configparser
import logging
import subprocess
import threading
from pathlib import Path

from .windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from .windows_bridge_sequencer import StartupResult, run_startup_sequence

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
        "robot_hand_pid": str(result.robot_hand_pid),
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


def _shutdown_children(result: StartupResult) -> None:
    """Kill all child processes launched during startup."""
    for pid in [
        result.primary_pid,
        result.mfp_pid,
        result.portrait_pid,
        result.landscape_pid,
        result.dashboard_pid,
        result.robot_hand_pid,
        result.audio_pid,
    ]:
        kill_process_tree(pid)


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

    logger.info("Running startup sequence")
    result = run_startup_sequence(
        manifest_path=manifest_path,
        state_dir=state_dir,
    )
    logger.info(
        "Startup complete: primary=%d mfp=%d portrait=%d landscape=%d dashboard=%d robot_hand=%d audio=%d",
        result.primary_pid, result.mfp_pid, result.portrait_pid, result.landscape_pid,
        result.dashboard_pid, result.robot_hand_pid, result.audio_pid,
    )

    pids_file = state_dir / "bridge_pids.ini"
    write_pids_file(pids_file, result)

    # Start background dispatch loop (dashboard polling + robot hand sync)
    manifest = configparser.ConfigParser()
    manifest.optionxform = str
    manifest.read(str(manifest_path), encoding="utf-8")
    bridge_config = build_bridge_config_from_manifest(manifest)
    dashboard_enabled = manifest["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"}

    dispatch_runner = DispatchLoopRunner(
        config=bridge_config,
        dashboard_cmd_file=Path(manifest["commands"]["dashboard_cmd_file"]),
        shared_state_file=state_dir / "shared_bridge_state.ini",
        ahk_cmd_file=state_dir / "ahk_cmd.txt",
        primary_pid=result.primary_pid,
        mfp_pid=result.mfp_pid,
        dashboard_enabled=dashboard_enabled,
    )
    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    logger.info("Background dispatch loop started")

    command = [ahk_exe, hotkey_script, str(manifest_path), str(pids_file)]
    logger.info("Launching AHK hotkey script: %s", " ".join(command))
    ahk_proc = subprocess.Popen(command, cwd=project_dir)

    try:
        exit_code = ahk_proc.wait()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        exit_code = 1
    finally:
        dispatch_runner.stop()
        dispatch_thread.join(timeout=2.0)
        logger.info("AHK exited — shutting down child processes")
        _shutdown_children(result)

    return exit_code
