from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path

from .config import load_config
from .controller_manifest import write_controller_manifest
from .logging_utils import configure_logging, install_exception_logging
from .runtime_support import hidden_subprocess_kwargs

BROKER_PROCESS_PATTERN = "fun_time\\.broker_app"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Launch the Fun Time controller stack.")
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
    config.chrome_overlay_manifest_file.touch(exist_ok=True)


def validate_config(config) -> None:
    require_file(config.paths.vlc_exe)
    require_file(config.paths.mfp_exe)
    require_file(config.paths.ahk_exe)
    require_file(config.paths.python_exe)
    if config.chrome_overlay.enabled:
        require_file(config.chrome_overlay.shortcut_path)
    for primary_vlc_dir in config.paths.primary_vlc_dirs:
        require_dir(primary_vlc_dir)
    for portrait_dir in config.paths.portrait_dirs:
        require_dir(portrait_dir)
    for landscape_dir in config.paths.landscape_dirs:
        require_dir(landscape_dir)
    require_dir(config.paths.clips_dir)
    require_dir(config.paths.audio_dir)
    require_file(config.project_dir / "controller.ahk")
    require_file(config.project_dir / "scripts" / "run_broker_service.ps1")
    require_file(config.project_dir / "fun_time" / "broker_app.py")
    require_file(config.project_dir / "fun_time" / "robot_hand" / "app.py")
    require_file(config.project_dir / "fun_time" / "audio_companion_app.py")


def _subprocess_window_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return hidden_subprocess_kwargs()


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
            "$_.CommandLine -match '" + BROKER_PROCESS_PATTERN + "' "
            "} | Select-Object -First 1; "
            "if ($null -ne $proc) { 'RUNNING' }"
        ),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, **_subprocess_window_kwargs())
    return result.returncode == 0 and "RUNNING" in result.stdout


def start_broker(config, logger) -> subprocess.Popen | None:
    if sys.platform != "win32":
        logger.warning("Broker auto-start is only implemented on Windows")
        return None

    tray_launcher = config.project_dir / "launch_broker_tray.vbs"
    command = ["wscript.exe", str(tray_launcher)]
    logger.warning("Broker was not running; starting %s", tray_launcher)
    return subprocess.Popen(command, cwd=config.project_dir, **_subprocess_window_kwargs())


def ensure_broker_running(config, logger, *, attempts: int = 20, delay_seconds: float = 0.25) -> bool:
    if is_broker_running():
        return True

    start_broker(config, logger)

    for _ in range(max(1, attempts)):
        time.sleep(delay_seconds)
        if is_broker_running():
            logger.info("Broker is now running")
            return True

    logger.warning("Broker did not appear to start successfully")
    return False


def run_controller(config, logger) -> int:
    ahk_script = config.project_dir / "controller.ahk"
    vlc_http_pass = f"fun_time_{secrets.token_hex(6)}"
    manifest_path = write_controller_manifest(config, vlc_http_pass)
    command = [str(config.paths.ahk_exe), str(ahk_script), str(manifest_path)]
    logger.info("Launching AutoHotkey controller using config %s", config.config_path)
    logger.info("VLC HTTP ports: portrait=%s landscape=%s", config.controller.vlc2_http_port, config.controller.vlc3_http_port)

    result = subprocess.run(command, cwd=config.project_dir, check=False)
    logger.info("Controller exited with code %s", result.returncode)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logger = configure_logging("fun_time.orchestrator", config.log_file("orchestrator"), console=True)
    install_exception_logging(logger)

    logger.info("Loaded config from %s", config.config_path)
    ensure_runtime_files(config)
    validate_config(config)

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    ensure_broker_running(config, logger)
    return run_controller(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())
