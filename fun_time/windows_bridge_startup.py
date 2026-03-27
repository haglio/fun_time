from __future__ import annotations

import configparser
import json
import os
import subprocess
import time
from pathlib import Path

import sys

from .vlc_actions import set_repeat_mode, vlc_http_cmd, wait_for_http
from .orchestrator_broker import BROKER_PROCESS_PATTERN, BROKER_TRAY_PATTERN, subprocess_window_kwargs
from .random_favs_browser import build_manifest, write_manifest


def _no_activate_kwargs() -> dict:
    """Return Popen kwargs that show the window without stealing focus.

    Uses SW_SHOWNOACTIVATE (4) so GUI apps open visible but don't take
    foreground focus.  Only applied during integration test runs.
    """
    if sys.platform != "win32":
        return {}
    if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4  # SW_SHOWNOACTIVATE
    return {"startupinfo": si}


def _write_result_file(result_file: str | Path, values: dict[str, int | str]) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {key: str(value) for key, value in values.items()}
    result_path = Path(result_file)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def _integration_direct_broker_start_enabled() -> bool:
    return os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"


def _resolve_broker_python_exe(config_path: str | Path) -> list[str]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    python_exe = str(config.get("paths", {}).get("python_exe", "")).strip()
    if python_exe:
        python_path = Path(python_exe)
        if not python_path.is_absolute():
            python_path = (Path(config_path).resolve().parent / python_path).resolve()
        if python_path.name.lower() == "pythonw.exe":
            python_console = python_path.with_name("python.exe")
            if python_console.exists():
                python_path = python_console
        if python_path.exists():
            return [str(python_path)]
    return ["py", "-3"]


def _start_broker_process_direct(config_path: str | Path) -> subprocess.Popen[bytes]:
    config_path = Path(config_path).resolve()
    command = [*_resolve_broker_python_exe(config_path), "-m", "fun_time.broker_app", "--config", str(config_path)]
    return subprocess.Popen(
        command,
        cwd=config_path.parent,
        **subprocess_window_kwargs(),
    )


def restart_broker(project_dir: str | Path, config_path: str | Path | None = None) -> None:
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
    if _integration_direct_broker_start_enabled() and config_path is not None:
        _start_broker_process_direct(config_path)
        return
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


def start_core_session(
    *,
    project_dir: str | Path,
    config_path: str | Path,
    random_favs_browser_manifest_file: str | Path,
    enabled_file: str | Path,
    paused_file: str | Path,
    audio_paused_file: str | Path,
    vlc_exe: str | Path,
    mfp_exe: str | Path,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    primary_port: int,
    portrait_port: int,
    landscape_port: int,
    password: str,
    result_file: str | Path,
    hide_windows: bool = False,
) -> None:
    restart_broker(project_dir, config_path)
    seed_robot_hand_state(enabled_file, paused_file, audio_paused_file)
    prepare_random_favs_browser_manifest(config_path, random_favs_browser_manifest_file)
    launch_core_apps(
        project_dir=project_dir,
        vlc_exe=vlc_exe,
        mfp_exe=mfp_exe,
        primary_sources=primary_sources,
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        primary_port=primary_port,
        portrait_port=portrait_port,
        landscape_port=landscape_port,
        password=password,
        result_file=result_file,
        hide_windows=hide_windows,
    )


def launch_ui_companions(
    *,
    python_exe: str | Path,
    dashboard_module: str,
    dashboard_enabled: bool,
    windows_bridge_manifest_path: str | Path,
    dashboard_x: int,
    dashboard_y: int,
    dashboard_width: int,
    dashboard_height: int,
    mfp_pid: int,
    robot_hand_module: str,
    audio_module: str,
    config_path: str | Path,
    clips_folder: str | Path,
    audio_folder: str | Path,
    robot_x: int,
    robot_y: int,
    robot_width: int,
    robot_height: int,
    result_file: str | Path,
) -> None:
    python_exe = str(python_exe)
    windows_bridge_manifest_path = str(windows_bridge_manifest_path)
    config_path = str(config_path)
    clips_folder = str(clips_folder)
    audio_folder = str(audio_folder)

    dashboard_pid = 0
    if dashboard_enabled:
        dashboard_proc = subprocess.Popen(
            [
                python_exe,
                "-m",
                dashboard_module,
                windows_bridge_manifest_path,
                "--x",
                str(dashboard_x),
                "--y",
                str(dashboard_y),
                "--width",
                str(dashboard_width),
                "--height",
                str(dashboard_height),
                "--mfp-pid",
                str(mfp_pid),
            ],
            **subprocess_window_kwargs(),
        )
        dashboard_pid = dashboard_proc.pid

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
            str(robot_x),
            "--y",
            str(robot_y),
            "--width",
            str(robot_width),
            "--height",
            str(robot_height),
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

    _write_result_file(
        result_file,
        {
            "dashboard_pid": dashboard_pid,
            "robot_hand_pid": robot_proc.pid,
            "audio_pid": audio_proc.pid,
        },
    )


def launch_core_apps(
    *,
    project_dir: str | Path,
    vlc_exe: str | Path,
    mfp_exe: str | Path,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    primary_port: int,
    portrait_port: int,
    landscape_port: int,
    password: str,
    result_file: str | Path,
    hide_windows: bool = False,
) -> None:
    project_dir = Path(project_dir)
    vlc_exe = str(vlc_exe)
    mfp_exe = str(mfp_exe)

    launch_kwargs = _no_activate_kwargs()

    primary_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, primary_sources, primary_port, password, repeat_mode="repeat"),
        cwd=project_dir,
        **launch_kwargs,
    )
    if not wait_for_http(primary_port, password, 7000):
        raise RuntimeError("Primary VLC HTTP did not come up")
    time.sleep(0.3)
    vlc_http_cmd(primary_port, "pl_next", password)
    if hide_windows:
        vlc_http_cmd(primary_port, "volume&val=0", password)

    mfp_proc = subprocess.Popen([mfp_exe], cwd=project_dir, **launch_kwargs)

    portrait_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, portrait_sources, portrait_port, password, repeat_mode="loop"),
        cwd=project_dir,
        **launch_kwargs,
    )
    landscape_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, landscape_sources, landscape_port, password, repeat_mode="loop"),
        cwd=project_dir,
        **launch_kwargs,
    )

    if not wait_for_http(portrait_port, password, 7000):
        raise RuntimeError("Portrait VLC HTTP did not come up")
    if not wait_for_http(landscape_port, password, 7000):
        raise RuntimeError("Landscape VLC HTTP did not come up")

    set_repeat_mode(portrait_port, password, "all")
    set_repeat_mode(landscape_port, password, "all")

    time.sleep(0.25)
    vlc_http_cmd(portrait_port, "pl_next", password)
    if hide_windows:
        vlc_http_cmd(portrait_port, "volume&val=0", password)
    time.sleep(0.15)
    vlc_http_cmd(landscape_port, "pl_next", password)
    if hide_windows:
        vlc_http_cmd(landscape_port, "volume&val=0", password)

    _write_result_file(
        result_file,
        {
            "primary_pid": primary_proc.pid,
            "mfp_pid": mfp_proc.pid,
            "portrait_pid": portrait_proc.pid,
            "landscape_pid": landscape_proc.pid,
        },
    )



def _build_vlc_launch_command(vlc_exe: str, sources: str, port: int, password: str, *, repeat_mode: str) -> list[str]:
    command = [
        vlc_exe,
        "--no-one-instance",
        "--random",
        "--extraintf",
        "http",
        "--http-host",
        "127.0.0.1",
        "--http-port",
        str(port),
        "--http-password",
        password,
    ]
    if os.environ.get("FUN_TIME_MUTE_AUDIO") == "1":
        command.extend(["--volume", "0"])
    if repeat_mode == "repeat":
        command.append("--repeat")
    elif repeat_mode == "loop":
        command.append("--loop")
    command.extend([part for part in sources.split("|") if part])
    return command


