"""A session's startup phases, in the order they have to happen in.

The children, then the windows, then the browser, then the companions, then the
reveal — each a function of its own, called from :func:`_run_startup_phases`,
which is the one place that says what comes after what.  The launching itself
belongs to :mod:`fun_time.windows_bridge_startup`; this module owns the order.
"""
from __future__ import annotations

import configparser
import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from app_support import state_files
from player_core.file_channel import append_command
from player_core.modes import MainMode

from main_player.play_points import play_points_filename
from satellite.contract import SatelliteChannels

from .hosted_origenerator import HostedApp, bring_up_the_hosted_app
from .manifest import LaunchManifest, RandomFavsBrowserSettings
from .mode_plan import MAIN_GENAU_MODE, STARTUP_MAIN_MODE, main_player_displays
from .modes import PLAYLIST_LANDSCAPE, PLAYLIST_PORTRAIT, build_playlist_file_path
from .overlay_progress import NullProgress, ProgressReporter, StartupCancelled
from .player_status import (
    read_genau_status,
    read_main_player_status,
)
from .players import Player
from .runtime_flow import write_flag_file
from .satellite_control import read_satellite_status
from .satellite_slot import SatelliteSlot
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .shortcuts import resolve_shortcut
from .win32 import (
    ANSWER_TIMEOUT_MS,
    disable_window_transitions,
    find_windows_by_class,
    minimize_window,
    move_window,
    set_always_on_top,
    wait_for_window_by_title,
    window_answers,
)
from .window_layout import (
    ScreenLayout,
    WindowLayoutPlan,
    WindowRect,
    compute_main_media_rect,
    screen_layout,
)
from .window_roles import GENAU_TITLE, MANAGED_ROLES, role_topmost
from .windows_bridge_random_favs_browser import (
    CHROME_WINDOW_CLASS,
    launch_random_favs_browser,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
    launch_genau,
    launch_main_player,
    launch_ui_companions,
    start_core_session,
)

logger = logging.getLogger(__name__)

# How long startup waits for a launched app to put its window on screen.  A poll
# returns the moment the window appears — a satellite's takes about half a second
# — so this is a ceiling for a machine under load, not a cost anyone pays.
WINDOW_RESOLVE_TIMEOUT_S = 15.0

GENAU_ANSWER_TIMEOUT_S = 12.0  # player_core's FirstClipPreload caps Genau's first decode at 10s

# How long startup waits for the main player to finish loading.  Wide enough for the worst
# case, a cold duration cache: one ffprobe per unprobed video, measured at 28s
# for 525 of them, and paid once because the cache persists.  Its ceiling is the
# overlay's own patience: the two waits in this phase run back to back under a
# single progress write, and the overlay tears itself down when that file has
# gone ``loading_screen.STALE_TIMEOUT_S`` without changing.  A test pins the sum.
MAIN_PLAYER_LOAD_TIMEOUT_S = 40.0


@dataclass(frozen=True)
class StartupResult:
    main_player_pid: int
    portrait_pid: int
    landscape_pid: int
    dashboard_pid: int
    genau_pid: int
    audio_pid: int
    # The hosted Origenerator's process, or 0 for a session with none configured.
    origenerator_pid: int = 0
    origenerator_taken_over: bool = False
    origenerator_already_open: bool = False
    # Which player the main slot was revealed on — last session's, resumed.
    # Carried out because the post-overlay z-order pass runs from the
    # orchestrator and has to re-assert the same policy these phases applied.
    # The satellite side has no such line: every room is BUILT in video mode.
    main_mode: MainMode = STARTUP_MAIN_MODE
    rfb_hwnd: int = 0
    # HWNDs resolved while every window was still visible; the dispatch
    # loop's role cache is seeded from this (hidden windows cannot be
    # re-resolved by pid/title lookups).
    role_hwnds: dict[str, int] = field(default_factory=dict)


def _read_result_pids(result_file: str | Path) -> dict[str, int]:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(result_file), encoding="utf-8")
    return {key: int(value) for key, value in parser["result"].items()}


def _build_unique_result_path(state_dir: Path, prefix: str) -> Path:
    return state_dir / f"{prefix}_{int(time.monotonic() * 1000)}.ini"


def _startup_role_hwnds(
    *,
    portrait_hwnd: int,
    landscape_hwnd: int,
    genau_hwnd: int,
    main_player_hwnd: int,
    dashboard_hwnd: int = 0,
    rfb_hwnd: int = 0,
) -> dict[str, int]:
    """The managed windows by role, as resolved at startup.

    The hosted app's three are not among them: it is still booting when the room
    opens, so none of them exists yet, and ``WindowRoles.hwnd`` resolves each
    fresh the first time the dispatch loop asks for one.
    """
    return {
        "portrait": portrait_hwnd,
        "landscape": landscape_hwnd,
        "genau": genau_hwnd,
        "main_player": main_player_hwnd,
        "dashboard": dashboard_hwnd,
        "rfb": rfb_hwnd,
    }


def apply_topmost_bands(role_hwnds: dict[str, int], mode: str, *, beneath: int = 0) -> None:
    """Give each managed window its topmost flag from the shared ``role_topmost``
    policy for *mode* — the same policy omnipause and mode switches honor, so
    they can never disagree.

    Only the main slot's mode is asked for: the satellite side always opens in
    video mode, which is the policy's own default; a later switch re-bands
    through ``role_windows``, which does pass it.

    Walked in ``MANAGED_ROLES`` order rather than the mapping's, because
    ``HWND_TOPMOST`` inserts at the *top* of the band: that order is what puts
    Genau's transparent HUD above the main player's video in video mode, and the policy says so
    outright ("Genau is promoted last").

    *beneath* is the loading overlay, when this runs under it: each promotion
    lands directly under it, which keeps the same order one slot lower.
    """
    for role in MANAGED_ROLES:
        hwnd = role_hwnds.get(role, 0)
        if hwnd:
            set_always_on_top(hwnd, role_topmost(role, mode), under=beneath)


def _apply_main_slot_visibility(main_player_hwnd: int, genau_hwnd: int, mode: str) -> None:
    """Park whichever slot-mate *mode* leaves idle.

    The main player and Genau share the main player's rect; the slot swaps by minimizing the idle
    one (which keeps its taskbar button) and restoring the active one.  Disable
    both windows' DWM transitions first so those minimize/restores are instant —
    no visible animation.  The main player is the idle one in genau mode; in video mode
    neither is, because Genau's HUD is drawn over the main player's video.

    Safe under the loading overlay: minimizing moves no window into the topmost
    band, so nothing can flash over it.
    """
    for hwnd in (main_player_hwnd, genau_hwnd):
        if hwnd:
            disable_window_transitions(hwnd)
    if main_player_hwnd and not main_player_displays(mode):
        minimize_window(main_player_hwnd, activate=False)


def apply_startup_window_state(
    *,
    portrait_hwnd: int,
    landscape_hwnd: int,
    genau_hwnd: int,
    main_player_hwnd: int,
    dashboard_hwnd: int = 0,
    rfb_hwnd: int = 0,
    mode: str = STARTUP_MAIN_MODE,
    beneath: int = 0,
) -> dict[str, int]:
    """Set the full window state for the mode the session opens in: bands, then
    visibility.

    *beneath* is the loading overlay when this runs under one, and keeps the
    cover on top across the walk (:func:`apply_topmost_bands`).  Zero without a
    cover: the integration path, and the re-band after the cover has gone.
    """
    role_hwnds = _startup_role_hwnds(
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=genau_hwnd,
        main_player_hwnd=main_player_hwnd,
        dashboard_hwnd=dashboard_hwnd,
        rfb_hwnd=rfb_hwnd,
    )
    apply_topmost_bands(role_hwnds, mode, beneath=beneath)
    _apply_main_slot_visibility(main_player_hwnd, genau_hwnd, mode)
    return role_hwnds


@dataclass
class _LaunchedChildren:
    """What the startup sequence has spawned so far.

    Accumulated as each child launches so that if a checkpoint cancels
    (``StartupCancelled``), the orchestrator can be handed exactly what to
    tear down — no more, no less.
    """

    pids: list[int] = field(default_factory=list)
    rfb_hwnd: int = 0
    origenerator_taken_over: bool = False
    origenerator_already_open: bool = False

    def hosts(self, app: HostedApp) -> int:
        self.origenerator_already_open = app.already_open
        self.origenerator_taken_over = app.taken_over
        if not app.taken_over:
            self.pids.append(app.pid)
        return app.pid


def release_the_players(m: LaunchManifest, main_mode: MainMode) -> None:
    """Start the players the session's mode puts to work.

    Startup holds every one of them so nothing plays into a room that is still
    being built; this releases exactly the ones the mode shows — Genau (with
    its audio) in both, the main player in video mode alone — so nothing plays into a
    minimized window or drives the OSR2 unasked.

    Called by the sequencer on the path with no cover, and by the orchestrator on
    the path with one — there, only once the cover has actually left the screen.
    That is the whole reason it is a function of its own: released with the
    phases, playback starts while the cover is still hiding it, and the first
    seconds of the video are spent under it.
    """
    write_flag_file(m.commands.main_player_paused_file, not main_player_displays(main_mode))
    for flag_file in (m.commands.genau_paused_file, m.commands.audio_paused_file):
        write_flag_file(flag_file, False)
    # The Robot Hand rides Genau's command channel rather than that flag (see
    # seed_startup_states, which holds it there), so the mode where it drives
    # outright has to be told here or it never starts.  Only genau mode: in video
    # mode the dispatch loop's arbiter picks between the hand and the funscript
    # on its first tick, and a RESUME here would start the hand against a
    # funscript that is about to take the device — the same reason leaving
    # OmniPause resumes it in genau mode alone.
    if main_mode == MAIN_GENAU_MODE:
        append_command(Path(m.commands.genau_cmd_file), "RESUME")


def run_startup_sequence(
    *,
    manifest_path: str | Path,
    state_dir: str | Path,
    progress: ProgressReporter | None = None,
    hide_windows: bool = False,
    env: SessionEnvironment = ORDINARY_SESSION,
) -> StartupResult:
    """Run the full startup sequence, returning all PIDs and the layout plan.

    When *hide_windows* is True, the satellite windows launch under the loading
    overlay and all positioning is deferred to the end so everything appears at
    once.  The window handles are returned in ``StartupResult.role_hwnds``.

    Each ``progress.advance`` is a cancellation checkpoint: if the loading
    screen has dropped the cancel flag, the reporter raises ``StartupCancelled``
    and this re-raises it tagged with everything launched so far, so the caller
    can tear the half-built session down.
    """
    if progress is None:
        progress = NullProgress()

    launched = _LaunchedChildren()
    try:
        return _run_startup_phases(
            manifest_path=manifest_path,
            state_dir=state_dir,
            progress=progress,
            hide_windows=hide_windows,
            launched=launched,
            env=env,
        )
    except StartupCancelled as cancelled:
        cancelled.launched_pids = launched.pids
        cancelled.rfb_hwnd = launched.rfb_hwnd
        cancelled.origenerator_taken_over = launched.origenerator_taken_over
        raise



@dataclass(frozen=True)
class _CoreSession:
    """The children phase 1 leaves, and the main slot's mode it resumed into."""

    main_mode: MainMode
    portrait_pid: int
    landscape_pid: int
    genau_pid: int
    main_player_pid: int
    origenerator_pid: int
    origenerator_taken_over: bool
    origenerator_already_open: bool
    # The main player's status file, dropped once phase 1 has spent last session's copy —
    # phase 4 holds the overlay on the new one appearing.
    main_player_status_file: Path


def _plan_the_layout(m: LaunchManifest) -> ScreenLayout:
    """Every window's rect, from the monitors and the manifest's layout section,
    made before the first child so each player opens where it plays."""
    return screen_layout(m.layout)


def _launch_the_satellites(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    state_dir: Path,
    project_dirs: str,
    launched: _LaunchedChildren,
) -> tuple[str, int, int]:
    """The core session: both satellite players, and the mode it resumed into.

    Returns the mode last session was closed in — the core session has just
    seeded every cross-process flag for it, and what is left is the half only
    this side can do: park the idle slot-mate, band the pair, and reveal on the
    right player.
    """
    core_result_file = _build_unique_result_path(state_dir, "core_session")
    broker_launcher_raw = m.commands.broker_tray_launcher.strip()
    regen_metadata_raw = m.regen.metadata_root.strip()
    # Each satellite's whole launch bundle, built once where the manifest is read.

    def _slot(player: Player, side: str, sources: str, playlist: str, rect):
        return SatelliteSlot(
            player=player,
            sources=sources,
            channels=replace(
                SatelliteChannels.from_manifest(
                    m.commands, side,
                    play_points=state_dir / play_points_filename(side)),
                playlist=build_playlist_file_path(state_dir, playlist)),
            log_file=state_dir / f"{side}_satellite.log",
            rect=rect,
        )

    portrait_slot = _slot(Player.PORTRAIT, "portrait", m.media.portrait_dirs,
                          PLAYLIST_PORTRAIT, plan.portrait)
    landscape_slot = _slot(Player.LANDSCAPE, "landscape", m.media.landscape_dirs,
                           PLAYLIST_LANDSCAPE, plan.landscape)
    main_mode = start_core_session(
        config_path=m.runtime.config_path,
        broker_cmd_file=m.commands.broker_cmd_file,
        broker_tray_launcher=Path(broker_launcher_raw) if broker_launcher_raw else None,
        broker_heartbeat_file=m.commands.broker_heartbeat_file,
        random_favs_browser_manifest_file=m.random_favs_browser.manifest_file,
        genau_paused_file=m.commands.genau_paused_file,
        genau_cmd_file=m.commands.genau_cmd_file,
        audio_paused_file=m.commands.audio_paused_file,
        main_player_paused_file=m.commands.main_player_paused_file,
        audio_volume_file=m.commands.audio_volume_file,
        main_player_cmd_file=m.commands.main_player_cmd_file,
        satellite_python_exe=m.executables.python_exe,
        satellite_module=m.modules.satellite_module,
        portrait=portrait_slot,
        landscape=landscape_slot,
        main_player_status_file=m.commands.main_player_status_file,
        dashboard_cmd_file=m.commands.dashboard_cmd_file,
        main_sources=m.media.main_player_library_sources,
        favs_file=m.media.favs_file,
        state_dir=state_dir,
        result_file=str(core_result_file),
        regen_metadata_root=Path(regen_metadata_raw) if regen_metadata_raw else None,
        # The satellites import player_core, so a named player_core checkout
        # must reach them exactly as it reaches Genau and the main player — without this
        # they quietly ran the venv's primary while everything else ran the
        # branch.
        project_dirs=project_dirs,
    )
    core_pids = _read_result_pids(core_result_file)
    portrait_pid = core_pids["portrait_pid"]
    landscape_pid = core_pids["landscape_pid"]
    launched.pids.extend([portrait_pid, landscape_pid])
    logger.info(
        "Core session launched: portrait=%d landscape=%d",
        portrait_pid, landscape_pid,
    )
    return main_mode, portrait_pid, landscape_pid


def _launch_the_main_slot_players(
    m: LaunchManifest,
    *,
    layout: ScreenLayout,
    state_dir: Path,
    project_dirs: str,
    launched: _LaunchedChildren,
) -> tuple[int, int, Path]:
    """Genau and the main player, who share the main slot's rect, and the main player's status file.

    That file is how startup learns the main player has finished loading, so it is dropped
    here — after ``start_core_session`` has read last session's copy to resume
    The main player onto the video it names, and before the main player could write a new one.
    """
    regen_metadata_raw = m.regen.metadata_root.strip()
    # Launch Genau and the main player as early as possible so they can initialise
    # pygame, scan media, and decode first frames while the rest of startup
    # continues.  Both share the Main slot's rect, which depends only on
    # the secondary monitor + main_top_ratio (already computed above).
    main_media_rect = compute_main_media_rect(
        secondary_monitor=layout.secondary_monitor, layout_config=layout.config,
    )
    # Genau's drive readout, which main player draws inside its console in video mode.  Named
    # here and handed to BOTH players, because each resolving it for itself is how
    # it went wrong: Genau derived it from its own config's state dir and wrote it
    # into the Genau repo, while the main player was told to read it out of Fun Time's — so
    # Video mode showed a console with the Genau half missing.
    genau_state = Path(m.commands.genau_cmd_file).parent
    genau_drive_file = genau_state / state_files.GENAU_DRIVE
    # Genau's own resume: it rescans its clips folder every launch and opens at
    # the top of it, so the clip the last session was left showing survives only
    # in the status file it published — read here, before this session's Genau
    # starts writing over it.
    genau_clip = read_genau_status(Path(m.commands.genau_status_file)).clip
    # project_dirs: which checkout of ../genau these two are run out of.  Empty
    # in an ordinary session — they resolve through their venv's editable
    # install, which is the primary — and a worktree of that repo while a branch
    # of it is being judged.
    genau_pid = launch_genau(
        python_exe=m.executables.genau_python_exe,
        genau_module=m.modules.genau_module,
        config_path=m.runtime.genau_config_path,
        clips_folder=m.media.genau_clips,
        genau_x=main_media_rect.x,
        genau_y=main_media_rect.y,
        genau_width=main_media_rect.width,
        genau_height=main_media_rect.height,
        command_file=m.commands.genau_cmd_file,
        paused_file=m.commands.genau_paused_file,
        console_file=m.commands.main_player_console_file,
        drive_file=genau_drive_file,
        status_file=m.commands.genau_status_file,
        dashboard_cmd_file=m.commands.dashboard_cmd_file,
        start_clip=genau_clip,
        project_dirs=project_dirs,
    )
    # The main player's status file is how startup learns the main player has finished loading, and it
    # can only say that once last session's copy is gone.  start_core_session
    # read that one already, to resume the main player onto the video it names, so this is
    # the first moment it is spent — and the last before the main player could write a new
    # one.  See _wait_for_main_player_loaded.
    main_player_status_file = Path(m.commands.main_player_status_file)
    main_player_status_file.unlink(missing_ok=True)
    main_player_pid = launch_main_player(
        python_exe=m.executables.genau_python_exe,
        main_player_module=m.modules.main_player_module,
        config_path=m.runtime.config_path,
        playlist_file=m.commands.main_player_playlist_file,
        command_file=m.commands.main_player_cmd_file,
        paused_file=m.commands.main_player_paused_file,
        status_file=m.commands.main_player_status_file,
        console_file=m.commands.main_player_console_file,
        drive_file=genau_drive_file,
        dashboard_cmd_file=m.commands.dashboard_cmd_file,
        log_file=state_dir / "main_player.log",
        state_dir=state_dir,
        main_player_x=main_media_rect.x,
        main_player_y=main_media_rect.y,
        main_player_width=main_media_rect.width,
        main_player_height=main_media_rect.height,
        clips_dir=m.media.genau_clips,
        metadata_dir=regen_metadata_raw or None,
        project_dirs=project_dirs,
    )
    launched.pids.extend([genau_pid, main_player_pid])
    return genau_pid, main_player_pid, main_player_status_file


def _launch_the_hosted_origenerator(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    project_dirs: str,
    launched: _LaunchedChildren,
) -> int:
    """The hosted app, first of the children and waited on by none, or 0 for a
    session the config names no checkout for."""
    app = bring_up_the_hosted_app(m, plan=plan, project_dirs=project_dirs)
    return 0 if app is None else launched.hosts(app)


def _launch_core_media(
    m: LaunchManifest,
    *,
    layout: ScreenLayout,
    state_dir: Path,
    launched: _LaunchedChildren,
) -> _CoreSession:
    """Phase 1: the hosted app, then the two satellites, then Genau and the main player.

    Nothing here waits for a window.  Everything is started as early as it can
    be, slowest first, so each child's own boot — ComfyUI, pygame, a media
    scan, first frames — runs under the rest of startup, the hosted app's on
    past the reveal.
    """
    # Read before the first launch that needs it: every child below takes the
    # named checkouts, the satellites and the hosted app included, because they
    # all import player_core.
    project_dirs = m.runtime.genau_project_dirs
    origenerator_pid = _launch_the_hosted_origenerator(
        m, plan=layout.plan, project_dirs=project_dirs, launched=launched)
    main_mode, portrait_pid, landscape_pid = _launch_the_satellites(
        m, plan=layout.plan, state_dir=state_dir, project_dirs=project_dirs,
        launched=launched)
    genau_pid, main_player_pid, main_player_status_file = _launch_the_main_slot_players(
        m, layout=layout, state_dir=state_dir, project_dirs=project_dirs,
        launched=launched)

    return _CoreSession(
        main_mode=main_mode,
        portrait_pid=portrait_pid,
        landscape_pid=landscape_pid,
        genau_pid=genau_pid,
        main_player_pid=main_player_pid,
        origenerator_pid=origenerator_pid,
        origenerator_taken_over=launched.origenerator_taken_over,
        origenerator_already_open=launched.origenerator_already_open,
        main_player_status_file=main_player_status_file,
    )


# What the two UI companions wait out before they are launched.  Unexplained
# since the sequencer replaced the AHK startup, and taking it out was tried on
# the hidden desktop rather than argued about: the suite passed three times with
# it, and the one run without it left the hosted app's window restored from a
# mode switch but never in the topmost band (test_origenerator_mode_integration).
# Twelve runs of that test ALONE, six per arm, pass either way, so whatever
# leans on this needs a whole room going up at once.  Kept, unproven.
_COMPANION_LAUNCH_DELAY_S = 1.2


def _position_windows_now(plan: WindowLayoutPlan, main_mode: MainMode, *,
                          env: SessionEnvironment,
                          progress: ProgressReporter) -> dict[str, int]:
    """Phase 2, on the path with no cover: place and band every window at once.

    No progress reporting here: this is the integration path, and the loading
    screen (with the reporter that drives it) belongs to the other one.
    """
    activate = not env.integration
    portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
    _move_window_to(portrait_hwnd, plan.portrait, "portrait satellite", activate=activate)
    _move_window_to(landscape_hwnd, plan.landscape, "landscape satellite", activate=activate)
    logger.info("Core windows positioned")

    role_hwnds = apply_startup_window_state(
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=_resolve_genau_window(progress),
        main_player_hwnd=wait_for_window_by_title("Main Player", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
        mode=main_mode,
    )
    logger.info("Startup window state applied")
    return role_hwnds


def _launch_the_companions(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    state_dir: Path,
    manifest_path: Path,
    launched: _LaunchedChildren,
) -> dict[str, int]:
    """Phase 3: the dashboard and the audio companion, the run's last children."""
    time.sleep(_COMPANION_LAUNCH_DELAY_S)
    ui_result_file = _build_unique_result_path(state_dir, "ui_companions")
    launch_ui_companions(
        python_exe=m.executables.python_exe,
        dashboard_module=m.modules.dashboard_module,
        dashboard_enabled=m.dashboard_enabled,
        dashboard_log_file=state_dir / "dashboard.log",
        windows_bridge_manifest_path=str(manifest_path),
        dashboard_x=plan.dashboard.x,
        dashboard_y=plan.dashboard.y,
        dashboard_width=plan.dashboard.width,
        dashboard_height=plan.dashboard.height,
        # The reference popup opens over the RFB's rect, so the dashboard needs it.
        rfb_x=plan.random_favs_browser.x,
        rfb_y=plan.random_favs_browser.y,
        rfb_width=plan.random_favs_browser.width,
        rfb_height=plan.random_favs_browser.height,
        audio_module=m.modules.audio_module,
        config_path=m.runtime.config_path,
        audio_folder=m.media.genau_audio,
        result_file=str(ui_result_file),
        project_dirs=m.runtime.genau_project_dirs,
    )
    ui_pids = _read_result_pids(ui_result_file)
    launched.pids.extend([ui_pids["dashboard_pid"], ui_pids["audio_pid"]])
    return ui_pids


def _wait_for_the_room_to_be_drawing(
    m: LaunchManifest,
    *,
    main_player_status_file: Path,
    progress: ProgressReporter,
) -> None:
    """Hold the cover until every player has a picture under it.

    The main player is the third player and by now the only one still loading: its window
    has been up since half a second after launch with its own loading screen
    painted into it, so revealing on the window alone shows his progress bar
    instead of a video.  The two satellites are the same case one step earlier —
    their windows exist within a second of launch and stay BLACK until mpv has
    opened the first clip, which is what "the windows are not ready when the
    loading screen goes away" looks like.

    Neither wait gets to keep the desktop: a player that never arrives is
    revealed over anyway, and the log says which one.
    """
    if not _wait_for_main_player_loaded(main_player_status_file, progress):
        logger.warning(
            "the main player reported no video within %.0fs; revealing over whatever it "
            "still has on screen", MAIN_PLAYER_LOAD_TIMEOUT_S,
        )
    if not _wait_for_players_drawing(
        (m.commands.portrait_status_file,
         m.commands.landscape_status_file),
        progress,
    ):
        logger.warning(
            "A satellite reported no frames within %.0fs; revealing anyway",
            SATELLITE_PLAY_TIMEOUT_S,
        )


def _place_and_park_under_the_cover(
    *,
    plan: WindowLayoutPlan,
    main_mode: MainMode,
    portrait_hwnd: int,
    landscape_hwnd: int,
    rfb_hwnd: int,
    dashboard_pid: int,
    progress: ProgressReporter,
) -> dict[str, int]:
    """Place every window where the plan says and park the idle slot-mate.

    Nothing here can show over the overlay: the players are still out of the
    topmost band, and no move or show lifts a window above the band it is not
    in.  ``_fix_post_loading_windows`` bands them, under the overlay.  This is
    still the last moment the dashboard is resolvable, and it is hidden
    (SW_HIDE) under the overlay, so its lookup must include hidden windows.
    """
    _move_window_to(portrait_hwnd, plan.portrait, "portrait satellite", activate=False)
    _move_window_to(landscape_hwnd, plan.landscape, "landscape satellite", activate=False)
    logger.info("Core windows positioned (deferred reveal)")

    dash_hwnd = (
        wait_for_window_by_title(
            "Fun Time", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True, include_hidden=True,
        )
        if dashboard_pid
        else 0
    )
    role_hwnds = _startup_role_hwnds(
        rfb_hwnd=rfb_hwnd,
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=_resolve_genau_window(progress),
        main_player_hwnd=wait_for_window_by_title("Main Player", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
        dashboard_hwnd=dash_hwnd,
    )
    _apply_main_slot_visibility(role_hwnds["main_player"], role_hwnds["genau"], main_mode)
    logger.info("Startup windows resolved and parked (bands deferred past the overlay)")
    return role_hwnds


def _settle_the_room_under_the_cover(
    m: LaunchManifest,
    *,
    core: _CoreSession,
    plan: WindowLayoutPlan,
    rfb_hwnd: int,
    dashboard_pid: int,
    progress: ProgressReporter,
) -> dict[str, int]:
    """Phase 4, on the path with a loading screen: everything at once, unseen."""
    # Named for the wait it is: until the players open their own windows there
    # is nothing here to position.
    progress.advance("players")
    # The satellites launched playing and own their playlists, so there is
    # nothing to start here — only resolve and position each under the overlay.
    portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
    _wait_for_the_room_to_be_drawing(
        m, main_player_status_file=core.main_player_status_file, progress=progress)

    progress.advance("windows")
    role_hwnds = _place_and_park_under_the_cover(
        plan=plan,
        main_mode=core.main_mode,
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        rfb_hwnd=rfb_hwnd,
        dashboard_pid=dashboard_pid,
        progress=progress,
    )
    progress.advance("finalizing")
    return role_hwnds


def _run_startup_phases(
    *,
    manifest_path: str | Path,
    state_dir: str | Path,
    progress: ProgressReporter,
    hide_windows: bool,
    launched: _LaunchedChildren,
    env: SessionEnvironment,
) -> StartupResult:
    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    m = LaunchManifest.read(manifest_path)
    layout = _plan_the_layout(m)
    plan = layout.plan

    # --- Phase 1: Launch core media stack ---
    progress.advance("services")
    core = _launch_core_media(m, layout=layout, state_dir=state_dir, launched=launched)

    # --- Phase 2: Position windows (layout computed up front) ---
    role_hwnds: dict[str, int] = {}
    if not hide_windows:
        role_hwnds = _position_windows_now(
            plan, core.main_mode, env=env, progress=progress)

    # --- Phase 2.5: Launch Random Favs Browser ---
    progress.advance("browser")
    rfb_hwnd = _maybe_launch_random_favs_browser(m.random_favs_browser, plan, env=env)
    launched.rfb_hwnd = rfb_hwnd

    # --- Phase 3: Launch UI companions ---
    progress.advance("companions")
    ui_pids = _launch_the_companions(
        m, plan=plan, state_dir=state_dir, manifest_path=manifest_path, launched=launched)

    # --- Phase 4 (loading screen only): batch-position everything at once ---
    if hide_windows:
        role_hwnds = _settle_the_room_under_the_cover(
            m, core=core, plan=plan, rfb_hwnd=rfb_hwnd,
            dashboard_pid=ui_pids["dashboard_pid"], progress=progress)

    # A session with nothing to hide under starts playing as soon as it is
    # built.  One with a cover does NOT: the orchestrator calls this once the
    # cover is off, since a player released under it loses its first seconds of
    # video (and Genau's audio) before anyone can see or hear them.
    if not hide_windows:
        release_the_players(m, core.main_mode)

    return StartupResult(
        main_player_pid=core.main_player_pid,
        portrait_pid=core.portrait_pid,
        landscape_pid=core.landscape_pid,
        dashboard_pid=ui_pids["dashboard_pid"],
        genau_pid=core.genau_pid,
        audio_pid=ui_pids["audio_pid"],
        origenerator_pid=core.origenerator_pid,
        origenerator_taken_over=core.origenerator_taken_over,
        origenerator_already_open=core.origenerator_already_open,
        main_mode=core.main_mode,
        role_hwnds=role_hwnds,
        rfb_hwnd=rfb_hwnd,
    )


def _move_window_to(hwnd: int, rect: WindowRect, label: str, *, activate: bool = True) -> None:
    """Move an already-resolved window to *rect* (a no-op warning if unresolved)."""
    if hwnd:
        move_window(hwnd, rect.x, rect.y, rect.width, rect.height, activate=activate)
        logger.info("Positioned %s (hwnd=%d) at %d,%d %dx%d",
                     label, hwnd, rect.x, rect.y, rect.width, rect.height)
    else:
        logger.warning("Could not find window for %s", label)


def _resolve_genau_window(progress: ProgressReporter) -> int:
    """Genau's window, once its thread takes messages: it waits out its first
    clip's decode before its loop starts, and a placement sent sooner lands late."""
    # Exactly, and the plain caption alone: the HUD that renames this window
    # is off until a mode switch, which is after this.
    hwnd = wait_for_window_by_title(
        GENAU_TITLE, timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True)
    if not hwnd:
        return 0
    for _ in range(int(GENAU_ANSWER_TIMEOUT_S * 1000 / ANSWER_TIMEOUT_MS)):
        if progress.cancelled:
            raise StartupCancelled()
        if window_answers(hwnd):  # waits up to ANSWER_TIMEOUT_MS on a busy thread
            return hwnd
    logger.warning("Genau took no messages within %.0fs; placing it anyway, and "
                   "that placement may land late", GENAU_ANSWER_TIMEOUT_S)
    return hwnd


def _resolve_satellite_hwnds() -> tuple[int, int]:
    """The portrait and landscape native-satellite windows, as (portrait, landscape).

    Each side is resolved by its DISTINCT window caption ("Portrait AI Player" vs
    "Landscape AI Player"), so the lookup can never assign one side's window to the
    other — a shared caption could, and that was the portrait/landscape visual swap.

    Deliberately NOT by pid.  The pid we launch with is the venv's
    ``Scripts\\pythonw.exe``, a launcher that spawns the base interpreter as a
    child, and the child is what owns the window — so a pid poll here can only
    ever run out its timeout.  Two of them (plus the main player's) were 25 seconds of a
    28-second loading screen.
    """
    return (
        wait_for_window_by_title(SATELLITE_PORTRAIT_TITLE, timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
        wait_for_window_by_title(SATELLITE_LANDSCAPE_TITLE, timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
    )


# How long the curtain waits for the two satellites to have a picture up.
# Their windows exist within a second of launch and stay BLACK until mpv has
# opened the first clip and drawn a frame — on the 4K landscape library that is
# several seconds — so a reveal timed on the windows alone lifts on two black
# rectangles.  Bounded like the main player's: a player that never gets there does not get
# to keep the desktop.
SATELLITE_PLAY_TIMEOUT_S = 25.0
_PLAY_POLL_S = 0.1


def _wait_for_players_drawing(status_files, progress: ProgressReporter,
                              timeout_s: float = SATELLITE_PLAY_TIMEOUT_S) -> bool:
    """Wait until every satellite is DRAWING, returning whether they all got there.

    The window existing is not the signal, and neither is the process running:
    a satellite opens its window immediately, then spends seconds asking mpv for
    the first clip.  Its status file says ``position_ms`` once frames are
    actually going out, which is the same thing the integration suite waits on
    to call a player started.

    Also a cancellation checkpoint, per poll, like the main player wait it sits beside:
    this is one of the stretches that can run for tens of seconds, and the
    overlay covering it offers Esc.
    """
    files = [Path(path) for path in status_files if path]
    if not files:
        return True
    # Counted rather than clocked: this runs while the room is starting, and a
    # poll loop that asks a monotonic clock is a loop that never ends where the
    # clock is stubbed.
    for _ in range(max(1, int(timeout_s / _PLAY_POLL_S))):
        if progress.cancelled:
            raise StartupCancelled()
        if all(read_satellite_status(path).position_ms > 0 for path in files):
            return True
        time.sleep(_PLAY_POLL_S)
    return False


def _wait_for_main_player_loaded(
    status_file: Path,
    progress: ProgressReporter,
    timeout_s: float = MAIN_PLAYER_LOAD_TIMEOUT_S,
) -> bool:
    """Wait until the main player has a video on screen, returning whether it got there.

    The main player's caption is NOT this signal.  The main player opens its window before reading its
    library and paints its own loading screen into it while it does — so the
    window exists within half a second of launch, however long the library walk
    then runs.  Waiting on the caption alone brings the overlay down over that
    loading screen, which is the one place it must never be seen: standalone main player
    owns its wait, and inside Fun Time, Fun Time owns it.

    The main player's status file is the signal, because the main player writes it only from its playback
    loop.  The stale one is dropped at launch, so a file naming a video is this
    session's the main player saying it is up.  Reading the *video* rather than merely the
    file's existence also survives a read that catches the first write half-done.

    Also a cancellation checkpoint, for the same reason ``advance`` is one — but
    checked per poll rather than once, because this is the one stretch of startup
    that can run for tens of seconds, and the overlay covering it offers Esc.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if progress.cancelled:
            raise StartupCancelled()
        if read_main_player_status(status_file).video:
            return True
        time.sleep(0.1)
    return False


def _maybe_launch_random_favs_browser(
    settings: RandomFavsBrowserSettings,
    plan: WindowLayoutPlan,
    *,
    env: SessionEnvironment,
) -> int:
    """Launch the Random Favs Browser if enabled and position it, returning its
    window handle (0 if not launched) for OmniPause's topmost band."""
    if not settings.enabled:
        return 0

    shortcut_path = settings.shortcut_path
    manifest_file = settings.manifest_file

    shortcut = resolve_shortcut(shortcut_path)
    if not shortcut.target:
        logger.warning("Random Favs Browser skipped: could not resolve shortcut %s", shortcut_path)
        return 0

    # Take a Chrome window snapshot before launch
    before_hwnds = find_windows_by_class(CHROME_WINDOW_CLASS)

    result = launch_random_favs_browser(manifest_file, shortcut=shortcut)
    if not result.should_launch:
        logger.info("Random Favs Browser skipped: launch plan was empty")
        return 0

    # Wait for a new Chrome window to appear
    new_hwnd = _wait_for_new_chrome_window(before_hwnds, timeout_ms=8000)
    if not new_hwnd:
        logger.warning("Random Favs Browser skipped: no new Chrome window appeared")
        return 0

    # Position the browser window
    rect = plan.random_favs_browser
    move_window(new_hwnd, rect.x, rect.y, rect.width, rect.height,
                activate=not env.integration)

    # The RFB's static topmost flag is applied by Phase 4's
    # apply_startup_window_state; nothing window-related to do here.

    logger.info("Random Favs Browser positioned")
    return new_hwnd


def _wait_for_new_chrome_window(before: set[int], timeout_ms: int = 8000) -> int:
    """Wait for a Chrome window that was not in the *before* set."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        appeared = find_windows_by_class(CHROME_WINDOW_CLASS) - before
        if appeared:
            return next(iter(appeared))
        time.sleep(0.2)
    return 0
