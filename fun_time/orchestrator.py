from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import os
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .live_session import publish_live_session
from .manifest import write_windows_bridge_manifest
from .windows_bridge_orchestrator import run_python_orchestrated_bridge
from app_support.logging_utils import configure_logging, install_exception_logging
from . import orchestrator_broker


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Launch the Fun Time Windows bridge stack.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--check", action="store_true", help="Validate config and exit.")
    return ap


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")


def require_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Missing directory: {path}")


def ensure_runtime_files(config) -> None:
    config.paths.state_dir.mkdir(parents=True, exist_ok=True)
    config.paths.weird_dir.mkdir(parents=True, exist_ok=True)
    config.paths.favs_file.parent.mkdir(parents=True, exist_ok=True)
    config.paths.favs_file.touch(exist_ok=True)
    config.random_favs_browser_manifest_file.touch(exist_ok=True)


def validate_config(config) -> None:
    require_file(config.paths.ahk_exe)
    require_file(config.paths.python_exe)
    if config.random_favs_browser.enabled:
        require_file(config.random_favs_browser.shortcut_path)
    for nau_library_dir in config.paths.nau_library_dirs:
        require_dir(nau_library_dir)
    for portrait_dir in config.paths.portrait_dirs:
        require_dir(portrait_dir)
    for landscape_dir in config.paths.landscape_dirs:
        require_dir(landscape_dir)
    require_dir(config.paths.clips_dir)
    require_dir(config.paths.audio_dir)
    require_file(config.project_dir / "windows_bridge_hotkeys.ahk")
    if config.paths.genau_config_path:
        require_file(config.paths.genau_config_path)
    require_file(config.project_dir / "fun_time" / "audio_companion_app.py")



def is_broker_running() -> bool:
    if sys.platform != "win32":
        return False

    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        (
            "$proc = Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match '^pythonw?\\.exe$|^py\\.exe$' -and "
            "$_.CommandLine -match '" + orchestrator_broker.BROKER_PROCESS_PATTERN + "' "
            "} | Select-Object -First 1; "
            "if ($null -ne $proc) { 'RUNNING' }"
        ),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        **orchestrator_broker.subprocess_window_kwargs(),
    )
    return result.returncode == 0 and "RUNNING" in result.stdout


def start_broker(config, logger) -> subprocess.Popen | None:
    if sys.platform != "win32":
        logger.warning("Broker auto-start is only implemented on Windows")
        return None

    launcher = config.paths.broker_tray_launcher
    if not launcher or not launcher.is_file():
        logger.warning("broker_tray_launcher not configured or missing; skipping broker start")
        return None

    command = ["wscript.exe", str(launcher)]
    logger.warning("Broker was not running; starting %s", launcher)
    return subprocess.Popen(
        command,
        cwd=launcher.parent,
        **orchestrator_broker.broker_launch_kwargs(),
    )


def ensure_broker_running(config, logger, *, attempts: int = 20, delay_seconds: float = 0.25) -> bool:
    if is_broker_running():
        return True

    if start_broker(config, logger) is None:
        return False

    for _ in range(max(1, attempts)):
        time.sleep(delay_seconds)
        if is_broker_running():
            logger.info("Broker is now running")
            return True

    logger.warning("Broker did not appear to start successfully")
    return False


def run_windows_bridge(config, logger) -> int:
    manifest_path = write_windows_bridge_manifest(config)
    hotkey_script = config.project_dir / "windows_bridge_hotkeys.ahk"

    logger.info("Launching Python-orchestrated Windows bridge using config %s", config.config_path)

    exit_code = run_python_orchestrated_bridge(
        manifest_path=manifest_path,
        ahk_exe=str(config.paths.ahk_exe),
        hotkey_script=str(hotkey_script),
        state_dir=config.paths.state_dir,
        project_dir=config.project_dir,
    )
    logger.info("Windows bridge exited with code %s", exit_code)
    return exit_code


def _taskbar_pin_dir() -> Path:
    """Return the Windows taskbar pinned-shortcuts folder."""
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"


def stamp_shortcut_aumid() -> None:
    """Set AppUserModelID on the pinned Fun Time taskbar shortcut.

    Searches the Windows taskbar pin folder for shortcuts whose name
    contains "Fun" and stamps them with the AppUserModelID.  Failures
    are logged but never fatal — the app still launches, just without
    the open indicator.
    """
    from .win32 import APP_USER_MODEL_ID, set_shortcut_app_user_model_id

    _log = logging.getLogger(__name__)

    candidates: list[Path] = []

    # Only stamp the pinned taskbar shortcut (outside the repo).
    # The project's Fun Time.lnk is stamped once and committed.
    pin_dir = _taskbar_pin_dir()
    if pin_dir.is_dir():
        for lnk in pin_dir.glob("*.lnk"):
            if lnk.stem.lower() == "fun time":
                candidates.append(lnk)

    for lnk in candidates:
        try:
            set_shortcut_app_user_model_id(str(lnk), APP_USER_MODEL_ID)
            _log.info("Stamped AppUserModelID on %s", lnk)
        except OSError as exc:
            _log.warning("Could not stamp AppUserModelID on %s: %s", lnk, exc)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logger = configure_logging("fun_time.orchestrator", config.log_file("orchestrator"), console=True)
    install_exception_logging(logger)

    from .single_instance import MUTEX_ORCHESTRATOR, mutex_name_for_config, try_acquire_mutex, show_already_running_message
    _mutex_handle = try_acquire_mutex(mutex_name_for_config(MUTEX_ORCHESTRATOR, config.config_path))
    if _mutex_handle is None:
        logger.warning("Another Fun Time instance is already running; exiting")
        show_already_running_message("Another copy of Fun Time is already running.")
        return 1

    logger.info("Loaded config from %s", config.config_path)
    ensure_runtime_files(config)
    validate_config(config)
    # The taskbar pin belongs to the installed app, and lives in %APPDATA% —
    # outside every checkout.  Only the session the pin actually launches has
    # any business relabelling it; a session on some other config (an
    # integration run's temp one, a developer's alternate) would be reaching
    # into the user's shell to stamp a shortcut that points at neither of them.
    if config.config_path == DEFAULT_CONFIG_PATH:
        stamp_shortcut_aumid()

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    # Say we are up before touching anything the whole machine shares — the
    # broker restart on the next line above all — because that window is the only
    # chance an integration run has to see us and get out of the way.  Not before
    # the --check return: validating a config starts no session, and a claim from
    # one would block a run for as long as the checking process lived.  An
    # integration run's own orchestrator stays silent too: the claim is what tells
    # a run to abort, and a run that published one would abort itself.
    if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
        publish_live_session(config.paths.state_dir)

    ensure_broker_running(config, logger)
    return run_windows_bridge(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())

