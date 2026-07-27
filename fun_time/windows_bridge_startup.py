from __future__ import annotations

import configparser
import logging
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .audio_volume import MAX_VOLUME, write_volume
from .broker_control import PARK_CMD, write_broker_command
from .config import load_config
from .dashboard_runtime import is_broker_heartbeat_fresh, read_nau_status
from .modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_NAU,
    PLAYLIST_PORTRAIT,
    SatelliteLibraryContext,
    build_fmode_playlists,
    build_playlist_file_path,
    build_primary_playlist,
)
from .satellite_control import read_satellite_status
from .session_resume import playlist_fits_sources, resume_playlists
from .watch_stats import watch_stats_path
from .orchestrator_broker import (
    BROKER_LAUNCHER_PATTERN,
    BROKER_PROCESS_PATTERN,
    BROKER_TRAY_PATTERN,
    broker_launch_kwargs,
    subprocess_window_kwargs,
)
from .random_favs_browser import build_manifest, write_manifest
from .child_log import open_child_log
from .rfb_tab_page import tabs_dir, write_tab_pages
from .window_layout import WindowRect

logger = logging.getLogger(__name__)


# The two native satellites carry DISTINCT window captions so the sequencer can
# resolve each to its slot by title when the pid lookup fails (the genau venv's
# pythonw launcher can own a pid other than the window's — the same reason
# launch_nau needs a title fallback).  A shared caption lets the fallback assign
# one side's window to the other, which is the portrait/landscape visual swap.
# The sequencer imports these to resolve by, so the strings live in one place.
# These captions are also what each window calls itself in Alt-Tab and on its
# taskbar button, so they name what the window *is* rather than this project's
# internal word for it.
SATELLITE_PORTRAIT_TITLE = "Portrait AI Player"
SATELLITE_LANDSCAPE_TITLE = "Landscape AI Player"


def _write_result_file(result_file: str | Path, values: dict[str, int | str]) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {key: str(value) for key, value in values.items()}
    result_path = Path(result_file)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def stop_broker_processes() -> None:
    """Kill every broker and broker-tray process on the machine.

    Matched by command line, so there is nothing for a working directory to
    scope: the sweep reaches the same processes wherever it runs from.
    """
    ps_command = (
        "$targets = Get-CimInstance Win32_Process | Where-Object { "
        "(($_.Name -match '^pythonw?\\.exe$|^py\\.exe$') -and $_.CommandLine -match '"
        + BROKER_PROCESS_PATTERN + "|" + BROKER_TRAY_PATTERN
        + "') -or "
        "(($_.Name -match '^wscript\\.exe$') -and $_.CommandLine -match '"
        + BROKER_LAUNCHER_PATTERN
        + "') "
        "}; "
        "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command],
        check=False,
        **subprocess_window_kwargs(),
    )


def reap_orphaned_satellites(
    satellite_module: str, status_files: Sequence[str | Path],
) -> None:
    """Kill satellite players stranded on the state files this session is claiming.

    Normal shutdown kills the two satellites the orchestrator tracked, but a hard
    crash or an unclean close can strand them alive.  A stranded pair keeps reading
    the same ``state/*_cmd.txt`` / ``*_status.txt`` files this session's pair will
    use, so on reopen four players race two files — stalled video and crossed
    controls.  Reaped once at startup, before the new pair launches.

    *status_files* is what bounds the reap, and it must: every satellite on the
    machine runs ``-m <satellite_module>``, so matching the module alone made this
    a machine-wide sweep — an integration run, whose state dir is somewhere else
    entirely, killed both players in the user's live session on its way up.  Only a
    player already bound to one of *our* files can be stranded on it, and nothing
    else can be.  No files means nothing to claim and so nothing to reap.
    """
    if not status_files:
        return
    module_pattern = re.escape(satellite_module)
    # PowerShell single-quoted literals: only ' needs doubling, so a Windows path's
    # backslashes and brackets stay literal (no regex or -like wildcard surprises).
    claimed = ",".join("'" + str(path).replace("'", "''") + "'" for path in status_files)
    ps_command = (
        f"$claimed = @({claimed}); "
        "Get-CimInstance Win32_Process | Where-Object { $p = $_; "
        "($p.Name -match '^pythonw?\\.exe$|^py\\.exe$') -and $p.CommandLine -and "
        f"($p.CommandLine -match '-m\\s+{module_pattern}(\\s|$)') -and "
        "($claimed | Where-Object { $p.CommandLine.Contains($_) }) "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command],
        check=False,
        **subprocess_window_kwargs(),
    )


def launch_broker_tray(broker_tray_launcher: Path | None) -> None:
    """Start the broker's tray, or do nothing if one is already up.

    The tray and the broker each hold a single-instance mutex, so launching
    over a live pair costs one process that exits immediately.  That is what
    makes this safe to run on a liveness reading we do not fully trust: the
    worst case is a wasted launch, where killing first cannot be taken back.

    The tray goes up with the broker's own kwargs, not the ordinary
    hidden-window ones: it has to break away from an integration run's job
    object and outlive the run that started it.
    """
    if broker_tray_launcher and broker_tray_launcher.is_file():
        subprocess.Popen(
            ["wscript.exe", str(broker_tray_launcher)],
            cwd=broker_tray_launcher.parent,
            **broker_launch_kwargs(),
        )


def broker_source_mtime(broker_tray_launcher: Path | None) -> float | None:
    """When osr2_broker's own sources were last written, or None if unreadable.

    The launcher sits in the broker's repo root, so its package is the sibling
    directory.  Only that package counts: the config, logs and state files beside
    it change constantly without changing what the process runs, and the shared
    siblings it imports (``app_support``, ``shared_ui``) belong to four apps, so
    neither should be able to order a restart here.
    """
    if broker_tray_launcher is None:
        return None
    package = broker_tray_launcher.parent / "osr2_broker"
    try:
        return max((source.stat().st_mtime for source in package.rglob("*.py")), default=None)
    except OSError:
        return None


def broker_process_started_at() -> float | None:
    """When the running broker started, in Unix seconds — None if none is up.

    Matched on the command line like every other process lookup here: the broker
    runs under a bare ``pythonw``, so its image name says nothing.  The oldest is
    the one reported, because that is the one at risk of being stale.
    """
    ps_command = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "($_.Name -match '^pythonw?\\.exe$|^py\\.exe$') -and $_.CommandLine -match '"
        + BROKER_PROCESS_PATTERN
        + "' } | ForEach-Object { "
        "[int64]($_.CreationDate.ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds "
        "} | Sort-Object | Select-Object -First 1"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command],
        check=False, capture_output=True, text=True, **subprocess_window_kwargs(),
    )
    try:
        return float(result.stdout.strip())
    except (AttributeError, ValueError):
        return None


def ensure_broker(
    broker_heartbeat_file: str | Path | None,
    broker_tray_launcher: Path | None = None,
) -> None:
    """Start the broker if one is not already running, or replace a stale one.

    A healthy broker outlives the session that launched it: the user's own tools
    keep talking to it over the shared UDP inlet, and osr2_broker installs a
    self-healing scheduled task that keeps one alive.  Killing a live broker to
    relaunch our own would drop every client mid-stream.

    A stale heartbeat is not permission to kill.  osr2_broker only ticks it while
    it holds the serial port, so a powered-off OSR2 makes a healthy broker look
    gone — and a session start is exactly when the device tends to be off.  So we
    launch over it instead and let the single-instance mutexes absorb it.

    A broker older than its own sources IS permission, and is the one case that
    gets one.  That is two timestamps rather than a guess, and what it means is
    that our command vocabulary has moved past what that process can understand —
    an unrecognised verb is dropped with no log line and no error, so the feature
    that added it simply appears dead.  RETRACT shipped into exactly that gap.
    Startup is when to spend the restart: the session is coming up around it
    anyway, and either timestamp being unreadable means we cannot tell, which is
    not permission.
    """
    source_mtime = broker_source_mtime(broker_tray_launcher)
    if source_mtime is not None:
        started_at = broker_process_started_at()
        if started_at is not None and source_mtime > started_at:
            logger.info(
                "Broker started %.0fs before its own code was last written; restarting it",
                source_mtime - started_at,
            )
            stop_broker_processes()
            launch_broker_tray(broker_tray_launcher)
            return
    if broker_heartbeat_file is not None and is_broker_heartbeat_fresh(Path(broker_heartbeat_file)):
        return
    launch_broker_tray(broker_tray_launcher)


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
    genau_cmd_file: str | Path | None = None,
) -> None:
    """Seed the cross-process flags for the startup mode (nau): Genau parked, Nau
    paused until the sequencer's reveal unpauses it, and the sound level back at
    full — Nau and the audio companion each launch unattenuated, so a level left
    muted by the last session would silence this one with nothing on screen to
    explain it.

    Genau's *display* is seeded too, on its command channel.  Blanking keys off
    DISPLAY_ON/DISPLAY_OFF and Genau defaults to owning its display (so a
    standalone run paints its clips), while the DISPLAY_OFF that would blank it
    under an orchestrator only rides a mode *switch* — and a session that starts
    in nau mode never switches.  Left unsaid, Genau comes up painting its clips
    in the primary slot it shares with Nau.
    """
    for path, value in (
        (Path(genau_paused_file), "1"),
        (Path(audio_paused_file), "1"),
        (Path(nau_paused_file), "1"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    write_volume(Path(audio_volume_file), MAX_VOLUME)
    if genau_cmd_file is not None:
        path = Path(genau_cmd_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PAUSE\nDISPLAY_OFF", encoding="utf-8")


def reset_satellite_paused_states(
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
) -> None:
    """Clear both satellite paused flags so this session's players start playing.

    Unlike the genau/audio/nau flags, the satellite paused files are outside
    ``seed_startup_states``' scope and nothing else clears them.  A ``"1"`` left
    stranded by a prior session's OmniPause would make this session's satellites
    read paused and never play (frozen at position 0), so reset both to ``"0"``
    before they launch — a satellite always comes up playing.
    """
    for path in (Path(portrait_paused_file), Path(landscape_paused_file)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0", encoding="utf-8")


def start_core_session(
    *,
    config_path: str | Path,
    broker_cmd_file: str | Path,
    broker_tray_launcher: Path | None = None,
    broker_heartbeat_file: str | Path | None = None,
    random_favs_browser_manifest_file: str | Path,
    genau_paused_file: str | Path,
    genau_cmd_file: str | Path | None = None,
    audio_paused_file: str | Path,
    nau_paused_file: str | Path,
    audio_volume_file: str | Path,
    satellite_python_exe: str | Path,
    satellite_module: str,
    portrait_cmd_file: str | Path,
    portrait_paused_file: str | Path,
    portrait_status_file: str | Path,
    landscape_cmd_file: str | Path,
    landscape_paused_file: str | Path,
    landscape_status_file: str | Path,
    nau_status_file: str | Path,
    portrait_log_file: str | Path,
    landscape_log_file: str | Path,
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
    regen_media_root: Path | None = None,
    regen_metadata_root: Path | None = None,
) -> None:
    # Clear any satellites stranded by a prior crash on the very files this
    # session is about to claim, so four players never race the two command/status
    # file sets.  Bounded to those files: a session elsewhere on the machine (an
    # integration run) owns different ones and must be left alone.
    reap_orphaned_satellites(
        satellite_module, [portrait_status_file, landscape_status_file],
    )
    # Send the OSR2 home first, so it waits out startup parked rather than
    # wherever the last session left it — the two native players decode their
    # first frames while Nau and Genau scan their libraries, and that wait is
    # long.  The verb keeps in the file until the broker's next tick, so it does
    # not matter that ensure_broker may only now be starting one.
    write_broker_command(broker_cmd_file, PARK_CMD)
    ensure_broker(broker_heartbeat_file, broker_tray_launcher)
    seed_startup_states(
        genau_paused_file, audio_paused_file, nau_paused_file, audio_volume_file,
        genau_cmd_file,
    )
    # seed_startup_states does not touch the satellite paused files; clear any "1"
    # a prior OmniPause stranded so the satellites launch playing, not frozen.
    reset_satellite_paused_states(portrait_paused_file, landscape_paused_file)
    prepare_random_favs_browser_manifest(config_path, random_favs_browser_manifest_file)
    state_path = Path(state_dir)
    portrait_playlist = build_playlist_file_path(state_path, PLAYLIST_PORTRAIT)
    landscape_playlist = build_playlist_file_path(state_path, PLAYLIST_LANDSCAPE)
    # Come back to the clips this session was closed on, rather than three the
    # user never chose: last session's playlists are still on disk and each
    # player's status file names the video it had on screen, so a reopen rotates
    # them instead of reshuffling.  Only a session with nothing to resume — a
    # first run, a wiped state dir — is built, by the same builder the F-mode
    # toggle uses.  Shuffle and Premiere still rebuild on demand, and that is
    # where videos added since come in.
    nau_playlist = build_playlist_file_path(state_path, PLAYLIST_NAU)
    resumed = resume_playlists([
        (portrait_playlist, read_satellite_status(Path(portrait_status_file)).video),
        (landscape_playlist, read_satellite_status(Path(landscape_status_file)).video),
        (nau_playlist, read_nau_status(Path(nau_status_file)).video),
    ])
    if not resumed:
        build_fmode_playlists(
            primary_sources=primary_sources,
            portrait_sources=portrait_sources,
            landscape_sources=landscape_sources,
            favs_file=Path(favs_file),
            state_dir=state_path,
            enabled=False,
            library=SatelliteLibraryContext(
                metadata_root=regen_metadata_root,
                watch_stats_file=watch_stats_path(state_path),
            ),
        )
    elif not playlist_fits_sources(nau_playlist, primary_sources):
        # Resumed from FunTimeVR, whose primary rotation merges the VR library
        # into this one's: its playlist is still in the state dir both apps
        # share, and honoring it puts VR videos on the desktop primary, which
        # must never play them.  Rebuild the primary from this session's own
        # library alone; the satellites' playlists come from the same dirs in
        # either app, so their resume stands.
        build_primary_playlist(nau_playlist, primary_sources)
        logger.info("Resumed playlists; rebuilt the primary's, which held another app's videos")
    # Which of the two ran is the difference between the clips of the last
    # session and three new ones, so the log says outright which you are getting.
    logger.info(
        "Resumed last session's playlists"
        if resumed
        else "Nothing to resume; built fresh playlists"
    )
    launch_core_apps(
        python_exe=satellite_python_exe,
        satellite_module=satellite_module,
        portrait_playlist=portrait_playlist,
        landscape_playlist=landscape_playlist,
        portrait_cmd_file=portrait_cmd_file,
        portrait_paused_file=portrait_paused_file,
        portrait_status_file=portrait_status_file,
        landscape_cmd_file=landscape_cmd_file,
        landscape_paused_file=landscape_paused_file,
        landscape_status_file=landscape_status_file,
        portrait_log_file=portrait_log_file,
        landscape_log_file=landscape_log_file,
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
    console_file: str | Path | None = None,
    drive_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
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
    # In genau mode Genau draws the primary console — the same panel Nau draws in
    # the other modes — so it reads the console Fun Time publishes and posts a
    # press back on the dashboard command file, exactly as Nau does.
    if console_file is not None:
        cmd.extend(["--console-file", str(console_file)])
    # Where Genau publishes its drive readout for Nau to draw in Hybrid.  Named by
    # us so both players name the same file; Genau resolving it from its own
    # config wrote it into the Genau repo, where Nau was never looking.
    if drive_file is not None:
        cmd.extend(["--drive-file", str(drive_file)])
    if dashboard_cmd_file is not None:
        cmd.extend(["--dashboard-cmd-file", str(dashboard_cmd_file)])
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
    console_file: str | Path,
    drive_file: str | Path,
    dashboard_cmd_file: str | Path,
    log_file: str | Path,
    nau_x: int,
    nau_y: int,
    nau_width: int,
    nau_height: int,
    metadata_dir: str | Path | None = None,
) -> int:
    """Launch Nau subprocess, returning its PID.

    Its stdout and stderr go to *log_file* for the same reason a satellite's do:
    Nau is the same mpv-backed player under the same windowed ``pythonw``, which
    gives an unhandled exception nowhere to print its traceback.
    """
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
        # Nau's HUD is the console the dashboard used to be: it reads the panel we
        # publish, reads Genau's readout for the section under it, and posts a
        # press — on a button or on the volume control — back onto the same
        # command file the dashboard wrote.
        "--console-file",
        str(console_file),
        "--drive-file",
        str(drive_file),
        "--dashboard-cmd-file",
        str(dashboard_cmd_file),
        "--x",
        str(nau_x),
        "--y",
        str(nau_y),
        "--width",
        str(nau_width),
        "--height",
        str(nau_height),
        # Fun Time owns the slot's geometry, so Nau drops its title bar here — the
        # satellites and Genau do the same.  Run standalone it keeps its chrome.
        "--borderless",
    ]
    # Lets Nau group a video's versions from Evolver's metadata sidecars rather
    # than guessing from clip names.
    if metadata_dir:
        cmd += ["--metadata-dir", str(metadata_dir)]
    with open_child_log(log_file, cmd) as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, **subprocess_window_kwargs())
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
    portrait_log_file: str | Path,
    landscape_log_file: str | Path,
    portrait_rect: WindowRect,
    landscape_rect: WindowRect,
    result_file: str | Path,
    portrait_hud_file: str | Path | None = None,
    landscape_hud_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
) -> None:
    """Spawn the two native satellite players (portrait + landscape).

    Each is our own mpv-backed process (this repo's ``satellite`` package),
    driven through its command/paused/status file quartet like Nau.  Each launches
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
        log_file=portrait_log_file,
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
        log_file=landscape_log_file,
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

    The satellite is our own mpv-backed process, driven through the
    command/paused/status file quartet exactly as Nau is.  It takes no
    ``--config`` — the quartet plus geometry fully specify it — and stays silent
    with ``--no-audio``.  ``--title`` gives it the distinct caption the sequencer
    resolves its slot by.

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
    log_file: str | Path,
    x: int,
    y: int,
    width: int,
    height: int,
    hud_file: str | Path | None = None,
    dashboard_cmd_file: str | Path | None = None,
) -> int:
    """Launch a native satellite player subprocess, returning its PID.

    A sibling of :func:`launch_nau`: our own mpv-backed process, launched at the
    given rect with the given distinct *title*, driven through the
    command/paused/status file quartet, and drawing its own lock HUD from the
    published panel.

    Its stdout and stderr go to *log_file*: a satellite runs windowed under
    ``pythonw`` and would otherwise die from an unhandled exception with the
    traceback written to a handle that goes nowhere.
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
    with open_child_log(log_file, cmd) as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, **subprocess_window_kwargs())
    return proc.pid
