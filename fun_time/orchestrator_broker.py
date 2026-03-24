from __future__ import annotations

import subprocess
import sys
import time

from .runtime_support import hidden_subprocess_kwargs

BROKER_PROCESS_PATTERN = "fun_time\\.broker_app"
BROKER_TRAY_PATTERN = "broker_tray\\.ps1|launch_broker_tray\\.vbs"


def subprocess_window_kwargs() -> dict:
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
    result = subprocess.run(command, capture_output=True, text=True, check=False, **subprocess_window_kwargs())
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
            "$_.CommandLine -match '" + BROKER_TRAY_PATTERN + "' "
            "} | Select-Object -First 1; "
            "if ($null -ne $proc) { 'RUNNING' }"
        ),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, **subprocess_window_kwargs())
    return result.returncode == 0 and "RUNNING" in result.stdout


def start_broker(config, logger) -> subprocess.Popen | None:
    if sys.platform != "win32":
        logger.warning("Broker auto-start is only implemented on Windows")
        return None

    tray_launcher = config.project_dir / "launch_broker_tray.vbs"
    command = ["wscript.exe", str(tray_launcher)]
    logger.warning("Broker was not running; starting %s", tray_launcher)
    return subprocess.Popen(command, cwd=config.project_dir, **subprocess_window_kwargs())


def ensure_broker_running(config, logger, *, attempts: int = 20, delay_seconds: float = 0.25) -> bool:
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
