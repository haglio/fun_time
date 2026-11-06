from __future__ import annotations

import configparser
import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from funestra.modes import MainMode
from funestra.play_points import play_points_filename

from satellite.contract import SatelliteChannels

from . import preview_marker
from .fun_time_funestras import FunTimeFunestra
from .funestra_deaths import (
    FunestraDied,
    LaunchedFunestra,
    logs_as_they_stand,
    raise_if_a_funestra_died,
)
from .funestra_status import (
    read_genau_status,
    read_main_funestra_status,
)
from .hosted_origenerator import HostedApp, bring_up_the_hosted_app
from .manifest import LaunchManifest, RandomFavsBrowserSettings
from .mode_plan import STARTUP_MAIN_MODE, kino_shows
from .modes import PLAYLIST_LANDSCAPE, PLAYLIST_PORTRAIT, build_playlist_file_path
from .origenerator_switches import RfbMode
from .overlay_progress import NullProgress, ProgressReporter, StartupCancelled
from .runtime_flow import write_flag_file
from .satellite_control import read_satellite_status
from .satellite_launch import SatelliteLaunch
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .shared_state import BridgeState, read_shared_state, shared_state_path
from .shortcuts import resolve_shortcut
from .win32 import (
    find_windows_by_class,
    move_window,
    set_always_on_top,
    wait_for_window_by_title,
)
from .window_layout import (
    ScreenLayout,
    WindowLayoutPlan,
    WindowRect,
    compute_main_media_rect,
    screen_layout,
)
from .window_names import MANAGED_WINDOWS, window_is_topmost
from .windows_bridge_random_favs_browser import (
    CHROME_WINDOW_CLASS,
    launch_random_favs_browser,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
    GenauStart,
    launch_main_funestra,
    launch_ui_companions,
    start_core_session,
)

logger = logging.getLogger(__name__)

WINDOW_RESOLVE_TIMEOUT_S = 15.0

MAIN_FUNESTRA_LOAD_TIMEOUT_S = 40.0


@dataclass(frozen=True)
class StartupResult:
    main_funestra_pid: int
    portrait_pid: int
    landscape_pid: int
    dashboard_pid: int
    audio_pid: int
    origenerator_pid: int = 0
    origenerator_taken_over: bool = False
    origenerator_already_open: bool = False
    origenerator_slideshow_on: tuple[FunTimeFunestra, ...] = ()
    main_mode: MainMode = STARTUP_MAIN_MODE
    rfb_hwnd: int = 0
    hwnds_by_window: dict[str, int] = field(default_factory=dict)


def _read_result_pids(result_file: str | Path) -> dict[str, int]:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(result_file), encoding="utf-8")
    return {key: int(value) for key, value in parser["result"].items()}


def _build_unique_result_path(state_dir: Path, prefix: str) -> Path:
    return state_dir / f"{prefix}_{int(time.monotonic() * 1000)}.ini"


@dataclass(frozen=True)
class StartupWindows:
    portrait_hwnd: int
    landscape_hwnd: int
    main_funestra_hwnd: int
    dashboard_hwnd: int = 0
    rfb_hwnd: int = 0

    def by_name(self) -> dict[str, int]:
        return {
            "portrait": self.portrait_hwnd,
            "landscape": self.landscape_hwnd,
            "main_funestra": self.main_funestra_hwnd,
            "dashboard": self.dashboard_hwnd,
            "rfb": self.rfb_hwnd,
        }


def apply_topmost_bands(hwnds_by_window: dict[str, int], *, beneath: int = 0,
                        rfb_mode: RfbMode = RfbMode.RFB) -> None:
    for window_name in MANAGED_WINDOWS:
        hwnd = hwnds_by_window.get(window_name, 0)
        if hwnd:
            set_always_on_top(hwnd, window_is_topmost(window_name, rfb_mode), under=beneath)


def apply_startup_window_state(
    windows: StartupWindows,
    *,
    beneath: int = 0,
    rfb_mode: RfbMode = RfbMode.RFB,
) -> dict[str, int]:
    hwnds_by_window = windows.by_name()
    apply_topmost_bands(hwnds_by_window, beneath=beneath, rfb_mode=rfb_mode)
    return hwnds_by_window


@dataclass
class _LaunchedChildren:
    pids: list[int] = field(default_factory=list)
    funestras: list[LaunchedFunestra] = field(default_factory=list)
    rfb_hwnd: int = 0
    dashboard_pid: int = 0
    origenerator_taken_over: bool = False
    origenerator_already_open: bool = False
    origenerator_slideshow_on: tuple[FunTimeFunestra, ...] = ()

    def hosts(self, app: HostedApp) -> int:
        self.origenerator_already_open = app.already_open
        self.origenerator_taken_over = app.taken_over
        self.origenerator_slideshow_on = app.slideshow_on
        if not app.taken_over:
            self.pids.append(app.pid)
        return app.pid


def release_the_funestras(m: LaunchManifest, main_mode: MainMode) -> None:
    write_flag_file(m.commands.main_funestra_paused_file, not kino_shows(main_mode))
    for flag_file in (m.commands.genau_paused_file, m.commands.audio_paused_file):
        write_flag_file(flag_file, False)


def run_startup_sequence(
    *,
    manifest_path: str | Path,
    state_dir: str | Path,
    progress: ProgressReporter | None = None,
    hide_windows: bool = False,
    env: SessionEnvironment = ORDINARY_SESSION,
) -> StartupResult:
    if progress is None:
        progress = NullProgress()

    launched = _LaunchedChildren()
    try:
        return _run_startup_phases(
            _StartupRequest(Path(manifest_path), Path(state_dir), hide_windows, env),
            progress, launched)
    except (StartupCancelled, FunestraDied) as stopped:
        stopped.launched_pids = launched.pids
        stopped.rfb_hwnd = launched.rfb_hwnd
        stopped.origenerator_taken_over = launched.origenerator_taken_over
        raise


@dataclass(frozen=True)
class _StartupRequest:
    manifest_path: Path
    state_dir: Path
    hide_windows: bool
    env: SessionEnvironment


@dataclass(frozen=True)
class _CoreSession:
    main_mode: MainMode
    portrait_pid: int
    landscape_pid: int
    main_funestra_pid: int
    origenerator_pid: int
    origenerator_taken_over: bool
    origenerator_already_open: bool
    origenerator_slideshow_on: tuple[FunTimeFunestra, ...]
    main_funestra_status_file: Path


def _plan_the_layout(m: LaunchManifest) -> ScreenLayout:
    return screen_layout(m.layout, rfb_mode_selector=m.runtime.hosts_origenerator)


def _launch_the_satellites(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    state_dir: Path,
    launched: _LaunchedChildren,
) -> tuple[str, int, int]:
    core_result_file = _build_unique_result_path(state_dir, "core_session")

    def _launch(funestra: FunTimeFunestra, playlist: str, rect):
        satellite = funestra.label
        channel = m.satellite(funestra)
        return SatelliteLaunch(
            funestra=funestra,
            sources=channel.sources,
            channels=replace(
                SatelliteChannels.from_channel(
                    channel, m.commands, satellite,
                    play_points=state_dir / play_points_filename(satellite)),
                playlist=build_playlist_file_path(state_dir, playlist)),
            log_file=state_dir / f"{satellite}_satellite.log",
            rect=rect,
        )

    portrait_launch = _launch(FunTimeFunestra.PORTRAIT, PLAYLIST_PORTRAIT, plan.portrait)
    landscape_launch = _launch(FunTimeFunestra.LANDSCAPE, PLAYLIST_LANDSCAPE, plan.landscape)
    portrait_logs = logs_as_they_stand(portrait_launch.log_file)
    landscape_logs = logs_as_they_stand(landscape_launch.log_file)
    main_mode = start_core_session(
        m,
        portrait=portrait_launch,
        landscape=landscape_launch,
        state_dir=state_dir,
        result_file=str(core_result_file),
    )
    core_pids = _read_result_pids(core_result_file)
    portrait_pid = core_pids["portrait_pid"]
    landscape_pid = core_pids["landscape_pid"]
    launched.pids.extend([portrait_pid, landscape_pid])
    launched.funestras.extend([
        LaunchedFunestra("the Portrait Funestra", portrait_pid, portrait_logs),
        LaunchedFunestra("the Landscape Funestra", landscape_pid, landscape_logs),
    ])
    logger.info(
        "Core session launched: portrait=%d landscape=%d",
        portrait_pid, landscape_pid,
    )
    return main_mode, portrait_pid, landscape_pid


def _launch_the_main_funestra(
    m: LaunchManifest,
    *,
    layout: ScreenLayout,
    state_dir: Path,
    launched: _LaunchedChildren,
) -> tuple[int, Path]:
    main_media_rect = compute_main_media_rect(
        secondary_monitor=layout.secondary_monitor, layout_config=layout.config,
    )
    genau_flick = read_genau_status(Path(m.commands.genau_status_file)).flick
    state = read_shared_state(shared_state_path(state_dir)) or BridgeState()
    main_funestra_status_file = Path(m.commands.main_funestra_status_file)
    main_funestra_status_file.unlink(missing_ok=True)
    main_funestra_log = state_dir / "main_funestra.log"
    main_funestra_logs = logs_as_they_stand(main_funestra_log)
    main_funestra_pid = launch_main_funestra(
        m,
        state_dir=state_dir,
        log_file=main_funestra_log,
        rect=main_media_rect,
        genau=GenauStart(
            start_flick=genau_flick,
            latest=state.genau_latest,
            f_mode=state.genau_favorites_filter,
            orientations=(state.genau_plays_portrait, state.genau_plays_landscape),
        ),
    )
    launched.pids.append(main_funestra_pid)
    launched.funestras.append(LaunchedFunestra("the Main Funestra", main_funestra_pid, main_funestra_logs))
    return main_funestra_pid, main_funestra_status_file


def _launch_the_hosted_origenerator(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    sibling_checkouts: str,
    launched: _LaunchedChildren,
) -> int:
    app = bring_up_the_hosted_app(m, plan=plan, sibling_checkouts=sibling_checkouts)
    return 0 if app is None else launched.hosts(app)


def _launch_core_media(
    m: LaunchManifest,
    *,
    layout: ScreenLayout,
    state_dir: Path,
    launched: _LaunchedChildren,
) -> _CoreSession:
    origenerator_pid = _launch_the_hosted_origenerator(
        m, plan=layout.plan, sibling_checkouts=m.runtime.sibling_checkouts, launched=launched)
    main_mode, portrait_pid, landscape_pid = _launch_the_satellites(
        m, plan=layout.plan, state_dir=state_dir, launched=launched)
    main_funestra_pid, main_funestra_status_file = _launch_the_main_funestra(
        m, layout=layout, state_dir=state_dir, launched=launched)

    return _CoreSession(
        main_mode=main_mode,
        portrait_pid=portrait_pid,
        landscape_pid=landscape_pid,
        main_funestra_pid=main_funestra_pid,
        origenerator_pid=origenerator_pid,
        origenerator_taken_over=launched.origenerator_taken_over,
        origenerator_already_open=launched.origenerator_already_open,
        origenerator_slideshow_on=launched.origenerator_slideshow_on,
        main_funestra_status_file=main_funestra_status_file,
    )


_COMPANION_LAUNCH_DELAY_S = 1.2


def _position_windows_now(plan: WindowLayoutPlan, *, env: SessionEnvironment) -> dict[str, int]:
    activate = not env.integration
    portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
    _move_window_to(portrait_hwnd, plan.portrait, "portrait satellite", activate=activate)
    _move_window_to(landscape_hwnd, plan.landscape, "landscape satellite", activate=activate)
    logger.info("Core windows positioned")

    hwnds_by_window = apply_startup_window_state(StartupWindows(
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        main_funestra_hwnd=wait_for_window_by_title("Main Funestra", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
    ))
    logger.info("Startup window state applied")
    return hwnds_by_window


def _launch_the_companions(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    state_dir: Path,
    manifest_path: Path,
    launched: _LaunchedChildren,
) -> dict[str, int]:
    time.sleep(_COMPANION_LAUNCH_DELAY_S)
    ui_result_file = _build_unique_result_path(state_dir, "ui_companions")
    launch_ui_companions(
        m,
        plan=plan,
        manifest_path=manifest_path,
        dashboard_log_file=state_dir / "dashboard.log",
        result_file=str(ui_result_file),
    )
    ui_pids = _read_result_pids(ui_result_file)
    launched.pids.extend([ui_pids["dashboard_pid"], ui_pids["audio_pid"]])
    launched.dashboard_pid = ui_pids["dashboard_pid"]
    return ui_pids


def _wait_for_the_app_to_be_drawing(
    m: LaunchManifest,
    *,
    main_funestra_status_file: Path,
    progress: ProgressReporter,
    funestras: list[LaunchedFunestra] = (),
) -> None:
    if not _wait_for_main_funestra_loaded(main_funestra_status_file, progress, funestras=funestras):
        logger.warning(
            "the Main Funestra reported no video within %.0fs; revealing over whatever it "
            "still has on screen", MAIN_FUNESTRA_LOAD_TIMEOUT_S,
        )
    if not _wait_for_funestras_drawing(
        tuple(m.satellite(funestra).status_file for funestra in FunTimeFunestra.SATELLITES),
        progress,
        funestras=funestras,
    ):
        logger.warning(
            "A satellite reported no frames within %.0fs; revealing anyway",
            SATELLITE_PLAY_TIMEOUT_S,
        )


def _place_the_windows_under_the_loading_screen(
    *,
    plan: WindowLayoutPlan,
    portrait_hwnd: int,
    landscape_hwnd: int,
    rfb_hwnd: int,
    dashboard_pid: int,
) -> dict[str, int]:
    _move_window_to(portrait_hwnd, plan.portrait, "portrait satellite", activate=False)
    _move_window_to(landscape_hwnd, plan.landscape, "landscape satellite", activate=False)
    logger.info("Core windows positioned (deferred reveal)")

    dash_hwnd = (
        wait_for_window_by_title(
            preview_marker.app_title(preview_marker.shown_as()),
            timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True, include_hidden=True,
        )
        if dashboard_pid
        else 0
    )
    hwnds_by_window = StartupWindows(
        rfb_hwnd=rfb_hwnd,
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        main_funestra_hwnd=wait_for_window_by_title("Main Funestra", timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
        dashboard_hwnd=dash_hwnd,
    ).by_name()
    logger.info("Startup windows resolved (bands deferred past the overlay)")
    return hwnds_by_window


def _settle_the_app_under_the_loading_screen(
    m: LaunchManifest,
    *,
    core: _CoreSession,
    plan: WindowLayoutPlan,
    launched: _LaunchedChildren,
    progress: ProgressReporter,
) -> dict[str, int]:
    progress.advance("funestras")
    portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
    _wait_for_the_app_to_be_drawing(
        m, main_funestra_status_file=core.main_funestra_status_file, progress=progress,
        funestras=launched.funestras)

    progress.advance("windows")
    return _place_the_windows_under_the_loading_screen(
        plan=plan,
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        rfb_hwnd=launched.rfb_hwnd,
        dashboard_pid=launched.dashboard_pid,
    )


def _run_startup_phases(
    request: _StartupRequest,
    progress: ProgressReporter,
    launched: _LaunchedChildren,
) -> StartupResult:
    manifest_path, state_dir = request.manifest_path, request.state_dir
    hide_windows, env = request.hide_windows, request.env
    state_dir.mkdir(parents=True, exist_ok=True)
    m = LaunchManifest.read(manifest_path)
    layout = _plan_the_layout(m)
    plan = layout.plan

    progress.advance("services")
    core = _launch_core_media(m, layout=layout, state_dir=state_dir, launched=launched)

    raise_if_a_funestra_died(launched.funestras)

    hwnds_by_window: dict[str, int] = {}
    if not hide_windows:
        hwnds_by_window = _position_windows_now(plan, env=env)

    progress.advance("browser")
    rfb_hwnd = _maybe_launch_random_favs_browser(m.random_favs_browser, plan, env=env)
    launched.rfb_hwnd = rfb_hwnd

    progress.advance("companions")
    ui_pids = _launch_the_companions(
        m, plan=plan, state_dir=state_dir, manifest_path=manifest_path, launched=launched)

    raise_if_a_funestra_died(launched.funestras)

    if hide_windows:
        hwnds_by_window = _settle_the_app_under_the_loading_screen(
            m, core=core, plan=plan, launched=launched, progress=progress)

    raise_if_a_funestra_died(launched.funestras)

    if not hide_windows:
        release_the_funestras(m, core.main_mode)

    return StartupResult(
        main_funestra_pid=core.main_funestra_pid,
        portrait_pid=core.portrait_pid,
        landscape_pid=core.landscape_pid,
        dashboard_pid=ui_pids["dashboard_pid"],
        audio_pid=ui_pids["audio_pid"],
        origenerator_pid=core.origenerator_pid,
        origenerator_taken_over=core.origenerator_taken_over,
        origenerator_already_open=core.origenerator_already_open,
        origenerator_slideshow_on=core.origenerator_slideshow_on,
        main_mode=core.main_mode,
        hwnds_by_window=hwnds_by_window,
        rfb_hwnd=rfb_hwnd,
    )


def _move_window_to(hwnd: int, rect: WindowRect, label: str, *, activate: bool = True) -> None:
    if hwnd:
        move_window(hwnd, rect, activate=activate)
        logger.info("Positioned %s (hwnd=%d) at %d,%d %dx%d",
                     label, hwnd, rect.x, rect.y, rect.width, rect.height)
    else:
        logger.warning("Could not find window for %s", label)


def _resolve_satellite_hwnds() -> tuple[int, int]:
    return (
        wait_for_window_by_title(SATELLITE_PORTRAIT_TITLE, timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
        wait_for_window_by_title(SATELLITE_LANDSCAPE_TITLE, timeout_s=WINDOW_RESOLVE_TIMEOUT_S, exact=True),
    )


SATELLITE_PLAY_TIMEOUT_S = 25.0
_PLAY_POLL_S = 0.1


def _wait_for_funestras_drawing(status_files, progress: ProgressReporter,
                              timeout_s: float = SATELLITE_PLAY_TIMEOUT_S,
                              *, funestras: list[LaunchedFunestra] = ()) -> bool:
    files = [Path(path) for path in status_files if path]
    if not files:
        return True
    for _ in range(max(1, int(timeout_s / _PLAY_POLL_S))):
        if progress.cancelled:
            raise StartupCancelled()
        raise_if_a_funestra_died(funestras)
        if all(read_satellite_status(path).position_ms > 0 for path in files):
            return True
        time.sleep(_PLAY_POLL_S)
    return False


def _wait_for_main_funestra_loaded(
    status_file: Path,
    progress: ProgressReporter,
    timeout_s: float = MAIN_FUNESTRA_LOAD_TIMEOUT_S,
    *,
    funestras: list[LaunchedFunestra] = (),
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if progress.cancelled:
            raise StartupCancelled()
        raise_if_a_funestra_died(funestras)
        if read_main_funestra_status(status_file).video:
            return True
        time.sleep(0.1)
    return False


def _maybe_launch_random_favs_browser(
    settings: RandomFavsBrowserSettings,
    plan: WindowLayoutPlan,
    *,
    env: SessionEnvironment,
) -> int:
    if not settings.enabled:
        return 0

    shortcut_path = settings.shortcut_path
    manifest_file = settings.manifest_file

    shortcut = resolve_shortcut(shortcut_path)
    if not shortcut.target:
        logger.warning("Random Favs Browser skipped: could not resolve shortcut %s", shortcut_path)
        return 0

    before_hwnds = find_windows_by_class(CHROME_WINDOW_CLASS)

    result = launch_random_favs_browser(manifest_file, shortcut=shortcut)
    if not result.should_launch:
        logger.info("Random Favs Browser skipped: launch plan was empty")
        return 0

    new_hwnd = _wait_for_new_chrome_window(before_hwnds, timeout_ms=8000)
    if not new_hwnd:
        logger.warning("Random Favs Browser skipped: no new Chrome window appeared")
        return 0

    rect = plan.random_favs_browser
    move_window(new_hwnd, rect, activate=not env.integration)


    logger.info("Random Favs Browser positioned")
    return new_hwnd


def _wait_for_new_chrome_window(before: set[int], timeout_ms: int = 8000) -> int:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        appeared = find_windows_by_class(CHROME_WINDOW_CLASS) - before
        if appeared:
            return next(iter(appeared))
        time.sleep(0.2)
    return 0
