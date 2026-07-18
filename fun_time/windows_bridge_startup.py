from __future__ import annotations

import configparser
import re
import subprocess
import time
from pathlib import Path

from .audio_volume import MAX_VOLUME, write_volume
from .config import load_config
from .dashboard_runtime import is_broker_heartbeat_fresh
from .modes import SatelliteLibraryContext, build_fmode_playlists
from .watch_stats import watch_stats_path
from .orchestrator_broker import (
    BROKER_PROCESS_PATTERN,
    BROKER_TRAY_PATTERN,
    broker_launch_kwargs,
    subprocess_window_kwargs,
)
from .random_favs_browser import build_manifest, write_manifest
from .rfb_tab_page import tabs_dir, write_tab_pages
from .window_layout import WindowRect


# The two native satellites carry DISTINCT window captions so the sequencer can
# resolve each to its slot by title when the pid lookup fails (the genau venv's
# pythonw launcher can own a pid other than the window's — the same reason
# launch_nau needs a title fallback).  A shared caption lets the fallback assign
# one side's window to the other, which is the portrait/landscape visual swap.
# The sequencer imports these to resolve by, so the strings live in one place.
SATELLITE_PORTRAIT_TITLE = "Satellite Portrait"
SATELLITE_LANDSCAPE_TITLE = "Satellite Landscape"


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


def reap_orphaned_satellites(satellite_module: str) -> None:
    """Kill any native satellite players left over from a prior session.

    Normal shutdown kills the two satellites the orchestrator tracked, but a hard
    crash or an unclean close can strand them alive.  A stranded pair keeps reading
    the same ``state/*_cmd.txt`` / ``*_status.txt`` files this session's pair will
    use, so on reopen four players race two files — stalled video and crossed
    controls.  Reaped once at startup, before the new pair launches.

    Scoped to ``python -m <satellite_module>`` command lines, so it can never reach
    Nau (``-m nau``), the orchestrator, or anything else — only satellites die.
    """
    module_pattern = re.escape(satellite_module)
    ps_command = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "($_.Name -match '^pythonw?\\.exe$|^py\\.exe$') -and "
        f"$_.CommandLine -match '-m\\s+{module_pattern}(\\s|$)' "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command],
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
            **broker_launch_kwargs(),
        )


def ensure_broker(
    project_dir: str | Path,
    broker_heartbeat_file: str | Path | None,
    broker_tray_launcher: Path | None = None,
) -> None:
    """Start the broker only if one is not already running.

    A healthy broker outlives the session that launched it: harem and the user's
    direct VLC+MFP use keep talking to it over the shared UDP inlet, and
    osr2_broker installs a self-healing scheduled task that keeps one alive.
    Killing a live broker to relaunch our own would drop every client
    mid-stream, so a session (re)starts it only when the heartbeat is stale —
    the same liveness signal the dashboard and dispatch loop already read.
    """
    if broker_heartbeat_file is not None and is_broker_heartbeat_fresh(Path(broker_heartbeat_file)):
        return
    restart_broker(project_dir, broker_tray_launcher)


def prepare_random_favs_browser_manifest(config_path: str | Path, output_path: str | Path) -> None:
    """Pick this session's favourites and record the tabs Chrome should open.

    Lazy loading puts a local landing page in front of each favourite, so ten
    heavy generate pages do not all load at startup.
    """
    config = load_config(config_path)
    profile_directory, targets = build_manifest(config)
    urls = (
        write_tab_pages(tabs_dir(config.paths.state_dir), targets)
        if config.random_favs_browser.lazy_load
        else [target.url for target in targets]
    )
    write_manifest(Path(output_path), profile_directory, urls)


def seed_startup_states(
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    nau_paused_file: str | Path,
    audio_volume_file: str | Path,
) -> None:
    """Seed the cross-process flags for the startup mode (nau): Genau parked, Nau
    paused until the sequencer's reveal unpauses it, and the sound level back at
    full — Nau and the audio companion each launch unattenuated, so a level left
    muted by the last session would silence this one with nothing on screen to
    explain it."""
    for path, value in (
        (Path(genau_paused_file), "1"),
        (Path(audio_paused_file), "1"),
        (Path(nau_paused_file), "1"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    write_volume(Path(audio_volume_file), MAX_VOLUME)


def reset_satellite_paused_states(
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
) -> None:
    """Clear both satellite paused flags so this session's players start playing.

    Unlike the genau/audio/nau flags, the satellite paused files are outside
    ``seed_startup_states``' scope and nothing else clears them.  A ``"1"`` left
    stranded by a prior session's OmniPause would make this session's satellites
    read paused and never play (frozen at position 0), so reset both to ``"0"``
    before they launch — satellites always come up playing, as the VLCs did.
    """
    for path in (Path(portrait_paused_file), Path(landscape_paused_file)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0", encoding="utf-8")


def start_core_session(
    *,
    project_dir: str | Path,
    config_path: str | Path,
    broker_tray_launcher: Path | None = None,
    broker_heartbeat_file: str | Path | None = None,
    random_favs_browser_manifest_file: str | Path,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    nau_paused_file: str | Path,
    audio_volume_file: str | Path,
    genau_python_exe: str | Path,
    satellite_module: str,
    portrait_cmd_file: str | Path,
    portrait_paused_file: str | Path,
    portrait_status_file: str | Path,
    landscape_cmd_file: str | Path,
    landscape_paused_file: str | Path,
    landscape_status_file: str | Path,
    portrait_rect: WindowRect,
    landscape_rect: WindowRect,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    result_file: str | Path,
    portrait_hud_file: str | Path | None = None,
    landscape_hud_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
) -> None:
    # Clear any satellites stranded by a prior crash before this session's pair
    # launches, so four players never race the two command/status file sets.
    reap_orphaned_satellites(satellite_module)
    ensure_broker(project_dir, broker_heartbeat_file, broker_tray_launcher)
    seed_startup_states(genau_paused_file, audio_paused_file, nau_paused_file, audio_volume_file)
    # seed_startup_states does not touch the satellite paused files; clear any "1"
    # a prior OmniPause stranded so the satellites launch playing, not frozen.
    reset_satellite_paused_states(portrait_paused_file, landscape_paused_file)
    prepare_random_favs_browser_manifest(config_path, random_favs_browser_manifest_file)
    # One playlist authority: the same builder the F-mode toggle uses writes the
    # two satellite playlists and Nau's video/funscript pair list.
    playlist_plan = build_fmode_playlists(
        primary_sources=primary_sources,
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        enabled=False,
        library=SatelliteLibraryContext(
            metadata_root=provider_metadata_root,
            watch_stats_file=watch_stats_path(state_dir),
        ),
    )
    launch_core_apps(
        python_exe=genau_python_exe,
        satellite_module=satellite_module,
        portrait_playlist=playlist_plan.portrait_playlist_path,
        landscape_playlist=playlist_plan.landscape_playlist_path,
        portrait_cmd_file=portrait_cmd_file,
        portrait_paused_file=portrait_paused_file,
        portrait_status_file=portrait_status_file,
        landscape_cmd_file=landscape_cmd_file,
        landscape_paused_file=landscape_paused_file,
        landscape_status_file=landscape_status_file,
        portrait_rect=portrait_rect,
        landscape_rect=landscape_rect,
        result_file=result_file,
        portrait_hud_file=portrait_hud_file,
        landscape_hud_file=landscape_hud_file,
        dashboard_cmd_file=dashboard_cmd_file,
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
    metadata_dir: str | Path | None = None,
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
    # Lets Nau group a video's versions from Evolver's metadata sidecars rather
    # than guessing from clip names.
    if metadata_dir:
        cmd += ["--metadata-dir", str(metadata_dir)]
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


def launch_core_apps(
    *,
    python_exe: str | Path,
    satellite_module: str,
    portrait_playlist: str | Path,
    landscape_playlist: str | Path,
    portrait_cmd_file: str | Path,
    portrait_paused_file: str | Path,
    portrait_status_file: str | Path,
    landscape_cmd_file: str | Path,
    landscape_paused_file: str | Path,
    landscape_status_file: str | Path,
    portrait_rect: WindowRect,
    landscape_rect: WindowRect,
    result_file: str | Path,
    portrait_hud_file: str | Path | None = None,
    landscape_hud_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
) -> None:
    """Spawn the two native satellite players (portrait + landscape).

    Each is our own mpv-backed process (genau's ``satellite`` package), driven
    through its command/paused/status file quartet like Nau.  Each launches
    straight into its final portrait/landscape rect: mpv sizes its output to the
    launch geometry and does NOT rescale when a later Win32 move resizes the
    window, so launching at the real rect (exactly as Nau does) is what makes the
    video fill it.  There is no HTTP interface to wait on and nothing to enqueue or
    repeat-mode here — the native player owns its playlist and auto-advances (its
    wrap is repeat-all).
    """
    portrait_pid = launch_satellite(
        python_exe=python_exe,
        satellite_module=satellite_module,
        title=SATELLITE_PORTRAIT_TITLE,
        playlist_file=portrait_playlist,
        command_file=portrait_cmd_file,
        paused_file=portrait_paused_file,
        status_file=portrait_status_file,
        x=portrait_rect.x, y=portrait_rect.y,
        width=portrait_rect.width, height=portrait_rect.height,
        hud_file=portrait_hud_file, dashboard_cmd_file=dashboard_cmd_file,
    )
    landscape_pid = launch_satellite(
        python_exe=python_exe,
        satellite_module=satellite_module,
        title=SATELLITE_LANDSCAPE_TITLE,
        playlist_file=landscape_playlist,
        command_file=landscape_cmd_file,
        paused_file=landscape_paused_file,
        status_file=landscape_status_file,
        x=landscape_rect.x, y=landscape_rect.y,
        width=landscape_rect.width, height=landscape_rect.height,
        hud_file=landscape_hud_file, dashboard_cmd_file=dashboard_cmd_file,
    )
    _write_result_file(
        result_file,
        {
            "portrait_pid": portrait_pid,
            "landscape_pid": landscape_pid,
        },
    )


def _build_satellite_launch_command(
    python_exe: str | Path,
    satellite_module: str,
    *,
    title: str,
    playlist_file: str | Path,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    x: int,
    y: int,
    width: int,
    height: int,
    hud_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
) -> list[str]:
    """The argv for a native satellite player (``python -m satellite ...``).

    The satellite is our own mpv-backed process, so — unlike the VLC satellites it
    replaces — it is driven through the command/paused/status file quartet (like
    Nau) rather than a VLC HTTP port.  It takes no ``--config`` — the quartet plus
    geometry fully specify it — and stays silent with ``--no-audio``.  ``--title``
    gives it the distinct caption the sequencer resolves its slot by.

    The lock HUD rides along as two more files: the panel this loop publishes for
    the player to composite into its own video, and the command file a click on
    that HUD posts back to.  Both absent means the satellite simply draws no map.
    """
    command = [
        str(python_exe),
        "-m",
        str(satellite_module),
        "--title",
        str(title),
        "--playlist",
        str(playlist_file),
        "--command-file",
        str(command_file),
        "--paused-file",
        str(paused_file),
        "--status-file",
        str(status_file),
        "--x",
        str(x),
        "--y",
        str(y),
        "--width",
        str(width),
        "--height",
        str(height),
        "--no-audio",
    ]
    if hud_file:
        command += ["--hud-file", str(hud_file)]
    if dashboard_cmd_file:
        command += ["--dashboard-cmd-file", str(dashboard_cmd_file)]
    return command


def launch_satellite(
    *,
    python_exe: str | Path,
    satellite_module: str,
    title: str,
    playlist_file: str | Path,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    x: int,
    y: int,
    width: int,
    height: int,
    hud_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
) -> int:
    """Launch a native satellite player subprocess, returning its PID.

    The native counterpart to the VLC satellite spawn (and a sibling of
    :func:`launch_nau`): our own mpv-backed process, launched at the given rect
    with the given distinct *title*, driven through the command/paused/status
    file quartet, and drawing its own lock HUD from the published panel.
    """
    cmd = _build_satellite_launch_command(
        python_exe,
        satellite_module,
        title=title,
        playlist_file=playlist_file,
        command_file=command_file,
        paused_file=paused_file,
        status_file=status_file,
        x=x,
        y=y,
        width=width,
        height=height,
        hud_file=hud_file,
        dashboard_cmd_file=dashboard_cmd_file,
    )
    proc = subprocess.Popen(cmd, **subprocess_window_kwargs())
    return proc.pid
