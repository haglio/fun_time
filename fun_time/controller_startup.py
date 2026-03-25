from __future__ import annotations

import configparser
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


def seed_robot_hand_state(enabled_file: str | Path, paused_file: str | Path, audio_paused_file: str | Path) -> None:
    for path, value in (
        (Path(enabled_file), "1"),
        (Path(paused_file), "1"),
        (Path(audio_paused_file), "1"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")


def launch_runtime_companions(
    *,
    python_exe: str | Path,
    robot_hand_module: str,
    audio_module: str,
    config_path: str | Path,
    clips_folder: str | Path,
    audio_folder: str | Path,
    x: int,
    y: int,
    width: int,
    height: int,
    result_file: str | Path,
) -> None:
    python_exe = str(python_exe)
    config_path = str(config_path)
    clips_folder = str(clips_folder)
    audio_folder = str(audio_folder)

    robot_proc = subprocess.Popen(
        [
            python_exe,
            "-m",
            robot_hand_module,
            "--config",
            config_path,
            "--clips-folder",
            clips_folder,
            "--x",
            str(x),
            "--y",
            str(y),
            "--width",
            str(width),
            "--height",
            str(height),
        ],
        **subprocess_window_kwargs(),
    )
    audio_proc = subprocess.Popen(
        [
            python_exe,
            "-m",
            audio_module,
            "--config",
            config_path,
            "--audio-folder",
            audio_folder,
        ],
        **subprocess_window_kwargs(),
    )

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {
        "robot_hand_pid": str(robot_proc.pid),
        "audio_pid": str(audio_proc.pid),
    }
    result_path = Path(result_file)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as fp:
        parser.write(fp)
