from __future__ import annotations

import subprocess
from pathlib import Path

from .orchestrator_broker import BROKER_PROCESS_PATTERN, BROKER_TRAY_PATTERN, subprocess_window_kwargs
from .random_favs_browser import build_manifest, write_manifest


def restart_broker(project_dir: str | Path) -> None:
    project_path = Path(project_dir)
    launch_path = project_path / "launch_broker_tray.vbs"
    ps_command = (
        "$targets = Get-CimInstance Win32_Process | Where-Object { "
        "(($_.Name -match '^pythonw?\\.exe$|^py\\.exe$') -and $_.CommandLine -match '"
        + BROKER_PROCESS_PATTERN
        + "') -or "
        "(($_.Name -match '^powershell\\.exe$|^pwsh\\.exe$|^wscript\\.exe$') -and $_.CommandLine -match '"
        + BROKER_TRAY_PATTERN
        + "') "
        "}; "
        "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "Start-Sleep -Milliseconds 400"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command],
        cwd=project_path,
        check=False,
        **subprocess_window_kwargs(),
    )
    if launch_path.is_file():
        subprocess.Popen(
            ["wscript.exe", str(launch_path)],
            cwd=project_path,
            **subprocess_window_kwargs(),
        )


def prepare_random_favs_browser_manifest(config_path: str | Path, output_path: str | Path) -> None:
    profile_directory, urls = build_manifest(config_path)
    write_manifest(Path(output_path), profile_directory, urls)
