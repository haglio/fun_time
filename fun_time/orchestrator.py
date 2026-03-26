from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
import os
from pathlib import Path

from .config import load_config
from .broker_ports import ensure_mfp_serial_port, ensure_mfp_vlc_endpoint
from .windows_bridge_manifest import write_windows_bridge_manifest
from .windows_bridge_orchestrator import run_python_orchestrated_bridge
from .logging_utils import configure_logging, install_exception_logging
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
    require_file(config.paths.vlc_exe)
    require_file(config.paths.mfp_exe)
    require_file(config.paths.ahk_exe)
    require_file(config.paths.python_exe)
    if config.random_favs_browser.enabled:
        require_file(config.random_favs_browser.shortcut_path)
    for primary_vlc_dir in config.paths.primary_vlc_dirs:
        require_dir(primary_vlc_dir)
    for portrait_dir in config.paths.portrait_dirs:
        require_dir(portrait_dir)
    for landscape_dir in config.paths.landscape_dirs:
        require_dir(landscape_dir)
    require_dir(config.paths.clips_dir)
    require_dir(config.paths.audio_dir)
    require_file(config.project_dir / "windows_bridge_hotkeys.ahk")
    require_file(config.project_dir / "scripts" / "run_broker_service.ps1")
    require_file(config.project_dir / "fun_time" / "broker_app.py")
    require_file(config.project_dir / "fun_time" / "robot_hand" / "app.py")
    require_file(config.project_dir / "fun_time" / "audio_companion_app.py")
    require_file(config.project_dir / "fun_time" / "media_actions_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_modes_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_lock_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_robot_hand_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_omnipause_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_window_layout_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_random_favs_browser_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_startup_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_dashboard_bridge_app.py")
    require_file(config.project_dir / "fun_time" / "windows_bridge_runtime_flow_app.py")



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


def is_broker_tray_running() -> bool:
    if sys.platform != "win32":
        return False

    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        (
            "$proc = Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match '^powershell\\.exe$|^pwsh\\.exe$|^wscript\\.exe$' -and "
            "$_.CommandLine -match '" + orchestrator_broker.BROKER_TRAY_PATTERN + "' "
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

    if os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1":
        python_exe = str(config.paths.python_exe)
        python_path = Path(python_exe)
        if python_path.name.lower() == "pythonw.exe":
            python_console = python_path.with_name("python.exe")
            if python_console.exists():
                python_path = python_console
        if python_path.exists():
            command = [str(python_path), "-m", "fun_time.broker_app", "--config", str(config.config_path)]
        else:
            command = ["py", "-3", "-m", "fun_time.broker_app", "--config", str(config.config_path)]
        logger.warning("Broker was not running; starting direct integration broker process")
        return subprocess.Popen(
            command,
            cwd=config.project_dir,
            **orchestrator_broker.subprocess_window_kwargs(),
        )

    tray_launcher = config.project_dir / "launch_broker_tray.vbs"
    command = ["wscript.exe", str(tray_launcher)]
    logger.warning("Broker was not running; starting %s", tray_launcher)
    return subprocess.Popen(
        command,
        cwd=config.project_dir,
        **orchestrator_broker.subprocess_window_kwargs(),
    )


def ensure_broker_running(config, logger, *, attempts: int = 20, delay_seconds: float = 0.25) -> bool:
    if os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1":
        if is_broker_running():
            return True

        start_broker(config, logger)

        for _ in range(max(1, attempts)):
            time.sleep(delay_seconds)
            if is_broker_running():
                logger.info("Broker runtime is now running in integration mode")
                return True

        logger.warning("Broker runtime did not appear to start successfully in integration mode")
        return False

    if is_broker_running() and is_broker_tray_running():
        return True

    start_broker(config, logger)

    for _ in range(max(1, attempts)):
        time.sleep(delay_seconds)
        if is_broker_running() and is_broker_tray_running():
            logger.info("Broker and tray are now running")
            return True

    logger.warning("Broker runtime did not appear to start successfully")
    return False


def vlc_http_password_from_vlcrc() -> str | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    vlcrc = Path(appdata) / "vlc" / "vlcrc"
    try:
        with vlcrc.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("http-password="):
                    value = stripped.split("=", 1)[1].strip()
                    return value or None
    except OSError:
        return None
    return None


def resolve_vlc_http_password() -> str:
    return vlc_http_password_from_vlcrc() or f"fun_time_{secrets.token_hex(6)}"


def run_windows_bridge(config, logger) -> int:
    vlc_http_pass = resolve_vlc_http_password()
    manifest_path = write_windows_bridge_manifest(config, vlc_http_pass)
    hotkey_script = config.project_dir / "windows_bridge_hotkeys.ahk"

    logger.info("Launching Python-orchestrated Windows bridge using config %s", config.config_path)
    logger.info("VLC HTTP ports: portrait=%s landscape=%s", config.controller.vlc2_http_port, config.controller.vlc3_http_port)

    exit_code = run_python_orchestrated_bridge(
        manifest_path=manifest_path,
        ahk_exe=str(config.paths.ahk_exe),
        hotkey_script=str(hotkey_script),
        state_dir=config.paths.state_dir,
        project_dir=config.project_dir,
    )
    logger.info("Windows bridge exited with code %s", exit_code)
    return exit_code


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

    ensure_mfp_serial_port(config, logger)
    ensure_mfp_vlc_endpoint(config, logger)
    ensure_broker_running(config, logger)
    return run_windows_bridge(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())

