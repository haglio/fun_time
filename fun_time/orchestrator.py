from __future__ import annotations

import argparse
import logging
import subprocess
import time
import os
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .branch_session import apply_genau_dirs_to_sys_path

# Before the bridge imports: a worktree's genau_project_dirs override reaches
# Genau and Nau as subprocess PYTHONPATH, but THIS process — and the dispatch
# loop inside it — resolves player_core through the venv, which is the
# primary's.  A branch leaning on an unlanded player_core change then imports
# code the primary does not have, and the session dies at launch; that is how
# a verification session reached him broken on 2026-08-13.  A no-op in every
# ordinary session, where no override file exists.
apply_genau_dirs_to_sys_path()

from .manifest import write_windows_bridge_manifest
from .process_identity import prepare_orchestrator_launcher
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


def _is_the_fun_time_pin(lnk: Path) -> bool:
    """Whether *lnk* is the taskbar pin this app puts its AppUserModelID on.

    The stem, exactly: a pin named "Fun Time (branch).lnk" or "FunTime.lnk" is
    somebody else's and is left alone.
    """
    return lnk.stem.lower() == "fun time"


def stamp_shortcut_aumid() -> None:
    """Set AppUserModelID on the pinned Fun Time taskbar shortcut.

    Failures are logged but never fatal — the app still launches, just without
    the open indicator.
    """
    from .win32 import APP_USER_MODEL_ID, set_shortcut_app_user_model_id

    _log = logging.getLogger(__name__)

    candidates: list[Path] = []

    # The pin folder is outside the repo; nothing in the checkout is stamped
    # (*.lnk is gitignored, so there is no shortcut here to stamp).
    pin_dir = _taskbar_pin_dir()
    if pin_dir.is_dir():
        candidates.extend(lnk for lnk in pin_dir.glob("*.lnk") if _is_the_fun_time_pin(lnk))

    for lnk in candidates:
        try:
            set_shortcut_app_user_model_id(str(lnk), APP_USER_MODEL_ID)
            _log.info("Stamped AppUserModelID on %s", lnk)
        except OSError as exc:
            _log.warning("Could not stamp AppUserModelID on %s: %s", lnk, exc)


STARTUP_MARKER_NAME = "launcher.ready"


def startup_marker_path(config, marker_name: str = STARTUP_MARKER_NAME) -> Path:
    """Where the launcher looks to decide whether the launch succeeded.

    Each launcher watches its own marker (``launch.vbs`` this default,
    ``launch_vr.vbs`` FunTimeVR's), so one app's leftover can never vouch for
    the other's launch.
    """
    return config.paths.state_dir / marker_name


def signal_startup_resolved(config, marker_name: str = STARTUP_MARKER_NAME) -> None:
    """Tell ``launch.vbs`` that startup reached a resolved state.

    The launcher runs the orchestrator hidden, so it can only tell a good
    launch from a silent crash by watching for this marker.  We drop it once
    config has validated and we are committing to run -- or once we have shown
    the user our own "already running" message.  Every silent failure the
    launcher exists to surface (an import-time crash, a bad config, a missing
    library dir) happens *before* this point and leaves the marker absent,
    which is the launcher's cue to pop the log.  A failure to write it must
    never take the launch down with it, so it is only logged.
    """
    marker = startup_marker_path(config, marker_name)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ready\n", encoding="utf-8")
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not write startup marker %s", marker, exc_info=True
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    # A worktree's own answer to "which Origenerator does this session host" —
    # the counterpart of the genau override applied at the top of this module,
    # for the same reason: the machine's one config must not be repointed at an
    # unlanded branch for every session on the machine.
    from .branch_session import apply_origenerator_dir_override
    config = apply_origenerator_dir_override(config)
    logger = configure_logging("fun_time.orchestrator", config.log_file("orchestrator"), console=True)
    install_exception_logging(logger)

    from .single_instance import MUTEX_ORCHESTRATOR, mutex_name_for_config, try_acquire_mutex, show_already_running_message
    _mutex_handle = try_acquire_mutex(mutex_name_for_config(MUTEX_ORCHESTRATOR, config.instance_id))
    if _mutex_handle is None:
        logger.warning("Another Fun Time instance is already running; exiting")
        # The user got a message of our own; keep the launcher from adding a
        # second, misleading "failed to start" dialog on top of it.
        signal_startup_resolved(config)
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

    # Config validated and we are committing to launch the stack: past here any
    # crash is logged through the excepthook installed above, so the launcher's
    # silent-failure watch has done its job.
    signal_startup_resolved(config)
    # Leave the launcher a named interpreter to start the NEXT session through.
    # Every child below is named as it is launched; this one process cannot be,
    # because it is the one doing the naming -- see prepare_orchestrator_launcher.
    prepare_orchestrator_launcher()
    ensure_broker_running(config, logger)
    return run_windows_bridge(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())

