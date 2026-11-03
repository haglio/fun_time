from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.win32 import mutex_name, stamp_pinned_shortcuts

from .branch_session import describe_the_session_on_the_taskbar
from .checkout_overrides import apply_this_checkouts_overrides
from .config import DEFAULT_CONFIG_PATH, load_config
from .event_log import open_event_log
from .funestra_deaths import show_funestra_died_alert
from .funestra_engine import engine_missing_abort
from .headset_power import put_the_headset_to_sleep, wake_the_headset
from .manifest import write_windows_bridge_manifest
from .process_identity import prepare_orchestrator_launcher
from .project_paths import HOTKEY_SCRIPT_NAME, PROJECT_DIR
from .raising_the_loading_screen import RaisedLoadingScreen, open_the_loading_screen
from .session_environment import SessionEnvironment
from .session_handoff import (
    clear_handoff_request,
    hand_over_if_asked,
    keep_the_transition_screen,
    pending_handoff,
)
from .single_instance import (
    MUTEX_ORCHESTRATOR,
    claim_the_session,
    show_already_running_message,
)
from .win32_taskbar import APP_USER_MODEL_ID

if TYPE_CHECKING:
    from .windows_bridge_orchestrator import SessionOver


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Launch the Fun Time Windows bridge stack.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--check", action="store_true", help="Validate config and exit.")
    ap.add_argument("--no-cancel", action="store_true",
                    help="The way back from a crossing Esc called off: offer no Esc.")
    return ap


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")


def require_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Missing directory: {path}")


def ensure_runtime_files(config) -> None:
    config.paths.state_dir.mkdir(parents=True, exist_ok=True)
    config.paths.trash_dir.mkdir(parents=True, exist_ok=True)
    config.random_favs_browser_manifest_file.touch(exist_ok=True)


def validate_config(config) -> None:
    require_file(config.paths.ahk_exe)
    require_file(config.paths.python_exe)
    if config.random_favs_browser.enabled:
        require_file(config.random_favs_browser.shortcut_path)
    for main_funestra_library_dir in config.paths.main_funestra_library_dirs:
        require_dir(main_funestra_library_dir)
    for portrait_dir in config.paths.portrait_dirs:
        require_dir(portrait_dir)
    for landscape_dir in config.paths.landscape_dirs:
        require_dir(landscape_dir)
    require_dir(config.paths.flicks_dir)
    require_dir(config.paths.genau_audio_dir)
    require_file(config.project_dir / HOTKEY_SCRIPT_NAME)
    require_file(config.project_dir / "fun_time" / "audio_companion_app.py")


def run_windows_bridge(config, logger, env: SessionEnvironment, loading_screen: RaisedLoadingScreen) -> SessionOver:
    from .windows_bridge_orchestrator import run_session  # noqa: PLC0415

    manifest_path = write_windows_bridge_manifest(
        config, dashboard_enabled=env.dashboard_enabled)
    hotkey_script = config.project_dir / HOTKEY_SCRIPT_NAME

    logger.info("Launching Python-orchestrated Windows bridge using config %s", config.config_path)

    ended = run_session(
        manifest_path=manifest_path,
        ahk_exe=str(config.paths.ahk_exe),
        hotkey_script=str(hotkey_script),
        state_dir=config.paths.state_dir,
        project_dir=config.project_dir,
        loading_screen=loading_screen,
        env=env,
    )
    logger.info("Windows bridge exited with code %s", ended.exit_code)
    return ended


def stamp_shortcut_aumid() -> None:
    _log = logging.getLogger(__name__)
    for pin, refusal in stamp_pinned_shortcuts(APP_USER_MODEL_ID, ["Fun Time"]).items():
        if refusal is None:
            _log.info("Stamped AppUserModelID on %s", pin)
        else:
            _log.warning("Could not stamp AppUserModelID on %s: %s", pin, refusal)


STARTUP_MARKER_NAME = "launcher.ready"


def startup_marker_path(config, marker_name: str = STARTUP_MARKER_NAME) -> Path:
    return config.paths.state_dir / marker_name


def signal_startup_resolved(config, marker_name: str = STARTUP_MARKER_NAME) -> None:
    marker = startup_marker_path(config, marker_name)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ready\n", encoding="utf-8")
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not write startup marker %s", marker, exc_info=True
        )


def set_up_logging(config) -> logging.Logger:
    logger = configure_logging(
        "fun_time.orchestrator", config.log_file("orchestrator"), console=True)
    install_exception_logging(logger)
    return logger


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = SessionEnvironment.from_environ(os.environ)
    config = load_config(args.config)
    config = apply_this_checkouts_overrides(config, integration=env.integration)
    state_dir = config.paths.state_dir
    logger = set_up_logging(config)

    _mutex_handle = claim_the_session(mutex_name(MUTEX_ORCHESTRATOR, config.instance_id))
    if _mutex_handle is None:
        logger.warning("Another Fun Time instance is already running; exiting")
        signal_startup_resolved(config)
        show_already_running_message("Another copy of Fun Time is already running.")
        return 1

    logger.info("Loaded config from %s", config.config_path)
    keep_the_transition_screen(state_dir)
    ensure_runtime_files(config)
    clear_handoff_request(state_dir)
    validate_config(config)

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    open_event_log(state_dir)
    loading_screen = open_the_loading_screen(
        state_dir, show_overlays=env.show_overlays,
        sibling_checkouts=config.paths.sibling_checkouts_path, cancelable=not args.no_cancel,
    )
    try:
        if config.config_path == DEFAULT_CONFIG_PATH:
            stamp_shortcut_aumid()
        describe_the_session_on_the_taskbar(PROJECT_DIR)

        loading_screen.progress.announce("engine")
        if engine_missing_abort(config, log=logger.error, uncover=loading_screen.take_it_down):
            signal_startup_resolved(config)
            return 1

        signal_startup_resolved(config)
        prepare_orchestrator_launcher()
        wake_the_headset(state_dir, integration=env.integration)
        ended = run_windows_bridge(config, logger, env, loading_screen)
    except BaseException:
        loading_screen.take_it_down()
        put_the_headset_to_sleep(state_dir, crossing=False)
        raise
    put_the_headset_to_sleep(state_dir, crossing=pending_handoff(state_dir) is not None)
    if ended.why:
        show_funestra_died_alert(ended.why)
    hand_over_if_asked(config, logger)
    return ended.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

