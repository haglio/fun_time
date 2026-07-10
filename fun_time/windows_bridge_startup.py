from __future__ import annotations

import configparser
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import load_config
from .modes import SatelliteLibraryContext, build_fmode_playlists
from .watch_stats import watch_stats_path
from .vlc_actions import replace_playlist_from_file, set_repeat_mode, vlc_http_cmd, wait_for_http
from .orchestrator_broker import BROKER_PROCESS_PATTERN, BROKER_TRAY_PATTERN, subprocess_window_kwargs
from .random_favs_browser import build_manifest, write_manifest
from .rfb_tab_page import write_tab_pages


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
    """Pick this session's favourites and record the tabs Chrome should open.

    Lazy loading puts a local landing page in front of each favourite, so ten
    heavy generate pages do not all load at startup.
    """
    config = load_config(config_path)
    profile_directory, targets = build_manifest(config)
    urls = (
        write_tab_pages(config.random_favs_browser_tabs_dir, targets)
        if config.random_favs_browser.lazy_load
        else [target.url for target in targets]
    )
    write_manifest(Path(output_path), profile_directory, urls)


def seed_paused_states(
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    nau_paused_file: str | Path,
) -> None:
    """Seed pause flags for the startup mode (nau): Genau parked, Nau paused
    until the sequencer's reveal unpauses it."""
    for path, value in (
        (Path(genau_paused_file), "1"),
        (Path(audio_paused_file), "1"),
        (Path(nau_paused_file), "1"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")


def start_core_session(
    *,
    project_dir: str | Path,
    config_path: str | Path,
    broker_tray_launcher: Path | None = None,
    random_favs_browser_manifest_file: str | Path,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    nau_paused_file: str | Path,
    vlc_exe: str | Path,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    portrait_port: int,
    landscape_port: int,
    password: str,
    result_file: str | Path,
    hide_windows: bool = False,
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
) -> None:
    restart_broker(project_dir, broker_tray_launcher)
    seed_paused_states(genau_paused_file, audio_paused_file, nau_paused_file)
    prepare_random_favs_browser_manifest(config_path, random_favs_browser_manifest_file)
    # One playlist authority: the same builder the F-mode toggle uses writes
    # the three VLC playlists and Nau's video/funscript pair list.
    playlist_plan = build_fmode_playlists(
        primary_sources=primary_sources,
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        enabled=False,
        library=SatelliteLibraryContext(
            media_root=provider_media_root,
            metadata_root=provider_metadata_root,
            watch_stats_file=watch_stats_path(state_dir),
        ),
    )
    launch_core_apps(
        project_dir=project_dir,
        vlc_exe=vlc_exe,
        portrait_playlist=playlist_plan.portrait_playlist_path,
        landscape_playlist=playlist_plan.landscape_playlist_path,
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
    genau_x: int,
    genau_y: int,
    genau_width: int,
    genau_height: int,
    command_file: str | Path | None = None,
    paused_file: str | Path | None = None,
) -> int:
    """Launch Genau subprocess, returning its PID."""
    cmd = [
        str(python_exe),
        "-m",
        genau_module,
        "--config",
        str(config_path),
        "--clips-folder",
        str(clips_folder),
        "--x",
        str(genau_x),
        "--y",
        str(genau_y),
        "--width",
        str(genau_width),
        "--height",
        str(genau_height),
    ]
    cmd.append("--fun-time")
    if command_file is not None:
        cmd.extend(["--command-file", str(command_file)])
    if paused_file is not None:
        cmd.extend(["--paused-file", str(paused_file)])
    proc = subprocess.Popen(cmd, **subprocess_window_kwargs())
    return proc.pid


def launch_nau(
    *,
    python_exe: str | Path,
    nau_module: str,
    config_path: str | Path,
    playlist_file: str | Path,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    nau_x: int,
    nau_y: int,
    nau_width: int,
    nau_height: int,
) -> int:
    """Launch Nau subprocess, returning its PID."""
    cmd = [
        str(python_exe),
        "-m",
        nau_module,
        "--config",
        str(config_path),
        "--playlist",
        str(playlist_file),
        "--command-file",
        str(command_file),
        "--paused-file",
        str(paused_file),
        "--status-file",
        str(status_file),
        "--x",
        str(nau_x),
        "--y",
        str(nau_y),
        "--width",
        str(nau_width),
        "--height",
        str(nau_height),
    ]
    proc = subprocess.Popen(cmd, **subprocess_window_kwargs())
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
    rfb_x: int,
    rfb_y: int,
    rfb_width: int,
    rfb_height: int,
    log_x: int,
    log_y: int,
    log_width: int,
    log_height: int,
    audio_module: str,
    config_path: str | Path,
    audio_folder: str | Path,
    result_file: str | Path,
) -> None:
    python_exe = str(python_exe)
    windows_bridge_manifest_path = str(windows_bridge_manifest_path)
    config_path = str(config_path)
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
                "--rfb-x",
                str(rfb_x),
                "--rfb-y",
                str(rfb_y),
                "--rfb-width",
                str(rfb_width),
                "--rfb-height",
                str(rfb_height),
                "--log-x",
                str(log_x),
                "--log-y",
                str(log_y),
                "--log-width",
                str(log_width),
                "--log-height",
                str(log_height),
            ],
            **subprocess_window_kwargs(),
        )
        dashboard_pid = dashboard_proc.pid

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
            "audio_pid": audio_proc.pid,
        },
    )


# A full integration run spawns and kills VLC many times; on a loaded machine
# the HTTP interface occasionally takes several seconds longer than a naive
# fixed timeout to bind.  Wait generously — _await_vlc_http still fails fast if
# the process dies, so the ceiling only bites a genuinely hung-but-alive VLC.
_VLC_HTTP_BIND_TIMEOUT_MS = 20000


def _await_vlc_http(port: int, password: str, proc, label: str) -> None:
    """Block until VLC's HTTP interface binds, or fail with a precise error.

    Waits up to _VLC_HTTP_BIND_TIMEOUT_MS, but only while *proc* is alive: a
    VLC that has already exited will never bind, so we surface its exit code
    immediately instead of waiting out the whole timeout.
    """
    if wait_for_http(port, password, _VLC_HTTP_BIND_TIMEOUT_MS, is_alive=lambda: proc.poll() is None):
        return
    if proc.poll() is not None:
        raise RuntimeError(f"{label} VLC exited before its HTTP interface bound (exit code {proc.returncode})")
    raise RuntimeError(f"{label} VLC HTTP did not come up within {_VLC_HTTP_BIND_TIMEOUT_MS // 1000}s")


def launch_core_apps(
    *,
    project_dir: str | Path,
    vlc_exe: str | Path,
    portrait_playlist: str | Path,
    landscape_playlist: str | Path,
    portrait_port: int,
    landscape_port: int,
    password: str,
    result_file: str | Path,
    hide_windows: bool = False,
) -> None:
    project_dir = Path(project_dir)
    vlc_exe = str(vlc_exe)

    # Defer playlist loading whenever VLC is muted (not just during the loading
    # screen).  This eliminates the audio-leak race where VLC outputs a frame
    # of audio before --volume 0 takes effect.  The playlist is loaded via
    # HTTP after volume is confirmed zero.
    should_mute = hide_windows or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"

    portrait_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, portrait_port, password, repeat_mode="loop", mute=should_mute,
                                   playlist_path=None if should_mute else Path(portrait_playlist)),
        cwd=project_dir,
    )
    landscape_proc = subprocess.Popen(
        _build_vlc_launch_command(vlc_exe, landscape_port, password, repeat_mode="loop", mute=should_mute,
                                   playlist_path=None if should_mute else Path(landscape_playlist)),
        cwd=project_dir,
    )

    _await_vlc_http(portrait_port, password, portrait_proc, "Portrait")
    _await_vlc_http(landscape_port, password, landscape_proc, "Landscape")

    set_repeat_mode(portrait_port, password, "all")
    set_repeat_mode(landscape_port, password, "all")

    time.sleep(0.25)
    if should_mute:
        vlc_http_cmd(portrait_port, "volume&val=0", password)
    if should_mute:
        replace_playlist_from_file(portrait_port, password, Path(portrait_playlist), enqueue_only=hide_windows)
    if not hide_windows:
        vlc_http_cmd(portrait_port, "pl_next", password)
    time.sleep(0.15)
    if should_mute:
        vlc_http_cmd(landscape_port, "volume&val=0", password)
    if should_mute:
        replace_playlist_from_file(landscape_port, password, Path(landscape_playlist), enqueue_only=hide_windows)
    if not hide_windows:
        vlc_http_cmd(landscape_port, "pl_next", password)

    _write_result_file(
        result_file,
        {
            "portrait_pid": portrait_proc.pid,
            "landscape_pid": landscape_proc.pid,
        },
    )



def _build_vlc_launch_command(vlc_exe: str, port: int, password: str, *, repeat_mode: str, mute: bool = False, playlist_path: Path | None = None) -> list[str]:
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
        # the user navigates.  When the playlist is loaded via HTTP after
        # muting, there is nothing to hear even if --volume 0 has a startup
        # race.
    # --no-random overrides VLC's saved vlcrc setting.  Without it, if the
    # user ever toggled shuffle inside VLC, the preference persists across
    # launches and VLC advances to random items instead of sequentially,
    # breaking vlc_nav_step's index-based prev/next navigation.
    command.append("--no-random")
    if repeat_mode == "repeat":
        command.append("--repeat")
    elif repeat_mode == "loop":
        command.append("--loop")
    if playlist_path is not None:
        command.append(str(playlist_path))
    return command
