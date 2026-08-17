from __future__ import annotations

import configparser
import logging
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from player_core.file_channel import append_command

from .audio_volume import MAX_VOLUME, publish_audio_level
from .broker_control import PARK_CMD, write_broker_command
from .config import load_config
from .dashboard_runtime import is_broker_heartbeat_fresh, read_nau_status
from .modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_NAU,
    PLAYLIST_PORTRAIT,
    SatelliteLibraryContext,
    build_all_playlists,
    build_playlist_file_path,
    build_main_playlist,
)
from .mode_plan import STARTUP_MAIN_MODE, genau_active
from .runtime_flow import SET_F_MODE_CMD, apply_mode_switch, write_flag_file
from .satellite_control import read_satellite_status
from .session_resume import (
    playlist_fits_sources,
    playlist_opens_on,
    resume_main_loop,
    resume_playlists,
    resume_satellite_locks,
    resume_shared_state,
)
from .shared_state import shared_state_path
from .watch_stats import watch_stats_path
from .orchestrator_broker import (
    BROKER_IMAGE_PATTERN,
    BROKER_LAUNCHER_PATTERN,
    BROKER_PROCESS_PATTERN,
    BROKER_TRAY_PATTERN,
    broker_launch_kwargs,
    subprocess_window_kwargs,
)
from .process_identity import PROCESS_NAME_PATTERN, identified_python_exe
from .random_favs_browser import build_manifest, write_manifest
from .child_log import open_child_log
from .rfb_tab_page import tabs_dir, write_tab_pages
from .win32 import APP_USER_MODEL_ID
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

# Every player this launches is one of Fun Time's windows, not an application of
# its own — the user opened one program and it opened these — so each is told to
# take Fun Time's taskbar identity rather than claim one.  Windows groups
# buttons by AppUserModelID and takes the icon and name from the pinned shortcut
# carrying the same one (``orchestrator.stamp_shortcut_aumid`` puts it there), so
# without this the bar showed four applications: Nau and Genau under their own
# marks, and the satellites — which claimed nothing — under whichever unrelated
# app had registered the shared python interpreter's path.
#
# Passed rather than shared as a constant: the players are separate apps in
# another repo and must not know Fun Time's name.  Each still runs as itself when
# launched from its own shortcut, since then there is nobody to tell it otherwise.
TASKBAR_IDENTITY_ARGS = ("--taskbar-identity", APP_USER_MODEL_ID)


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
        "(($_.Name -match '" + BROKER_IMAGE_PATTERN + "') -and $_.CommandLine -match '"
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
        f"($p.Name -match '{PROCESS_NAME_PATTERN}') -and $p.CommandLine -and "
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
    an unrecognized verb is dropped with no log line and no error, so the feature
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
    """Pick this session's favorites and record the tabs Chrome should open.

    Lazy loading puts a local landing page in front of each favorite, so ten
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
    genau_cmd_file: str | Path,
    *,
    nau_cmd_file: str | Path,
    volume: int = MAX_VOLUME,
    muted: bool = False,
    f_mode: bool = False,
    mode: str = STARTUP_MAIN_MODE,
) -> None:
    """Seed the cross-process flags the main slot opens on: both its players
    held until the sequencer's reveal starts whichever the mode puts on screen,
    and the sound, F-mode and mode this session comes back in.

    All three of those last are the session's, not a fresh session's.  The level
    is seeded because Nau and the audio companion each launch unattenuated and
    neither reads a level from a file it already has, so seeding is the only way
    a resumed session comes up as loud as you left it — and both sinks are told,
    through the one publisher the live volume commands use, which is what keeps a
    resumed mute explicable rather than a silence with nothing on screen behind
    it (Nau draws the level and the mute it is given).

    *f_mode* is the main player's own — this whole function is the main slot's
    seeding — and it is seeded for the same shape of reason: the playlist Nau is
    handed has already been narrowed and a list of scripted videos looks like any
    other, so Nau's HUD can only know from being told.  fun_time draws the
    satellites' HUD model itself, which is why a resumed F-mode session showed
    F-Mode on every player except the one that had to be sent it.

    *mode* is which player owns the big display, and it is seeded by REPLAYING
    the switch that would have reached it: every session is built in
    ``STARTUP_MAIN_MODE``, so coming back in genau or hybrid is a switch out
    of nau, and running it through the same planner a live switch uses is what
    stops the two from ever describing the mode differently.  Only the switch's
    *verbs* are kept — the windows are parked to match by the sequencer, and its
    pause flags are overwritten with a hold, since a live switch starts its
    player immediately and startup must not.

    Genau's *display* is the one thing that has to be said even in nau mode.
    Blanking keys off DISPLAY_ON/DISPLAY_OFF and Genau defaults to owning its
    display (so a standalone run paints its clips), while the DISPLAY_OFF that
    blanks it under an orchestrator only rides a switch — and a session opening
    in nau mode has no switch to ride.  Left unsaid, Genau comes up painting its
    clips in the main slot it shares with Nau.

    The defaults are a fresh session's: full, unmuted, unnarrowed, on Nau.
    """
    Path(genau_cmd_file).parent.mkdir(parents=True, exist_ok=True)
    # Written whole ONCE, here, before any player is running: the fresh
    # session's reset, clearing whatever a crashed predecessor left queued.
    # Everything after it appends — the player drains the queue in order, so a
    # later verb of the same kind supersedes an earlier one and none is lost.
    # The broker is left out on purpose — startup has already parked the OSR2, and
    # a switch INTO a genau-active mode has nothing to say to it anyway.
    Path(genau_cmd_file).write_text("PAUSE\nDISPLAY_OFF\n", encoding="utf-8")
    apply_mode_switch(
        current_mode=STARTUP_MAIN_MODE,
        target_mode=mode,
        omni_paused=False,
        genau_paused_file=genau_paused_file,
        audio_paused_file=audio_paused_file,
        genau_cmd_file=genau_cmd_file,
        nau_paused_file=nau_paused_file,
        nau_cmd_file=nau_cmd_file,
    )
    # After the switch, whose pause flags are a live one's: it would have started
    # Genau the moment it landed, and here that is twenty seconds of the OSR2
    # moving behind a progress bar.  Every player waits for the reveal instead.
    for path in (genau_paused_file, audio_paused_file, nau_paused_file):
        write_flag_file(path, True)
    # The flag does not hold Genau, which is how that twenty seconds went on
    # happening anyway.  Under Fun Time Genau runs in direct control, where its
    # stroke follows the PAUSE/RESUME verbs on THIS channel and the paused flag
    # above is never read at all — so the RESUME the switch just queued was still
    # waiting when Genau finished loading, and every session resuming into genau
    # or hybrid drove the OSR2 behind the loading screen.  Queued behind the
    # switch's verbs rather than replacing them: the channel is drained in order,
    # so the display and HUD it also asserted still land and only the play verb is
    # taken back.  The reveal is what hands Genau its RESUME.
    if genau_active(mode):
        append_command(Path(genau_cmd_file), "PAUSE")
    publish_audio_level(
        nau_cmd_file=Path(nau_cmd_file),
        genau_cmd_file=Path(genau_cmd_file),
        audio_volume_file=Path(audio_volume_file),
        volume=volume,
        muted=muted,
    )
    append_command(Path(nau_cmd_file), f"{SET_F_MODE_CMD} {int(f_mode)}")


def reset_satellite_paused_states(
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
    *,
    satellites_mode: str = "player",
) -> None:
    """Seed both satellite paused flags for the mode the session opens in.

    Unlike the genau/audio/nau flags, the satellite paused files are outside
    ``seed_startup_states``' scope and nothing else clears them.  A ``"1"`` left
    stranded by a prior session's OmniPause would make this session's satellites
    read paused and never play (frozen at position 0), so both are written
    before they launch.  In player mode that write is ``"0"`` — a satellite
    comes up playing.  A session RESUMED into origenerator mode comes up with
    them ``"1"`` instead: the regions are the hosted app's for the whole mode,
    and the players are black and paused underneath exactly as the mode switch
    would have left them.
    """
    paused = "1" if satellites_mode == "origenerator" else "0"
    for path in (Path(portrait_paused_file), Path(landscape_paused_file)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(paused, encoding="utf-8")


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
    nau_cmd_file: str | Path,
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
    main_sources: str,
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
    project_dirs: str | None = None,
) -> str:
    """Launch the session's media stack, returning the mode its main slot
    opens in — which the caller needs because parking the Nau/Genau pair to match
    takes window handles only the sequencer has."""
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
    nau_status = read_nau_status(Path(nau_status_file))
    resumed = resume_playlists([
        (portrait_playlist, read_satellite_status(Path(portrait_status_file)).video),
        (landscape_playlist, read_satellite_status(Path(landscape_status_file)).video),
        (nau_playlist, nau_status.video),
    ])
    # Come back to the state that session was in, too — F-mode, each side's
    # filter, order and lock, any group loop, the sound level, which player had
    # the big display.  The dispatch loop opens on this file, so a session that
    # resumed the files and not the state described itself wrongly on every HUD.
    # It is read before the flags below are seeded, because several of them are
    # what those flags have to be seeded to.
    carried = resume_shared_state(shared_state_path(state_path), resumed=resumed)
    seed_startup_states(
        genau_paused_file, audio_paused_file, nau_paused_file, audio_volume_file,
        genau_cmd_file, nau_cmd_file=nau_cmd_file,
        volume=carried.volume, muted=carried.muted, f_mode=carried.main_f_mode,
        mode=carried.main_mode,
    )
    # seed_startup_states does not touch the satellite paused files; seed them
    # for the mode this session opens in — playing in player mode (clearing any
    # "1" a prior OmniPause stranded), paused when resumed into origenerator
    # mode, whose players are black and held for the whole mode.
    reset_satellite_paused_states(portrait_paused_file, landscape_paused_file,
                                  satellites_mode=carried.satellites_mode)
    prepare_random_favs_browser_manifest(config_path, random_favs_browser_manifest_file)
    if not resumed:
        build_all_playlists(
            main_sources=main_sources,
            portrait_sources=portrait_sources,
            landscape_sources=landscape_sources,
            favs_file=Path(favs_file),
            state_dir=state_path,
            library=SatelliteLibraryContext(
                metadata_root=regen_metadata_root,
                watch_stats_file=watch_stats_path(state_path),
            ),
        )
    elif not playlist_fits_sources(nau_playlist, main_sources):
        # Resumed from FunTimeVR, whose main rotation merges the VR library
        # into this one's: its playlist is still in the state dir both apps
        # share, and honoring it puts VR videos on the desktop's primary monitor, which
        # must never play them.  Rebuild the main player from this session's own
        # library alone; the satellites' playlists come from the same dirs in
        # either app, so their resume stands.  Under the order it is coming back
        # in, like its F-mode: the state carried forward has to describe the file
        # this writes, not the one it replaced.
        build_main_playlist(nau_playlist, main_sources, f_mode=carried.main_f_mode,
                            recent=carried.main_latest)
        logger.info("Resumed playlists; rebuilt the main player's, which held another app's videos")
    # Which of the two ran is the difference between the clips of the last
    # session and three new ones, so the log says outright which you are getting.
    logger.info(
        "Resumed last session's playlists"
        if resumed
        else "Nothing to resume; built fresh playlists"
    )
    # A lock has no file of its own to come back in, so queue it for each side
    # that was holding one — from here it is waiting when the satellite starts.
    resume_satellite_locks([
        (Path(portrait_cmd_file), carried.locked2),
        (Path(landscape_cmd_file), carried.locked3),
    ])
    # The main player's loop is the same kind of thing, and queued the same way —
    # but only if the main player really did come back onto the video the loop was
    # cut from.  A rebuild above, or a clip deleted since, leaves some other
    # video leading, and those bounds would then mark out a stretch of a video
    # nobody chose.
    resume_main_loop(
        Path(nau_cmd_file),
        nau_status.loop_bounds if playlist_opens_on(nau_playlist, nau_status.video) else None,
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
        project_dirs=project_dirs,
    )
    return carried.main_mode


def genau_project_kwargs(project_dirs: str | Path | None) -> dict:
    """The ``Popen`` environment that decides which checkouts Genau and Nau run.

    Both are started as ``python -m genau`` / ``-m nau`` out of the genau venv,
    and every package they import — their own, and ``player_core`` under them —
    resolves through that venv's editable installs, which name the primary
    checkout of each repo for good.  So a *worktree* of either could not be run
    at all, and a branch of one could only be judged by landing it first.  Named
    here, those directories go on ``PYTHONPATH``, which Python puts ahead of
    site-packages, and a session runs the branch.

    Several, because a change is often in two of them at once — a HUD in
    ``../genau`` on a channel in ``../player_core`` — and running one branch
    against the other's landed code is not running the change.

    Left alone rather than pointed at the primary in ordinary use: empty means
    exactly what every session did before this.  A directory that is not there is
    dropped rather than fatal, because a worktree named in the config outlives
    the worktree and a session must still start.
    """
    paths = [str(Path(part)) for part in str(project_dirs or "").split(os.pathsep)
             if part and Path(part).is_dir()]
    if not paths:
        return {}
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    return {"env": {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}}


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
    start_clip: str = "",
    project_dirs: str | None = None,
) -> int:
    """Launch Genau subprocess, returning its PID.

    *start_clip* is the clip the last session was left showing, or "" for a
    session with none to come back to.  *project_dir* is which checkout of the
    genau repo to run — see :func:`genau_project_kwargs`.
    """
    cmd = [
        identified_python_exe(python_exe, "Genau"),
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
    cmd.extend(TASKBAR_IDENTITY_ARGS)
    if command_file is not None:
        cmd.extend(["--command-file", str(command_file)])
    if paused_file is not None:
        cmd.extend(["--paused-file", str(paused_file)])
    # In genau mode Genau draws the main console — the same panel Nau draws in
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
    # Genau rescans its clips folder every launch and opens at the top of it, so
    # the clip a session was left showing comes back only by being named here.  On
    # the command line rather than the command channel because that channel
    # upper-cases every line (a path cannot survive it) and because a verb would
    # arrive after Genau had already decoded the wrong clip.
    if start_clip:
        cmd.extend(["--start-clip", start_clip])
    proc = subprocess.Popen(
        cmd, **genau_project_kwargs(project_dirs), **subprocess_window_kwargs())
    return proc.pid


def launch_origenerator(
    *,
    python_exe: str | Path,
    origenerator_dir: str | Path,
    layout_plan,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    dashboard_cmd_file: str | Path,
    project_dirs: str | None = None,
) -> int:
    """Launch the hosted Origenerator, returning its PID.

    Its ``--fun-time`` contract (``origenerator.fun_time_mode``): the main
    window takes the RFB's rect, the shows take the two satellite region rects,
    and the file trio is how the session drives and observes it.  Run with
    ``cwd`` in the checkout so ``-m`` resolves that checkout's code — the same
    way its own launcher picks a checkout, and what lets a worktree of it be
    hosted for a branch verification.  It boots parked (its own doing), so
    nothing waits on it: the dispatch loop adopts the window when it appears.
    """
    rfb = layout_plan.random_favs_browser
    cmd = [
        identified_python_exe(python_exe, "Origenerator"),
        "-m", "origenerator", "--fun-time",
        "--x", str(rfb.x), "--y", str(rfb.y),
        "--width", str(rfb.width), "--height", str(rfb.height),
    ]
    for prefix, rect in (("portrait", layout_plan.portrait),
                         ("landscape", layout_plan.landscape)):
        cmd.extend([
            f"--{prefix}_x", str(rect.x), f"--{prefix}_y", str(rect.y),
            f"--{prefix}_width", str(rect.width),
            f"--{prefix}_height", str(rect.height),
        ])
    cmd.extend(TASKBAR_IDENTITY_ARGS)
    cmd.extend([
        "--command-file", str(command_file),
        "--paused-file", str(paused_file),
        "--status-file", str(status_file),
        "--dashboard-cmd-file", str(dashboard_cmd_file),
    ])
    # The same checkout choice Genau, Nau and the satellites get: named
    # project dirs ride the hosted app's PYTHONPATH, so a branch of
    # player_core is the one its ensure_player_core_on_path finds (it defers
    # to an already-importable copy rather than walking up to the primary).
    kwargs: dict = {"cwd": str(origenerator_dir),
                    **genau_project_kwargs(project_dirs),
                    **subprocess_window_kwargs()}
    # A worktree checkout is unlanded code under judgment, not the live
    # install: run it as origenerator's own branch session (its preview
    # launcher sets the same flag), which seeds its database from the
    # primary's, skips the maintenance passes only the live app should run,
    # and leaves its generations for the live app to adopt.  A worktree sits
    # at exactly <repo>/.claude/worktrees/<name> by this suite's own working
    # law — the same layout origenerator's launch_preview_branch.vbs walks.
    checkout = Path(origenerator_dir)
    if checkout.parent.name == "worktrees" and checkout.parent.parent.name == ".claude":
        env = kwargs.get("env") or {**os.environ}
        kwargs["env"] = {**env, "ORIGENERATOR_BRANCH_SESSION": "1"}
    proc = subprocess.Popen(cmd, **kwargs)
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
    project_dirs: str | None = None,
) -> int:
    """Launch Nau subprocess, returning its PID.

    *project_dir* is which checkout of the genau repo to run — Nau ships there
    too, so it follows Genau onto a branch rather than staying on the primary
    while its housemate moves (see :func:`genau_project_kwargs`).

    Its stdout and stderr go to *log_file* for the same reason a satellite's do:
    Nau is the same mpv-backed player under the same windowed ``pythonw``, which
    gives an unhandled exception nowhere to print its traceback.
    """
    cmd = [
        identified_python_exe(python_exe, "Nau"),
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
        # And this window is one of ours, not an application of its own: see
        # TASKBAR_IDENTITY_ARGS.
        *TASKBAR_IDENTITY_ARGS,
    ]
    # Lets Nau group a video's versions from Evolver's metadata sidecars rather
    # than guessing from clip names.
    if metadata_dir:
        cmd += ["--metadata-dir", str(metadata_dir)]
    with open_child_log(log_file, cmd) as log:
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=log,
            **genau_project_kwargs(project_dirs), **subprocess_window_kwargs())
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
                identified_python_exe(python_exe, "Dashboard"),
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
            identified_python_exe(python_exe, "AudioCompanion"),
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
    project_dirs: str | None = None,
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
        role="Portrait",
        playlist_file=portrait_playlist,
        command_file=portrait_cmd_file,
        paused_file=portrait_paused_file,
        status_file=portrait_status_file,
        log_file=portrait_log_file,
        x=portrait_rect.x, y=portrait_rect.y,
        width=portrait_rect.width, height=portrait_rect.height,
        hud_file=portrait_hud_file, dashboard_cmd_file=dashboard_cmd_file,
        project_dirs=project_dirs,
    )
    landscape_pid = launch_satellite(
        python_exe=python_exe,
        satellite_module=satellite_module,
        title=SATELLITE_LANDSCAPE_TITLE,
        role="Landscape",
        playlist_file=landscape_playlist,
        command_file=landscape_cmd_file,
        paused_file=landscape_paused_file,
        status_file=landscape_status_file,
        log_file=landscape_log_file,
        x=landscape_rect.x, y=landscape_rect.y,
        width=landscape_rect.width, height=landscape_rect.height,
        hud_file=landscape_hud_file, dashboard_cmd_file=dashboard_cmd_file,
        project_dirs=project_dirs,
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
        # One of Fun Time's windows rather than an application of its own — see
        # TASKBAR_IDENTITY_ARGS.
        *TASKBAR_IDENTITY_ARGS,
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
    role: str,
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
    project_dirs: str | None = None,
) -> int:
    """Launch a native satellite player subprocess, returning its PID.

    A sibling of :func:`launch_nau`: our own mpv-backed process, launched at the
    given rect with the given distinct *title*, driven through the
    command/paused/status file quartet, and drawing its own lock HUD from the
    published panel.

    *role* names this player in the task list, where the two are otherwise the
    same anonymous interpreter as each other and as everything else -- it is the
    *title*'s counterpart for the process the window belongs to.

    Its stdout and stderr go to *log_file*: a satellite runs windowed under
    ``pythonw`` and would otherwise die from an unhandled exception with the
    traceback written to a handle that goes nowhere.
    """
    cmd = _build_satellite_launch_command(
        identified_python_exe(python_exe, role),
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
        proc = subprocess.Popen(cmd, stdout=log, stderr=log,
                                **genau_project_kwargs(project_dirs),
                                **subprocess_window_kwargs())
    return proc.pid
