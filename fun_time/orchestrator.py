"""Fun Time's process entry point: arguments, config, and the single instance.

The bottom of four layers, each of which knows only the one below it — this one
validates and hands off; :mod:`fun_time.windows_bridge_orchestrator` runs a
session's lifecycle; :mod:`fun_time.windows_bridge_sequencer` runs its startup
phases in order; :mod:`fun_time.windows_bridge_startup` launches the children.
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .branch_session import apply_genau_dirs_to_sys_path, apply_origenerator_dir_override
from .config import DEFAULT_CONFIG_PATH, load_config

# Before the bridge imports: a worktree's genau_project_dirs override reaches
# Genau and Nau as subprocess PYTHONPATH, but THIS process — and the dispatch
# loop inside it — resolves player_core through the venv, which is the
# primary's.  A branch leaning on an unlanded player_core change then imports
# code the primary does not have, and the session dies at launch; that is how
# a verification session reached him broken on 2026-08-13.  A no-op in every
# ordinary session, where no override file exists.
apply_genau_dirs_to_sys_path()

from app_support.logging_utils import configure_logging, install_exception_logging

from .manifest import write_windows_bridge_manifest
from .process_identity import prepare_orchestrator_launcher
from .single_instance import (
    MUTEX_ORCHESTRATOR,
    mutex_name_for_config,
    show_already_running_message,
    try_acquire_mutex,
)
from .win32_taskbar import APP_USER_MODEL_ID, set_shortcut_app_user_model_id
from .windows_bridge_orchestrator import run_session


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



def run_windows_bridge(config, logger) -> int:
    manifest_path = write_windows_bridge_manifest(config)
    hotkey_script = config.project_dir / "windows_bridge_hotkeys.ahk"

    logger.info("Launching Python-orchestrated Windows bridge using config %s", config.config_path)

    exit_code = run_session(
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

    The stem has to match "Fun Time" exactly, not merely start with it: the
    pin folder also holds "Fun Time VR.lnk", which launches the VR session and
    must keep an identity of its own.  Stamping by prefix would hand the
    desktop app's windows and the VR session's windows the same AUMID, which
    is Windows' definition of one app — one pinned button for both, lighting
    up whichever of them the user did not start.

    Only the copy under %APPDATA% is ours to touch; nothing in the repo is a
    shortcut, since .lnk is git-ignored here.  Failures are logged but never
    fatal — the app still launches, just without the open indicator.
    """
    _log = logging.getLogger(__name__)

    pin_dir = _taskbar_pin_dir()
    if not pin_dir.is_dir():
        return

    for lnk in pin_dir.glob("*.lnk"):
        if lnk.stem.lower() != "fun time":
            continue
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
    config = apply_origenerator_dir_override(config)
    logger = configure_logging("fun_time.orchestrator", config.log_file("orchestrator"), console=True)
    install_exception_logging(logger)

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
    return run_windows_bridge(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())

