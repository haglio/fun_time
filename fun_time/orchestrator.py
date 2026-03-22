from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path

from .config import load_config
from .logging_utils import configure_logging, install_exception_logging

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
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


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

    runner_script = config.project_dir / "scripts" / "run_broker_service.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(runner_script),
    ]
    logger.warning("Broker was not running; starting %s", runner_script)
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


def build_controller_args(config, vlc_http_pass: str) -> list[str]:
    layout = config.controller.layout
    primary_vlc_dirs_arg = "|".join(str(path) for path in config.paths.primary_vlc_dirs)
    portrait_dirs_arg = "|".join(str(path) for path in config.paths.portrait_dirs)
    landscape_dirs_arg = "|".join(str(path) for path in config.paths.landscape_dirs)
    return [
        str(config.paths.vlc_exe),
        str(config.paths.mfp_exe),
        primary_vlc_dirs_arg,
        portrait_dirs_arg,
        landscape_dirs_arg,
        str(config.paths.weird_dir),
        str(config.paths.favs_file),
        str(config.controller.vlc2_http_port),
        str(config.controller.vlc3_http_port),
        vlc_http_pass,
        str(config.paths.python_exe),
        "fun_time.robot_hand.app",
        str(config.paths.clips_dir),
        "fun_time.audio_companion_app",
        str(config.paths.audio_dir),
        str(config.robot_hand_mode_file),
        str(config.robot_hand_cmd_file),
        str(config.broker_cmd_file),
        str(config.audio_cmd_file),
        str(layout.primary_monitor),
        str(layout.secondary_monitor),
        str(layout.primary_top_ratio),
        str(layout.landscape_width_ratio),
        str(layout.mfp_width_ratio),
        str(layout.mfp_height_ratio),
        str(config.log_file("controller")),
        str(config.chrome_overlay.shortcut_path),
        str(config.chrome_overlay_manifest_file),
        str(config.config_path),
    ]


def run_controller(config, logger) -> int:
    ahk_script = config.project_dir / "controller.ahk"
    vlc_http_pass = f"fun_time_{secrets.token_hex(6)}"
    command = [str(config.paths.ahk_exe), str(ahk_script), *build_controller_args(config, vlc_http_pass)]
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
