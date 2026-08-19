"""Startup sequencer for the Python orchestrator.

Replaces AHK's ``StartWindowsBridge()`` — runs the full startup sequence
in Python: core session launch, window positioning, UI companion launch.
"""
from __future__ import annotations

import configparser
import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from player_core.file_channel import append_command

from .config import LayoutConfig
from .dashboard_runtime import genau_status_path, read_genau_status, read_nau_status
from .satellite_control import read_satellite_status
from .mode_plan import STARTUP_MAIN_MODE, genau_active, nau_displays
from .monitors import enumerate_monitors, get_logical_monitor_rects
from .overlay_progress import NullProgress, ProgressReporter, StartupCancelled
from .windows_bridge_random_favs_browser import launch_random_favs_browser
from .runtime_flow import write_flag_file
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
    launch_genau,
    launch_nau,
    launch_origenerator,
    launch_ui_companions,
    start_core_session,
)
from .window_roles import MANAGED_ROLES, ORIGENERATOR_ROLE_TITLES, role_topmost
from .win32 import (
    disable_window_transitions,
    find_window_for_process,
    minimize_window,
    move_window,
    restore_window,
    set_always_on_top,
    wait_for_window_by_title,
)
from .window_layout import (
    WindowLayoutPlan,
    WindowRect,
    compute_main_media_rect,
    compute_window_layout,
)

logger = logging.getLogger(__name__)

# How long startup waits for a launched app to put its window on screen.  A poll
# returns the moment the window appears — a satellite's takes about half a second
# — so this is a ceiling for a machine under load, not a cost anyone pays.
WINDOW_RESOLVE_TIMEOUT_S = 15.0

# How long startup waits for Nau to finish loading.  Wide enough for the worst
# case, a cold duration cache: one ffprobe per unprobed video, measured at 28s
# for 525 of them, and paid once because the cache persists.  Its ceiling is the
# overlay's own patience: the two waits in this phase run back to back under a
# single progress write, and the overlay tears itself down when that file has
# gone ``loading_screen.STALE_TIMEOUT_S`` without changing.  A test pins the sum.
NAU_LOAD_TIMEOUT_S = 40.0


@dataclass(frozen=True)
class StartupResult:
    nau_pid: int
    portrait_pid: int
    landscape_pid: int
    dashboard_pid: int
    genau_pid: int
    audio_pid: int
    layout_plan: WindowLayoutPlan
    # The hosted Origenerator's process, or 0 for a session with none configured.
    origenerator_pid: int = 0
    # Which player the main slot was revealed on — last session's, resumed.
    # Carried out because the post-overlay z-order pass runs from the
    # orchestrator and has to re-assert the same policy these phases applied.
    main_mode: str = STARTUP_MAIN_MODE
    # The satellite side's resumed mode, for the same reason: a session that
    # opens in origenerator mode needs its hosted window restored behind the
    # overlay and banded by the post-overlay pass, not popped up after the
    # reveal the loading screen exists to conceal.
    satellites_mode: str = 'player'
    rfb_hwnd: int = 0
    # HWNDs resolved while every window was still visible; the dispatch
    # loop's role cache is seeded from this (hidden windows cannot be
    # re-resolved by pid/title lookups).
    role_hwnds: dict[str, int] = field(default_factory=dict)


def _read_manifest(path: str | Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(path), encoding="utf-8")
    return parser


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
    nau_hwnd: int,
    dashboard_hwnd: int = 0,
    rfb_hwnd: int = 0,
    origenerator_hwnd: int = 0,
) -> dict[str, int]:
    """The managed windows by role, as resolved at startup."""
    return {
        "portrait": portrait_hwnd,
        "landscape": landscape_hwnd,
        "genau": genau_hwnd,
        "nau": nau_hwnd,
        "dashboard": dashboard_hwnd,
        "rfb": rfb_hwnd,
        "origenerator": origenerator_hwnd,
    }


def _apply_topmost_bands(role_hwnds: dict[str, int], mode: str,
                         satellites_mode: str = "player") -> None:
    """Give each managed window its topmost flag from the shared ``role_topmost``
    policy for *mode* — the same policy omnipause and mode switches honor, so
    they can never disagree.

    Walked in ``MANAGED_ROLES`` order rather than the mapping's, because
    ``HWND_TOPMOST`` inserts at the *top* of the band: that order is what puts
    Genau's transparent HUD above Nau's video in hybrid, and the policy says so
    outright ("Genau is promoted last").

    Never call this while the loading overlay is up.  The same insert-at-the-top
    behavior, against an overlay that is itself topmost, draws each promoted
    window over the overlay until its next poll re-asserts itself — the flashing
    the overlay exists to prevent.
    """
    for role in MANAGED_ROLES:
        hwnd = role_hwnds.get(role, 0)
        if hwnd:
            set_always_on_top(hwnd, role_topmost(role, mode, satellites_mode))


def _apply_main_slot_visibility(nau_hwnd: int, genau_hwnd: int, mode: str) -> None:
    """Park whichever slot-mate *mode* leaves idle.

    Nau and Genau share the main player's rect; the slot swaps by minimizing the idle
    one (which keeps its taskbar button) and restoring the active one.  Disable
    both windows' DWM transitions first so those minimize/restores are instant —
    no visible animation.  Genau is the idle one in nau mode and Nau in genau
    mode; in hybrid neither is, because Genau's HUD is drawn over Nau's video.

    Safe behind the loading overlay: minimizing moves no window into the topmost
    band, so nothing can flash over it.
    """
    for hwnd in (nau_hwnd, genau_hwnd):
        if hwnd:
            disable_window_transitions(hwnd)
    idle = 0
    if not genau_active(mode):
        idle = genau_hwnd
    elif not nau_displays(mode):
        idle = nau_hwnd
    if idle:
        minimize_window(idle, activate=False)


def _apply_startup_window_state(
    *,
    portrait_hwnd: int,
    landscape_hwnd: int,
    genau_hwnd: int,
    nau_hwnd: int,
    dashboard_hwnd: int = 0,
    rfb_hwnd: int = 0,
    origenerator_hwnd: int = 0,
    mode: str = STARTUP_MAIN_MODE,
    satellites_mode: str = "player",
) -> dict[str, int]:
    """Set the full window state for the mode the session opens in: bands, then
    visibility.

    Only for callers with no loading overlay on screen — the integration path,
    which has nothing to hide behind, and ``_fix_post_loading_windows``, which
    runs after the overlay process has exited.
    """
    role_hwnds = _startup_role_hwnds(
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=genau_hwnd,
        nau_hwnd=nau_hwnd,
        dashboard_hwnd=dashboard_hwnd,
        rfb_hwnd=rfb_hwnd,
        origenerator_hwnd=origenerator_hwnd,
    )
    _apply_topmost_bands(role_hwnds, mode, satellites_mode)
    _apply_main_slot_visibility(nau_hwnd, genau_hwnd, mode)
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


def run_startup_sequence(
    *,
    manifest_path: str | Path,
    state_dir: str | Path,
    progress: ProgressReporter | None = None,
    hide_windows: bool = False,
) -> StartupResult:
    """Run the full startup sequence, returning all PIDs and the layout plan.

    When *hide_windows* is True, the satellite windows launch behind the loading
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
        )
    except StartupCancelled as cancelled:
        cancelled.launched_pids = launched.pids
        cancelled.rfb_hwnd = launched.rfb_hwnd
        raise


def _run_startup_phases(
    *,
    manifest_path: str | Path,
    state_dir: str | Path,
    progress: ProgressReporter,
    hide_windows: bool,
    launched: _LaunchedChildren,
) -> StartupResult:
    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    m = _read_manifest(manifest_path)

    # Compute the window layout up front so the satellites can launch straight
    # into their real portrait/landscape rects (mpv sizes its output to the launch
    # geometry and will NOT rescale when a later Win32 move resizes the window),
    # exactly as Nau launches straight into its main slot rect below.
    layout_cfg = _layout_config_from_manifest(m)
    monitors = enumerate_monitors()
    primary_rect, secondary_rect = get_logical_monitor_rects(
        monitors, primary_index=layout_cfg.primary_monitor, secondary_index=layout_cfg.secondary_monitor,
    )
    plan = compute_window_layout(
        primary_monitor=primary_rect,
        secondary_monitor=secondary_rect,
        layout_config=layout_cfg,
    )

    # --- Phase 1: Launch core media stack ---
    progress.advance("services")
    core_result_file = _build_unique_result_path(state_dir, "core_session")
    broker_launcher_raw = m["commands"].get("broker_tray_launcher", "").strip()
    regen_media_raw = m.get("regen", "media_root", fallback="").strip()
    regen_metadata_raw = m.get("regen", "metadata_root", fallback="").strip()
    # Read before the first launch that needs it: the satellites and the
    # hosted Origenerator take the named checkouts exactly as Genau and Nau
    # below do.
    genau_project_dirs = m["runtime"].get("genau_project_dirs", "")
    # The mode last session was closed in, which the core session has just seeded
    # every cross-process flag for.  What is left is the half only this side can
    # do: park the idle slot-mate, band the pair, and reveal on the right player.
    main_mode = start_core_session(
        config_path=m["runtime"]["config_path"],
        broker_cmd_file=m["commands"]["broker_cmd_file"],
        broker_tray_launcher=Path(broker_launcher_raw) if broker_launcher_raw else None,
        broker_heartbeat_file=m["commands"]["broker_heartbeat_file"],
        random_favs_browser_manifest_file=m["random_favs_browser"]["manifest_file"],
        genau_paused_file=m["commands"]["genau_paused_file"],
        genau_cmd_file=m["commands"]["genau_cmd_file"],
        audio_paused_file=m["commands"]["audio_paused_file"],
        nau_paused_file=m["commands"]["nau_paused_file"],
        audio_volume_file=m["commands"]["audio_volume_file"],
        nau_cmd_file=m["commands"]["nau_cmd_file"],
        satellite_python_exe=m["executables"]["python_exe"],
        satellite_module=m["modules"]["satellite_module"],
        portrait_cmd_file=m["commands"]["portrait_cmd_file"],
        portrait_paused_file=m["commands"]["portrait_paused_file"],
        portrait_status_file=m["commands"]["portrait_status_file"],
        landscape_cmd_file=m["commands"]["landscape_cmd_file"],
        landscape_paused_file=m["commands"]["landscape_paused_file"],
        landscape_status_file=m["commands"]["landscape_status_file"],
        nau_status_file=m["commands"]["nau_status_file"],
        portrait_log_file=state_dir / "portrait_satellite.log",
        landscape_log_file=state_dir / "landscape_satellite.log",
        portrait_rect=plan.portrait,
        landscape_rect=plan.landscape,
        portrait_hud_file=m["commands"]["portrait_hud_file"],
        landscape_hud_file=m["commands"]["landscape_hud_file"],
        dashboard_cmd_file=m["commands"]["dashboard_cmd_file"],
        main_sources=m["media"]["nau_library_sources"],
        portrait_sources=m["media"]["portrait_dirs"],
        landscape_sources=m["media"]["landscape_dirs"],
        favs_file=m["media"]["favs_file"],
        state_dir=state_dir,
        result_file=str(core_result_file),
        regen_media_root=Path(regen_media_raw) if regen_media_raw else None,
        regen_metadata_root=Path(regen_metadata_raw) if regen_metadata_raw else None,
        # The satellites import player_core, so a named player_core checkout
        # must reach them exactly as it reaches Genau and Nau — without this
        # they quietly ran the venv's primary while everything else ran the
        # branch.
        project_dirs=genau_project_dirs,
    )
    core_pids = _read_result_pids(core_result_file)
    portrait_pid = core_pids["portrait_pid"]
    landscape_pid = core_pids["landscape_pid"]
    launched.pids.extend([portrait_pid, landscape_pid])
    logger.info(
        "Core session launched: portrait=%d landscape=%d",
        portrait_pid, landscape_pid,
    )

    # Launch Genau and Nau as early as possible so they can initialise
    # pygame, scan media, and decode first frames while the rest of startup
    # continues.  Both share the Main slot's rect, which depends only on
    # the secondary monitor + main_top_ratio (already computed above).
    main_media_rect = compute_main_media_rect(
        secondary_monitor=secondary_rect, layout_config=layout_cfg,
    )
    # Genau's drive readout, which Nau draws inside its console in Hybrid.  Named
    # here and handed to BOTH players, because each resolving it for itself is how
    # it went wrong: Genau derived it from its own config's state dir and wrote it
    # into the Genau repo, while Nau was told to read it out of Fun Time's — so
    # Hybrid showed a console with the Genau half missing.
    genau_state = Path(m["commands"]["genau_cmd_file"]).parent
    genau_drive_file = genau_state / "genau_drive.txt"
    # Genau's own resume: it rescans its clips folder every launch and opens at
    # the top of it, so the clip the last session was left showing survives only
    # in the status file it published — read here, before this session's Genau
    # starts writing over it.
    genau_clip = read_genau_status(genau_status_path(genau_state)).clip
    # genau_project_dirs (read above, before the satellites needed it): which
    # checkout of ../genau these two are run out of.  Empty in an ordinary
    # session — they resolve through their venv's editable install, which is the
    # primary — and a worktree of that repo while a branch of it is being judged.
    genau_pid = launch_genau(
        python_exe=m["executables"]["genau_python_exe"],
        genau_module=m["modules"]["genau_module"],
        config_path=m["runtime"]["genau_config_path"],
        clips_folder=m["media"]["genau_clips"],
        genau_x=main_media_rect.x,
        genau_y=main_media_rect.y,
        genau_width=main_media_rect.width,
        genau_height=main_media_rect.height,
        command_file=m["commands"]["genau_cmd_file"],
        paused_file=m["commands"]["genau_paused_file"],
        console_file=m["commands"]["nau_console_file"],
        drive_file=genau_drive_file,
        dashboard_cmd_file=m["commands"]["dashboard_cmd_file"],
        start_clip=genau_clip,
        project_dirs=genau_project_dirs,
    )
    # Nau's status file is how startup learns Nau has finished loading, and it
    # can only say that once last session's copy is gone.  start_core_session
    # read that one already, to resume Nau onto the video it names, so this is
    # the first moment it is spent — and the last before Nau could write a new
    # one.  See _wait_for_nau_loaded.
    nau_status_file = Path(m["commands"]["nau_status_file"])
    nau_status_file.unlink(missing_ok=True)
    nau_pid = launch_nau(
        python_exe=m["executables"]["genau_python_exe"],
        nau_module=m["modules"]["nau_module"],
        config_path=m["runtime"]["genau_config_path"],
        playlist_file=m["commands"]["nau_playlist_file"],
        command_file=m["commands"]["nau_cmd_file"],
        paused_file=m["commands"]["nau_paused_file"],
        status_file=m["commands"]["nau_status_file"],
        console_file=m["commands"]["nau_console_file"],
        drive_file=genau_drive_file,
        dashboard_cmd_file=m["commands"]["dashboard_cmd_file"],
        log_file=state_dir / "nau.log",
        nau_x=main_media_rect.x,
        nau_y=main_media_rect.y,
        nau_width=main_media_rect.width,
        nau_height=main_media_rect.height,
        metadata_dir=regen_metadata_raw or None,
        project_dirs=genau_project_dirs,
    )
    launched.pids.extend([genau_pid, nau_pid])

    # The hosted Origenerator, when the config names a checkout: launched with
    # the players so its own boot (ComfyUI, the library maintenance passes)
    # runs behind the rest of startup.  Nothing here waits on it — it comes up
    # parked by design, and the dispatch loop adopts its window whenever it
    # appears, restoring it only if the session is in origenerator mode.
    origenerator_dir = m["runtime"].get("origenerator_dir", "").strip()
    origenerator_pid = 0
    if origenerator_dir:
        # Clear a "1" a prior session's OmniPause stranded in the hosted app's
        # paused flag: the app reads it every tick, so a stale freeze made
        # every show open frozen while the room ran.  The room opens unpaused
        # (OmniPause is never resumed into), so the flag opens unpaused too.
        write_flag_file(m["commands"]["origenerator_paused_file"], False)
        # And the command file, for the same reason and one more: the app drains
        # whatever is in it on its first tick, so a verb the last session left
        # unread would land on this one -- a stranded OPEN_SHOWS filling the
        # regions of a session that opened in player mode.
        origenerator_cmd_file = Path(m["commands"]["origenerator_cmd_file"])
        origenerator_cmd_file.parent.mkdir(parents=True, exist_ok=True)
        origenerator_cmd_file.write_text("", encoding="utf-8")
        origenerator_pid = launch_origenerator(
            python_exe=(m["executables"].get("origenerator_python_exe", "").strip()
                        or m["executables"]["python_exe"]),
            origenerator_dir=origenerator_dir,
            layout_plan=plan,
            command_file=m["commands"]["origenerator_cmd_file"],
            paused_file=m["commands"]["origenerator_paused_file"],
            status_file=m["commands"]["origenerator_status_file"],
            dashboard_cmd_file=m["commands"]["dashboard_cmd_file"],
            # It imports player_core too (the shows' HUD is the players'
            # shared one), so a named checkout reaches it like everyone else.
            project_dirs=genau_project_dirs,
        )
        launched.pids.append(origenerator_pid)
        logger.info("Origenerator launched from %s (pid %d)", origenerator_dir, origenerator_pid)

    # The satellite side's resumed mode: the core session just wrote the
    # opening state to the shared INI (see session_resume), and a session that
    # opens in origenerator mode needs its hosted window handled by the same
    # startup choreography as everyone else — not popped up after the reveal.
    from .shared_state import read_shared_state, shared_state_path
    _shared = read_shared_state(shared_state_path(state_dir))
    satellites_mode = _shared.satellites_mode if _shared is not None else "player"

    # A session that OPENS in origenerator mode gets the same OPEN_SHOWS the
    # switch into it sends: the mode means both regions playing the library of
    # their own shape, and a resumed session that skipped this came up on two
    # black rectangles under a mode that said otherwise.  Written now rather
    # than after the app is up -- it drains its command file on its first tick,
    # which is after its window exists, so an early write is read at exactly
    # the right moment and needs no waiting on.
    if origenerator_pid and satellites_mode == "origenerator":
        append_command(Path(m["commands"]["origenerator_cmd_file"]), "OPEN_SHOWS")

    # --- Phase 2: Position windows (layout computed up front) ---
    skip_activate = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    role_hwnds: dict[str, int] = {}

    if not hide_windows:
        # --- Normal mode: position immediately ---
        # No progress reporting on this path: it is the integration one, and the
        # loading screen (with the reporter that drives it) belongs to the other.
        portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
        _move_window_to(portrait_hwnd, plan.portrait, "portrait satellite", activate=not skip_activate)
        _move_window_to(landscape_hwnd, plan.landscape, "landscape satellite", activate=not skip_activate)
        logger.info("Core windows positioned")

        role_hwnds = _apply_startup_window_state(
            portrait_hwnd=portrait_hwnd,
            landscape_hwnd=landscape_hwnd,
            genau_hwnd=wait_for_window_by_title("Genau", timeout_s=WINDOW_RESOLVE_TIMEOUT_S),
            nau_hwnd=wait_for_window_by_title("Nau", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
            mode=main_mode,
        )
        logger.info("Startup window state applied")

    # --- Phase 2.5: Launch Random Favs Browser ---
    progress.advance("browser")
    rfb_hwnd = _maybe_launch_random_favs_browser(m, plan)
    launched.rfb_hwnd = rfb_hwnd

    # --- Phase 3: Launch UI companions ---
    progress.advance("companions")
    time.sleep(1.2)

    dashboard_enabled = m["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"}
    ui_result_file = _build_unique_result_path(state_dir, "ui_companions")
    launch_ui_companions(
        python_exe=m["executables"]["python_exe"],
        dashboard_module=m["modules"]["dashboard_module"],
        dashboard_enabled=dashboard_enabled,
        # The HUD rides the dashboard's enable gate so integration's
        # FUN_TIME_DISABLE_DASHBOARD keeps both always-on-top overlays off.
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
        audio_module=m["modules"]["audio_module"],
        config_path=m["runtime"]["config_path"],
        audio_folder=m["media"]["genau_audio"],
        result_file=str(ui_result_file),
    )
    ui_pids = _read_result_pids(ui_result_file)
    launched.pids.extend([ui_pids["dashboard_pid"], ui_pids["audio_pid"]])

    # --- Phase 4 (loading screen only): batch-position everything at once ---
    if hide_windows:
        # Named for the wait it actually is: the players open their own windows,
        # and until they have there is nothing here to position.
        progress.advance("players")

        # The satellites launched playing (their paused flag is unset) and own
        # their playlists, so there is nothing to start here — just resolve and
        # position each behind the loading overlay.
        portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()

        # Nau is the third player, and by now the only one still loading: its
        # window has been up since half a second after launch with its own
        # loading screen painted into it.  Hold the overlay over that, so the
        # session is revealed on a video rather than on Nau's progress bar.  A
        # Nau that never gets there does not get to keep the desktop, though —
        # the reveal goes ahead, and says why.
        if not _wait_for_nau_loaded(nau_status_file, progress):
            logger.warning(
                "Nau reported no video within %.0fs; revealing over whatever it "
                "still has on screen", NAU_LOAD_TIMEOUT_S,
            )

        # And for the two satellites, for the same reason: their windows exist
        # within a second of launch and stay BLACK until mpv has opened the
        # first clip.  Revealing on the windows alone lifts the curtain on two
        # black rectangles that fill in a few seconds later, which is what "the
        # windows are not ready when the loading screen goes away" looks like.
        if not _wait_for_players_drawing(
            (m["commands"]["portrait_status_file"],
             m["commands"]["landscape_status_file"]),
            progress,
        ):
            logger.warning(
                "A satellite reported no frames within %.0fs; revealing anyway",
                SATELLITE_PLAY_TIMEOUT_S,
            )

        # A session opening in origenerator mode holds the overlay for the
        # hosted app's window too, and restores it behind the curtain — the
        # whole point of the loading screen is that the room is set up before
        # it is seen, and this window used to pop up seconds after the
        # reveal.  Restoring is overlay-safe (no promotion); the band comes
        # from the post-overlay pass.  A boot that outruns the wait does not
        # keep the desktop: the reveal goes ahead and the dispatch loop's
        # converger adopts the window when it finally appears.
        origenerator_hwnd = 0
        if origenerator_pid and satellites_mode == "origenerator":
            origenerator_hwnd = _wait_for_origenerator_window(origenerator_pid)
            if origenerator_hwnd:
                restore_window(origenerator_hwnd, activate=False)
            else:
                logger.warning(
                    "Origenerator window not up within %.0fs; revealing without "
                    "it — the converger adopts it when it appears",
                    ORIGENERATOR_BOOT_TIMEOUT_S,
                )

        progress.advance("windows")
        _move_window_to(portrait_hwnd, plan.portrait, "portrait satellite", activate=False)
        _move_window_to(landscape_hwnd, plan.landscape, "landscape satellite", activate=False)
        logger.info("Core windows positioned (deferred reveal)")

        # Resolve every managed window and park the idle slot-mate.  The topmost
        # bands are deliberately NOT applied here: the overlay is topmost, and
        # HWND_TOPMOST inserts above it, so each promotion would flash its window
        # over the overlay.  _fix_post_loading_windows applies them once the
        # overlay process has exited.  This is still the last moment the dashboard
        # is resolvable, so its handle is captured now — and it is hidden (SW_HIDE)
        # behind the loading overlay, so its lookup must include hidden windows.
        dash_hwnd = (
            wait_for_window_by_title(
                "Fun Time", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True, include_hidden=True,
            )
            if ui_pids["dashboard_pid"]
            else 0
        )

        role_hwnds = _startup_role_hwnds(
            rfb_hwnd=rfb_hwnd,
            portrait_hwnd=portrait_hwnd,
            landscape_hwnd=landscape_hwnd,
            genau_hwnd=wait_for_window_by_title("Genau", timeout_s=WINDOW_RESOLVE_TIMEOUT_S),
            nau_hwnd=wait_for_window_by_title("Nau", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
            dashboard_hwnd=dash_hwnd,
            origenerator_hwnd=origenerator_hwnd,
        )
        _apply_main_slot_visibility(role_hwnds["nau"], role_hwnds["genau"], main_mode)
        logger.info("Startup windows resolved and parked (bands deferred past the overlay)")

        progress.advance("finalizing")

    # The reveal: startup held every player, and this releases the ones the
    # session's mode actually puts to work — Nau in nau and hybrid, Genau (with
    # its audio) in genau and hybrid, and the idle slot-mate not at all, so
    # nothing plays into a minimized window or drives the OSR2 unasked.  This
    # runs in both paths — the loading-screen (hide_windows) path reveals
    # everything at once, and the no-loading-screen path (integration) has
    # nothing to hide behind but must still start its players.
    write_flag_file(m["commands"]["nau_paused_file"], not nau_displays(main_mode))
    for key in ("genau_paused_file", "audio_paused_file"):
        write_flag_file(m["commands"][key], not genau_active(main_mode))
    # Genau's stroke rides its command channel rather than that flag (see
    # seed_startup_states, which holds it there), so the mode where it drives
    # outright has to be told here or it never starts.  Only genau mode: in hybrid
    # the dispatch loop's arbiter picks between Genau and the funscript on its
    # first tick, and a RESUME here would start Genau against a funscript that is
    # about to take the device — the same reason leaving OmniPause resumes Genau
    # in genau mode alone.
    if main_mode == "genau":
        append_command(Path(m["commands"]["genau_cmd_file"]), "RESUME")

    return StartupResult(
        nau_pid=nau_pid,
        portrait_pid=portrait_pid,
        landscape_pid=landscape_pid,
        dashboard_pid=ui_pids["dashboard_pid"],
        genau_pid=genau_pid,
        audio_pid=ui_pids["audio_pid"],
        layout_plan=plan,
        origenerator_pid=origenerator_pid,
        main_mode=main_mode,
        satellites_mode=satellites_mode,
        role_hwnds=role_hwnds,
        rfb_hwnd=rfb_hwnd,
    )


# How long a session resumed into origenerator mode holds the overlay for the
# hosted app's window.  Its boot runs ComfyUI and the library passes, so it is
# the slowest child by far; bounded so a hung boot cannot wedge startup — the
# reveal proceeds and the dispatch loop's converger adopts the window later.
ORIGENERATOR_BOOT_TIMEOUT_S = 60.0


def _wait_for_origenerator_window(pid: int,
                                  timeout_s: float = ORIGENERATOR_BOOT_TIMEOUT_S) -> int:
    """The hosted app's main window, polled until its slow boot shows one —
    parked (minimized) included — or 0 at the ceiling."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hwnd = find_window_for_process(pid, ORIGENERATOR_ROLE_TITLES["origenerator"])
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return 0


def _layout_config_from_manifest(m: configparser.ConfigParser) -> LayoutConfig:
    return LayoutConfig(
        primary_monitor=int(m["layout"]["primary_monitor"]),
        secondary_monitor=int(m["layout"]["secondary_monitor"]),
        main_top_ratio=float(m["layout"]["main_top_ratio"]),
        landscape_width_ratio=float(m["layout"]["landscape_width_ratio"]),
    )


def _move_window_to(hwnd: int, rect: WindowRect, label: str, *, activate: bool = True) -> None:
    """Move an already-resolved window to *rect* (a no-op warning if unresolved)."""
    if hwnd:
        move_window(hwnd, rect.x, rect.y, rect.width, rect.height, activate=activate)
        logger.info("Positioned %s (hwnd=%d) at %d,%d %dx%d",
                     label, hwnd, rect.x, rect.y, rect.width, rect.height)
    else:
        logger.warning("Could not find window for %s", label)


def _resolve_satellite_hwnds() -> tuple[int, int]:
    """The portrait and landscape native-satellite windows, as (portrait, landscape).

    Each side is resolved by its DISTINCT window caption ("Portrait AI Player" vs
    "Landscape AI Player"), so the lookup can never assign one side's window to the
    other — a shared caption could, and that was the portrait/landscape visual swap.

    Deliberately NOT by pid.  The pid we launch with is the venv's
    ``Scripts\\pythonw.exe``, a launcher that spawns the base interpreter as a
    child, and the child is what owns the window — so a pid poll here can only
    ever run out its timeout.  Two of them (plus Nau's) were 25 seconds of a
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
# rectangles.  Bounded like Nau's: a player that never gets there does not get
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

    Also a cancellation checkpoint, per poll, like the Nau wait it sits beside:
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


def _wait_for_nau_loaded(
    status_file: Path,
    progress: ProgressReporter,
    timeout_s: float = NAU_LOAD_TIMEOUT_S,
) -> bool:
    """Wait until Nau has a video on screen, returning whether it got there.

    Nau's caption is NOT this signal.  Nau opens its window before reading its
    library and paints its own loading screen into it while it does — so the
    window exists within half a second of launch, however long the library walk
    then runs.  Waiting on the caption alone brings the overlay down over that
    loading screen, which is the one place it must never be seen: standalone Nau
    owns its wait, and inside Fun Time, Fun Time owns it.

    Nau's status file is the signal, because Nau writes it only from its playback
    loop.  The stale one is dropped at launch, so a file naming a video is this
    session's Nau saying it is up.  Reading the *video* rather than merely the
    file's existence also survives a read that catches the first write half-done.

    Also a cancellation checkpoint, for the same reason ``advance`` is one — but
    checked per poll rather than once, because this is the one stretch of startup
    that can run for tens of seconds, and the overlay covering it offers Esc.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if progress.cancelled:
            raise StartupCancelled()
        if read_nau_status(status_file).video:
            return True
        time.sleep(0.1)
    return False


def resolve_shortcut(shortcut_path: str) -> tuple[str, str, str]:
    """Resolve a Windows .lnk shortcut, returning (target, work_dir, args).

    Uses the COM IShellLink interface via ctypes.
    """
    try:
        import win32com.client  # type: ignore[import-untyped]
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortcut(shortcut_path)
        return link.TargetPath, link.WorkingDirectory, link.Arguments
    except Exception:
        pass

    # Fallback: use PowerShell
    try:
        ps_script = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}'); "
            f"Write-Output $s.TargetPath; Write-Output $s.WorkingDirectory; Write-Output $s.Arguments"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, check=False,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 3:
            return lines[0], lines[1], lines[2]
        if len(lines) >= 1:
            return lines[0], lines[1] if len(lines) > 1 else "", ""
    except Exception:
        pass

    return "", "", ""


def _maybe_launch_random_favs_browser(
    m: configparser.ConfigParser,
    plan: WindowLayoutPlan,
) -> int:
    """Launch the Random Favs Browser if enabled and position it.

    Returns the browser window handle (0 if not launched).  The handle is
    needed so the dispatch loop can include RFB in omnipause topmost management.
    """
    if m["random_favs_browser"]["enabled"] != "1":
        return 0

    shortcut_path = m["random_favs_browser"]["shortcut_path"]
    manifest_file = m["random_favs_browser"]["manifest_file"]

    target, work_dir, args = resolve_shortcut(shortcut_path)
    if not target:
        logger.warning("Random Favs Browser skipped: could not resolve shortcut %s", shortcut_path)
        return 0

    # Take a Chrome window snapshot before launch
    before_hwnds = _get_chrome_window_hwnds()

    result = launch_random_favs_browser(
        manifest_file,
        shortcut_target=target,
        shortcut_work_dir=work_dir,
        shortcut_args=args,
    )
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
    no_activate = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    move_window(new_hwnd, rect.x, rect.y, rect.width, rect.height, activate=not no_activate)

    # The RFB's static topmost flag is applied by Phase 4's
    # _apply_startup_window_state; nothing window-related to do here.

    logger.info("Random Favs Browser positioned")
    return new_hwnd


def _get_chrome_window_hwnds() -> set[int]:
    """Get the set of visible Chrome window handles."""
    hwnds: set[int] = set()

    _user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        # Check process name via PID
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Check title is non-empty
        title_len = _user32.GetWindowTextLengthW(hwnd)
        if title_len > 0:
            # Get the window class name to identify Chrome
            class_name = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, class_name, 256)
            if "Chrome" in class_name.value:
                hwnds.add(hwnd)
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return hwnds


def _wait_for_new_chrome_window(before: set[int], timeout_ms: int = 8000) -> int:
    """Wait for a new Chrome window that wasn't in the 'before' set."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = _get_chrome_window_hwnds()
        new_windows = current - before
        if new_windows:
            return next(iter(new_windows))
        time.sleep(0.2)
    return 0
