from __future__ import annotations

import configparser
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from .vlc_actions import replace_playlist_from_file, set_repeat_mode, vlc_http_cmd, wait_for_http
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



def stop_broker_processes(project_dir: str | Path) -> None:
    """Kill all broker and broker-tray processes without restarting."""
    project_path = Path(project_dir)
    ps_command = (
        "$targets = Get-CimInstance Win32_Process | Where-Object { "
        "(($_.Name -match '^pythonw?\\.exe$|^py\\.exe$') -and $_.CommandLine -match '"
        + BROKER_PROCESS_PATTERN
        + "') -or "
        "(($_.Name -match '^powershell\\.exe$|^pwsh\\.exe$|^wscript\\.exe$') -and $_.CommandLine -match '"
        + BROKER_TRAY_PATTERN
        + "') "
        "}; "
        "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command],
        cwd=project_path,
        check=False,
        **subprocess_window_kwargs(),
    )


def restart_broker(project_dir: str | Path, broker_tray_launcher: Path | None = None) -> None:
    stop_broker_processes(project_dir)
    time.sleep(0.4)
    if broker_tray_launcher and broker_tray_launcher.is_file():
        subprocess.Popen(
            ["wscript.exe", str(broker_tray_launcher)],
            cwd=broker_tray_launcher.parent,
            **subprocess_window_kwargs(),
        )


def prepare_random_favs_browser_manifest(config_path: str | Path, output_path: str | Path) -> None:
    profile_directory, urls = build_manifest(config_path)
    write_manifest(Path(output_path), profile_directory, urls)


def seed_genau_state(paused_file: str | Path, audio_paused_file: str | Path) -> None:
    for path, value in (
        (Path(paused_file), "1"),
        (Path(audio_paused_file), "1"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")


def start_core_session(
    *,
    project_dir: str | Path,
    config_path: str | Path,
    broker_tray_launcher: Path | None = None,
    random_favs_browser_manifest_file: str | Path,
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
    restart_broker(project_dir, broker_tray_launcher)
    seed_genau_state(paused_file, audio_paused_file)
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


def launch_genau(
    *,
    python_exe: str | Path,
    genau_module: str,
    config_path: str | Path,
    clips_folder: str | Path,
    robot_x: int,
    robot_y: int,
    robot_width: int,
    robot_height: int,
) -> int:
    """Launch Genau subprocess, returning its PID."""
    proc = subprocess.Popen(
        [
            str(python_exe),
            "-m",
            genau_module,
            "--config",
            str(config_path),
            "--clips-folder",
            str(clips_folder),
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
    return proc.pid


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
    genau_module: str,
    audio_module: str,
    config_path: str | Path,
    clips_folder: str | Path,
    audio_folder: str | Path,
    robot_x: int,
    robot_y: int,
    robot_width: int,
    robot_height: int,
    genau_pid: int = 0,
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

    if not genau_pid:
        genau_pid = launch_genau(
            python_exe=python_exe,
            genau_module=genau_module,
            config_path=config_path,
            clips_folder=clips_folder,
            robot_x=robot_x,
            robot_y=robot_y,
            robot_width=robot_width,
            robot_height=robot_height,
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
            "genau_pid": genau_pid,
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

    # Playlist files live in the state directory so they persist across launches
    # and can be inspected for debugging.  They keep the VLC command lines well
    # under Windows' 32 767-character limit even with hundreds of video files.
    state_dir = project_dir / "state"
    primary_playlist = state_dir / "vlc_primary_playlist.m3u"
    portrait_playlist = state_dir / "vlc_portrait_playlist.m3u"
    landscape_playlist = state_dir / "vlc_landscape_playlist.m3u"

    launch_kwargs = _no_activate_kwargs()

    # Defer playlist loading whenever VLC is muted (not just during the loading
    # screen).  This eliminates the audio-leak race where VLC outputs a frame
    # of audio before --volume 0 takes effect.  The playlist is loaded via
    # HTTP after volume is confirmed zero.
    should_mute = hide_windows or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"
    primary_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, primary_sources, primary_port, password, repeat_mode="repeat", mute=should_mute,
                                   playlist_path=primary_playlist, defer_playlist=should_mute),
        cwd=project_dir,
        **launch_kwargs,
    )
    if not wait_for_http(primary_port, password, 7000):
        raise RuntimeError("Primary VLC HTTP did not come up")
    time.sleep(0.3)
    if should_mute:
        vlc_http_cmd(primary_port, "volume&val=0", password)
    if should_mute:
        # enqueue_only during loading screen prevents playback; the
        # sequencer's Phase 4 pl_play will start it when ready.
        replace_playlist_from_file(primary_port, password, primary_playlist, enqueue_only=hide_windows)
    if not hide_windows:
        vlc_http_cmd(primary_port, "pl_next", password)

    mfp_proc = subprocess.Popen([mfp_exe], cwd=project_dir, **launch_kwargs)

    portrait_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, portrait_sources, portrait_port, password, repeat_mode="loop", mute=should_mute,
                                   playlist_path=portrait_playlist, defer_playlist=should_mute),
        cwd=project_dir,
        **launch_kwargs,
    )
    landscape_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, landscape_sources, landscape_port, password, repeat_mode="loop", mute=should_mute,
                                   playlist_path=landscape_playlist, defer_playlist=should_mute),
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
    if should_mute:
        vlc_http_cmd(portrait_port, "volume&val=0", password)
    if should_mute:
        replace_playlist_from_file(portrait_port, password, portrait_playlist, enqueue_only=hide_windows)
    if not hide_windows:
        vlc_http_cmd(portrait_port, "pl_next", password)
    time.sleep(0.15)
    if should_mute:
        vlc_http_cmd(landscape_port, "volume&val=0", password)
    if should_mute:
        replace_playlist_from_file(landscape_port, password, landscape_playlist, enqueue_only=hide_windows)
    if not hide_windows:
        vlc_http_cmd(landscape_port, "pl_next", password)

    _write_result_file(
        result_file,
        {
            "primary_pid": primary_proc.pid,
            "mfp_pid": mfp_proc.pid,
            "portrait_pid": portrait_proc.pid,
            "landscape_pid": landscape_proc.pid,
        },
    )



def _build_vlc_launch_command(vlc_exe: str, sources: str, port: int, password: str, *, repeat_mode: str, mute: bool = False, playlist_path: Path | None = None, defer_playlist: bool = False) -> list[str]:
    command = [
        vlc_exe,
        "--no-one-instance",
        "--extraintf",
        "http",
        "--http-host",
        "127.0.0.1",
        "--http-port",
        str(port),
        "--http-password",
        password,
    ]
    if mute or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1":
        command.extend(["--volume", "0"])
        # --start-paused must NEVER be used: VLC re-applies it on every item
        # transition, not just startup. This causes a black screen every time
        # the user navigates.  When defer_playlist is True the playlist is
        # loaded via HTTP after muting, so there is nothing to hear even if
        # --volume 0 has a startup race.
    # --no-random overrides VLC's saved vlcrc setting.  Without it, if the
    # user ever toggled shuffle inside VLC, the preference persists across
    # launches and VLC advances to random items instead of sequentially,
    # breaking vlc_nav_step's index-based prev/next navigation.
    command.append("--no-random")
    if repeat_mode == "repeat":
        command.append("--repeat")
    elif repeat_mode == "loop":
        command.append("--loop")
    sources_list: list[str] = []
    for source in [part for part in sources.split("|") if part]:
        p = Path(source)
        if p.is_dir():
            sources_list.extend(str(f) for f in sorted(p.rglob("*.mp4")))
        else:
            sources_list.append(source)
    random.shuffle(sources_list)
    if playlist_path is not None and sources_list:
        # Always write the .m3u file — it is needed for later HTTP loading
        # even when defer_playlist is True.  This keeps the command line well
        # under Windows' 32 767-character limit when there are hundreds of
        # video files.
        playlist_path = Path(playlist_path)
        playlist_path.parent.mkdir(parents=True, exist_ok=True)
        playlist_path.write_text("\n".join(sources_list) + "\n", encoding="utf-8")
        if not defer_playlist:
            command.append(str(playlist_path))
    elif not defer_playlist:
        command.extend(sources_list)
    return command


