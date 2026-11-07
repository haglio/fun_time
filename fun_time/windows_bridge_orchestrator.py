from __future__ import annotations

import configparser
import contextlib
import datetime
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from app_support.subprocess_utils import tie_to_this_process
from app_support.threading_utils import wait_until
from app_support.win32 import process_started
from funestra.file_channel import append_command
from voice_core.listener import why_unavailable
from voice_core.whisper_reader import WhisperReader

from . import preview_marker
from .append_only import append_line
from .checkout_overrides import sibling_checkouts_kwargs
from .child_launch import no_child_log, no_console_window
from .config import load_config
from .crown import majority_now
from .dashboard_actions import LIBRARY_OPEN_FILENAME, REFERENCE_OPEN_FILENAME
from .desktop_session import (
    NO_FAREWELL,
    HostedAppFarewell,
    HotkeyScript,
    HotkeyScriptLaunch,
    LaunchedSoFar,
    RunningSession,
    SessionLaunch,
    StartupEnd,
    hold_the_osr2,
    release_of,
    silence_the_funestras,
)
from .event_log import THE_LISTENERS_LOGGERS
from .fun_time_funestras import FunTimeFunestra
from .fun_time_windows import ChildPids, FunTimeWindows, find_origenerators_window
from .funestra_deaths import (
    FunestraDied,
    funestra_died_message,
    part_closed_message,
    the_part_that_closed,
)
from .hosted_contract import ASKING_BUDGET_S, NO_HOSTED_VOCABULARY, HostedVocabulary
from .hosted_origenerator import what_it_answers_by_voice_in
from .hud_transport import HudPublisher
from .library_handles import build_library_handles, genau_flick_sources
from .lock_hud import prime_group_indexes
from .loopback_inbox import PRESS_PORT_FILENAME
from .loopback_server import ThreadingHTTPServer, serve_loopback
from .manifest import CommandFiles, LaunchManifest
from .modes import collect_video_files
from .origenerator_switches import CLOSE_SLIDESHOWS, RfbMode, hosted_satellites
from .osr2_section import DASHBOARD_OSR2_PART, osr2_part_path
from .overlay_progress import (
    ACTIVATING_ORIGENERATOR,
    CANCEL_CLOSING_FUN_TIME,
    CANCEL_FILENAME,
    CANCEL_WORD,
    SHUTDOWN_PHASES,
    SHUTDOWN_PROGRESS_FILENAME,
    STARTING_VOICE_COMMANDS,
    NullProgress,
    PhaseProgress,
    ProgressReporter,
    StartupCancelled,
    ready_file_for,
    what_the_flag_asks,
)
from .process_identity import NAMER
from .process_tree import kill_process_tree
from .raising_the_loading_screen import EXIT_WAIT_S, RaisedLoadingScreen
from .rfb_slideshow import rfb_slideshow_on
from .session_end import session_end_marker_path
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .session_handoff import (
    DESKTOP,
    HandoffTarget,
    crossing_progress_path,
    drop_transition_screen,
    forget_the_kept_origenerator,
    keep_the_origenerator,
    kept_origenerator,
    launch_the_way_back_screen,
    launch_transition_screen,
    pending_handoff,
    release_the_headset,
    request_handoff,
)
from .shared_state import read_shared_state, shared_state_path
from .shortcuts import Shortcut, resolve_shortcut
from .standalone_origenerator import RELEASE
from .state_file_names import take_up_the_retired_state_file_names
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, prewarm_thumbnails
from .unlogged_messages import UNLOGGED_MESSAGE_PORT_FILENAME
from .voice_control import HostedVoice, VoiceController
from .win32 import (
    close_window,
    describe_windows,
    find_window_by_pid,
    hide_window,
    iter_zorder,
    set_always_on_top,
    wait_for_window_by_title,
    windows_obscuring,
)
from .win32_events import PressAndCaretWatch
from .window_layout import SecondaryMonitorRects, screen_layout, secondary_monitor_rects
from .windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    Publishing,
    SessionFiles,
    SessionStart,
    build_bridge_config_from_manifest,
)
from .windows_bridge_sequencer import (
    StartupResult,
    StartupWindows,
    apply_startup_window_state,
    apply_topmost_bands,
    release_the_funestras,
    run_startup_sequence,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

logger = logging.getLogger(__name__)


_CHILD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("funestras", ("main_funestra_pid", "portrait_pid", "landscape_pid")),
    ("companions", ("dashboard_pid", "audio_pid", "origenerator_pid")),
)

_CHILD_PID_KEYS = tuple(key for _, keys in _CHILD_GROUPS for key in keys)


@dataclass(frozen=True)
class ChildProcess:
    # Windows hands a freed PID back out within seconds, so every deferred kill compares creation
    # times first.

    pid: int
    created_at: int


def identify_children(result: StartupResult) -> dict[str, ChildProcess]:
    children: dict[str, ChildProcess] = {}
    for key in _CHILD_PID_KEYS:
        pid = getattr(result, key)
        children[key] = ChildProcess(pid=pid, created_at=process_started(pid) or 0)
    return children


def write_pids_file(path: Path, children: dict[str, ChildProcess]) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["pids"] = {key: str(child.pid) for key, child in children.items()}
    parser["created_at"] = {key: str(child.created_at) for key, child in children.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def _describe_session_end(state_dir: Path, exit_code: int) -> str:
    marker = session_end_marker_path(state_dir)
    try:
        reason = marker.read_text(encoding="utf-8").strip()
    except OSError:
        reason = ""
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not clear %s", marker, exc_info=True)
    if reason:
        return f"asked to end: {reason}"
    if exit_code:
        return "the hotkey script FAILED -- see its own MsgBox/exit path"
    return "UNEXPECTED -- nothing asked it to end; the hotkey script exited on its own"


def kill_recorded_child(child: ChildProcess) -> None:
    if not child.pid:
        return
    created_at = process_started(child.pid)
    if created_at is None:
        logger.info("Not killing PID %d: process already exited", child.pid)
        return
    if created_at != child.created_at:
        logger.warning(
            "Not killing PID %d: Windows recycled it to a process created at %d, "
            "not the child we launched at %d",
            child.pid, created_at, child.created_at,
        )
        return
    told_at = time.monotonic()
    still_running = kill_process_tree(child.pid)
    took_s = time.monotonic() - told_at
    if still_running:
        logger.warning("Gave up waiting for PID %d to end after %.1fs; still running: %s",
                       child.pid, took_s, ", ".join(map(str, still_running)))
    else:
        logger.info("Ended PID %d in %.1fs", child.pid, took_s)


def _close_origenerator_gracefully(child: ChildProcess | None) -> None:
    if child is None or not child.pid:
        return
    hwnd = find_origenerators_window(child.pid)
    if not hwnd:
        return
    close_window(hwnd)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process_started(child.pid) != child.created_at:
            return
        time.sleep(0.2)
    logger.warning("Origenerator did not close within 5s; the kill sweep takes it")


def _park_the_hosted_origenerator(
    state_dir: Path, child: ChildProcess | None, command_file: Path, *, taken_over: bool,
) -> bool:
    if child is None or not child.pid:
        return False
    hwnd = find_origenerators_window(child.pid)
    if not hwnd:
        return False
    append_command(command_file, CLOSE_SLIDESHOWS)
    hide_window(hwnd)
    keep_the_origenerator(state_dir, pid=child.pid, created_at=child.created_at,
                          taken_over=taken_over)
    logger.info("Leaving the hosted Origenerator running (pid=%d)", child.pid)
    return True


def see_the_hosted_app_out(
    state_dir: Path,
    child: ChildProcess | None,
    command_file: Path | None,
    *,
    keep: bool,
    taken_over: bool,
) -> bool:
    if keep and command_file is not None and _park_the_hosted_origenerator(
            state_dir, child, command_file, taken_over=taken_over):
        return True
    forget_the_kept_origenerator(state_dir)
    if taken_over and command_file is not None:
        append_command(command_file, RELEASE)
        return True
    _close_origenerator_gracefully(child)
    return False


def let_go_of_a_kept_origenerator(state_dir: Path, command_file: Path) -> None:
    kept = kept_origenerator(state_dir)
    forget_the_kept_origenerator(state_dir)
    if kept is None:
        return
    if process_started(kept.pid) != kept.created_at:
        return
    if kept.taken_over:
        append_command(command_file, RELEASE)
        return
    child = ChildProcess(pid=kept.pid, created_at=kept.created_at)
    _close_origenerator_gracefully(child)
    kill_recorded_child(child)


def _shutdown_children(
    rfb_hwnd: int,
    children: dict[str, ChildProcess],
    progress: ProgressReporter,
    *,
    state_dir: Path,
    farewell: HostedAppFarewell = NO_FAREWELL,
) -> None:
    progress.advance("browser")
    close_window(rfb_hwnd)
    spared = see_the_hosted_app_out(
        state_dir, children.get("origenerator_pid"), farewell.command_file,
        keep=farewell.keep, taken_over=farewell.taken_over)
    for phase, keys in _CHILD_GROUPS:
        progress.advance(phase)
        with progress.loading_screen_kept_up():
            for key in keys:
                if spared and key == "origenerator_pid":
                    continue
                kill_recorded_child(children[key])


CLOSING_SCREEN_READY_TIMEOUT_S = 5.0


def _wait_for_closing_screen(ready_file: Path, proc: subprocess.Popen, *,
                             now: Callable[[], float] = time.monotonic) -> None:
    try:
        wait_until(lambda: ready_file.exists() or proc.poll() is not None,
                   timeout=CLOSING_SCREEN_READY_TIMEOUT_S, interval=0.05, now=now)
    except TimeoutError:
        logger.warning(
            "Closing screen not ready after %.1fs; closing anyway",
            CLOSING_SCREEN_READY_TIMEOUT_S,
        )
        return
    if not ready_file.exists():
        logger.warning("Closing screen exited before it was ready")


@contextlib.contextmanager
def _closing_screen(
    state_dir: Path, *, sibling_checkouts: str,
    crossing: HandoffTarget | None = None, esc_cancels: str = "",
    now: Callable[[], float] = time.monotonic,
) -> Iterator[ProgressReporter]:
    progress_file = (
        crossing_progress_path(state_dir) if crossing is not None
        else state_dir / SHUTDOWN_PROGRESS_FILENAME
    )
    ready_file = ready_file_for(progress_file)
    ready_file.unlink(missing_ok=True)
    if crossing is not None:
        progress: ProgressReporter = NullProgress()
        proc = launch_transition_screen(state_dir, crossing, sibling_checkouts=sibling_checkouts)
    else:
        progress = PhaseProgress(progress_file, phases=SHUTDOWN_PHASES, hint=esc_cancels)
        progress.advance("controls")
        proc = subprocess.Popen([
            NAMER.named_exe(sys.executable, "ClosingScreen"),
            "-m", "fun_time.closing_screen", str(progress_file),
        ], **no_child_log(), **no_console_window(), **sibling_checkouts_kwargs(sibling_checkouts))
        tie_to_this_process(proc)
    logger.info("Teardown loading screen launched (pid=%d)", proc.pid)
    _wait_for_closing_screen(ready_file, proc, now=now)
    try:
        yield progress
    finally:
        if crossing is None:
            progress.finish()
            try:
                wait_until(lambda: proc.poll() is not None, timeout=EXIT_WAIT_S, now=now)
            except TimeoutError:
                proc.kill()
                logger.warning("Closing screen did not exit, killed")
            progress_file.unlink(missing_ok=True)
            ready_file.unlink(missing_ok=True)
        else:
            logger.info("Leaving the loading screen up for %s", crossing.app_name)


_CANCELLED_EXIT_CODE = 0


def launch_hotkey_script(*, ahk_exe: str | Path, script: str | Path, manifest_path: Path,
                         pids_file: Path, project_dir: Path) -> subprocess.Popen:
    command = [str(ahk_exe), str(script), str(manifest_path), str(pids_file), str(os.getpid())]
    logger.info("Launching AHK hotkey script: %s", " ".join(command))
    return subprocess.Popen(command, cwd=project_dir, **no_child_log(), **no_console_window())


def stop_hotkey_script(proc: subprocess.Popen, ahk_cmd_file: Path) -> None:
    try:
        ahk_cmd_file.write_text("exit", encoding="utf-8")
    except OSError:
        logger.warning("Could not ask the hotkey script to exit", exc_info=True)
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("Hotkey script did not exit after cancel, killed")


def _take_down_the_startup(
    end: StartupEnd,
    launched: LaunchedSoFar,
    launch: SessionLaunch,
    loading_screen: RaisedLoadingScreen,
    hotkeys: HotkeyScript,
) -> SessionOver:
    state_dir = loading_screen.progress_file.parent
    back_to = loading_screen.turns_back_to if (
        end.canceled and what_the_flag_asks(loading_screen.cancel_file) == CANCEL_WORD) else None
    logger.info("%s; tearing down %d launched child(ren)", end.reason, len(launched.pids))
    stop_hotkey_script(hotkeys.proc, hotkeys.cmd_file)
    with loading_screen.progress.loading_screen_kept_up():
        for pid in launched.pids:
            kill_process_tree(pid)
    if launched.release_origenerator_via is not None:
        append_command(launched.release_origenerator_via, RELEASE)
    close_window(launched.rfb_hwnd)
    if back_to is not None:
        logger.info("Going back to %s", back_to.app_name)
        way_back = launch_the_way_back_screen(
            state_dir, sibling_checkouts=launch.manifest.runtime.sibling_checkouts)
        _wait_for_closing_screen(ready_file_for(crossing_progress_path(state_dir)), way_back)
        request_handoff(state_dir, back_to, cancelable=False)
    loading_screen.take_it_down()
    loading_screen.cancel_file.unlink(missing_ok=True)
    if back_to is None:
        drop_transition_screen(state_dir)
        release_the_headset(state_dir)
    return SessionOver(_CANCELLED_EXIT_CODE)


class _AppendOnWriteHandler(logging.Handler):
    def __init__(self, log_path: Path):
        super().__init__()
        self.log_path = log_path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            lines = [f"{ts} {record.getMessage()}"]
            if record.exc_info:
                lines += logging.Formatter().formatException(record.exc_info).splitlines()
            append_line(self.log_path, "".join(f"{line}\r\n" for line in lines))
        except Exception:
            pass


def add_dispatch_file_handler(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = _AppendOnWriteHandler(log_path)
    for name in ("fun_time", *THE_LISTENERS_LOGGERS):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)


HUD_PRIME_TIMEOUT_S = 20.0
POST_LOADING_RESOLVE_TIMEOUT_S = 3.0
SETTLE_PASSES = 8
SETTLE_WAIT_S = 1.5


def _log_window_obstruction(name: str, hwnd: int, *, ignore: int = 0) -> None:
    if not hwnd:
        logger.warning("%s window unresolved after loading; cannot check z-order", name)
        return
    covering = [
        w for w in windows_obscuring(hwnd, iter_zorder())
        if w.hwnd != ignore
    ]
    if covering:
        logger.warning("%s (hwnd=%d) is covered at startup by: %s",
                       name, hwnd, describe_windows(covering))
    else:
        logger.info("%s (hwnd=%d) is frontmost over its rect at startup", name, hwnd)


def _fix_post_loading_windows(result: StartupResult, *, overlay_hwnd: int = 0,
                              rfb_mode: RfbMode = RfbMode.RFB) -> dict[str, int]:
    dash_hwnd = 0
    if result.dashboard_pid:
        dash_hwnd = find_window_by_pid(result.dashboard_pid)
        if not dash_hwnd:
            dash_hwnd = wait_for_window_by_title(
                preview_marker.app_title(preview_marker.shown_as()),
                timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True)

    main_funestra_hwnd = find_window_by_pid(result.main_funestra_pid) or wait_for_window_by_title(
        "Main Funestra", timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True
    )
    portrait_hwnd = find_window_by_pid(result.portrait_pid) or wait_for_window_by_title(
        SATELLITE_PORTRAIT_TITLE, timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True
    )
    landscape_hwnd = find_window_by_pid(result.landscape_pid) or wait_for_window_by_title(
        SATELLITE_LANDSCAPE_TITLE, timeout_s=POST_LOADING_RESOLVE_TIMEOUT_S, exact=True
    )
    hwnds_by_window = apply_startup_window_state(
        StartupWindows(
            rfb_hwnd=result.rfb_hwnd,
            portrait_hwnd=portrait_hwnd,
            landscape_hwnd=landscape_hwnd,
            main_funestra_hwnd=main_funestra_hwnd,
            dashboard_hwnd=dash_hwnd),
        beneath=overlay_hwnd,
        rfb_mode=rfb_mode,
    )
    logger.info("Post-loading window state corrected")
    _settle_the_funestras(
        _funestras_to_settle(
            portrait_hwnd=portrait_hwnd, landscape_hwnd=landscape_hwnd,
            main_funestra_hwnd=main_funestra_hwnd,
        ),
        overlay_hwnd=overlay_hwnd,
    )
    _log_window_obstruction("Main Funestra", main_funestra_hwnd, ignore=overlay_hwnd)
    _log_window_obstruction("Portrait satellite", portrait_hwnd, ignore=overlay_hwnd)
    _log_window_obstruction("Landscape satellite", landscape_hwnd, ignore=overlay_hwnd)
    return hwnds_by_window


def _funestras_to_settle(*, portrait_hwnd: int, landscape_hwnd: int,
                       main_funestra_hwnd: int) -> list[tuple[str, int, int]]:
    return [("portrait", portrait_hwnd, 0), ("landscape", landscape_hwnd, 0),
            ("main", main_funestra_hwnd, 0)]


def _settle_the_funestras(funestras: Sequence[tuple[str, int, int]], *,
                        overlay_hwnd: int = 0, passes: int = SETTLE_PASSES,
                        wait_s: float = SETTLE_WAIT_S) -> None:
    # Windows' SetWindowPos waits on the target's own thread, so a promotion can time out and land only when
    # that thread next pumps.
    for _ in range(passes):
        stack = iter_zorder()
        buried = [
            (name, hwnd) for name, hwnd, allowed_above in funestras
            if hwnd and _covering(hwnd, stack, ignore=(overlay_hwnd, allowed_above))
        ]
        if not buried:
            break
        for name, hwnd in buried:
            logger.info("The %s Funestra is still buried; re-asserting its band", name)
            set_always_on_top(hwnd, True, under=overlay_hwnd)
        time.sleep(wait_s)


def _covering(hwnd: int, stack, *, ignore: tuple[int, ...] = ()) -> list:
    return [w for w in windows_obscuring(hwnd, stack) if w.hwnd not in ignore]


def _main_browse_stills(bridge_config) -> list[str]:
    return [
        handle.preview
        for handle in build_library_handles(
            bridge_config.main_sources, bridge_config.regen_metadata_root
        )
    ]


def start_hud_priming(
    bridge_config, manifest, *, enabled: bool
) -> tuple[HudPublisher | None, threading.Event]:
    primed = threading.Event()
    if not enabled:
        primed.set()
        return None, primed
    sources = (bridge_config.portrait.sources, bridge_config.landscape.sources)
    cache_dir = bridge_config.state_dir / THUMBNAIL_CACHE_DIRNAME
    publisher = HudPublisher(
        {
            **{funestra.label: manifest.satellite(funestra).hud_file
               for funestra in FunTimeFunestra.SATELLITES},
            "main_funestra": Path(manifest.commands.main_funestra_console_file),
            DASHBOARD_OSR2_PART: osr2_part_path(Path(manifest.commands.state_dir)),
        },
        cache_dir,
    )

    def _warm() -> None:
        try:
            prime_group_indexes(sources, bridge_config.regen_metadata_root)
        finally:
            primed.set()
        for source in sources:
            if source:
                prewarm_thumbnails(collect_video_files(source), cache_dir)
        prewarm_thumbnails(_main_browse_stills(bridge_config), cache_dir)
        prewarm_thumbnails(collect_video_files(genau_flick_sources(manifest.media.genau_flicks)),
                           cache_dir)

    threading.Thread(target=_warm, daemon=True, name="hud-warm").start()
    return publisher, primed


def clear_last_sessions_leftovers(
    state_dir: Path, commands: CommandFiles, *, pids_file: Path, ahk_cmd_file: Path,
) -> None:
    dashboard_cmd_file = Path(commands.dashboard_cmd_file)
    for stale in (ahk_cmd_file, pids_file, dashboard_cmd_file,
                  dashboard_cmd_file.with_suffix(".processing"), state_dir / PRESS_PORT_FILENAME,
                  state_dir / UNLOGGED_MESSAGE_PORT_FILENAME,
                  Path(commands.dashboard_state_file), session_end_marker_path(state_dir),
                  state_dir / REFERENCE_OPEN_FILENAME, state_dir / LIBRARY_OPEN_FILENAME):
        stale.unlink(missing_ok=True)


def secondary_rects(manifest: LaunchManifest) -> Callable[..., SecondaryMonitorRects] | None:
    try:
        layout = screen_layout(manifest.layout)
    except (ValueError, OSError):
        return None
    return partial(secondary_monitor_rects, layout.secondary_monitor, layout.config)


def seat_the_secondary_monitor(manifest: LaunchManifest, hwnds_by_window: dict[str, int]) -> None:
    state = read_shared_state(shared_state_path(Path(manifest.commands.state_dir)))
    rects = secondary_rects(manifest)
    if state is None or rects is None:
        return
    most = majority_now(state, Path(manifest.commands.main_funestra_status_file),
                        Path(manifest.commands.genau_status_file))
    FunTimeWindows(pids=ChildPids(), hwnds_by_window=hwnds_by_window).seat(rects(majority=most))


def _reveal_the_app(
    result: StartupResult,
    *,
    manifest: LaunchManifest,
    loading_screen: RaisedLoadingScreen,
    hud: tuple[HudPublisher | None, threading.Event],
    before_the_loading_screen_goes: Callable[[], RfbMode] = lambda: RfbMode.RFB,
) -> None:
    hud_publisher, hud_primed = hud
    if hud_publisher is not None and not hud_primed.wait(timeout=HUD_PRIME_TIMEOUT_S):
        logger.warning("HUD indexes not primed after %.0fs; revealing anyway",
                       HUD_PRIME_TIMEOUT_S)
    rfb_mode = before_the_loading_screen_goes()
    loading_screen.progress.advance("finalizing")

    hwnds_by_window = _fix_post_loading_windows(
        result, overlay_hwnd=loading_screen.window(), rfb_mode=rfb_mode)
    seat_the_secondary_monitor(manifest, hwnds_by_window)

    loading_screen.take_it_down()

    release_the_funestras(manifest, result.main_mode)
    release_the_headset(loading_screen.progress_file.parent)

    apply_topmost_bands(hwnds_by_window, rfb_mode=rfb_mode)
    _settle_the_funestras(
        _funestras_to_settle(
            portrait_hwnd=hwnds_by_window.get("portrait", 0),
            landscape_hwnd=hwnds_by_window.get("landscape", 0),
            main_funestra_hwnd=hwnds_by_window.get("main_funestra", 0),
        ),
        passes=3, wait_s=0.4,
    )


def _serve_loopback(port: int, dispatch_runner: DispatchLoopRunner) -> ThreadingHTTPServer | None:
    try:
        server = serve_loopback(port=port, omni_paused=lambda: dispatch_runner.state.omni_paused)
    except OSError:
        logger.warning("Loopback server not started (port %d busy)", port, exc_info=True)
        return None
    logger.info("Loopback server started on 127.0.0.1:%d", port)
    return server


@dataclass(frozen=True)
class PreparedVoice:
    controller: Future[VoiceController]
    thread: threading.Thread


def _what_it_answers(answer: Future[HostedVocabulary]) -> HostedVocabulary:
    try:
        return answer.result(timeout=ASKING_BUDGET_S)
    except Exception:
        logger.exception("The hosted Origenerator was not heard saying what it answers "
                         "by voice")
        return NO_HOSTED_VOCABULARY


def _asked_in_the_background(ask: Callable[[], HostedVocabulary]) -> Future[HostedVocabulary]:
    answer: Future[HostedVocabulary] = Future()

    def ask_it() -> None:
        try:
            answer.set_result(ask())
        except Exception as error:
            answer.set_exception(error)

    threading.Thread(target=ask_it, daemon=True, name="ask-the-hosted-app").start()
    return answer


def prepare_voice_control(
    config_path: str, *, dashboard_cmd_file: Path, hosted_cmd_file: str | Path | None,
    hosted_said_at_suffix: str = "",
    ask_the_hosted_app: Callable[[], HostedVocabulary] = lambda: NO_HOSTED_VOCABULARY,
) -> PreparedVoice | None:
    try:
        cfg = load_config(config_path)
        if not cfg.voice_control.enabled:
            logger.info("Voice control disabled in config")
            return None
        unavailable = why_unavailable()
        logger.info("Voice control check: available=%s, model=%s, device_name=%s",
                    not unavailable, cfg.voice_control.model_path, cfg.voice_control.device_name)
        if unavailable:
            logger.error("Voice control enabled but import failed: %s", unavailable)
            return None
        settings = cfg.voice_control
        verifying_listener = WhisperReader()
        verifying_listener.load_ahead()
        answer = _asked_in_the_background(ask_the_hosted_app)
        controller: Future[VoiceController] = Future()

        def listen() -> None:
            try:
                voice_controller = VoiceController(
                    cmd_file=dashboard_cmd_file,
                    settings=settings,
                    verifying_listener=verifying_listener,
                    hosted=HostedVoice(
                        _what_it_answers(answer), hosted_cmd_file, hosted_said_at_suffix),
                )
                voice_controller.mute()
            except Exception as error:
                logger.exception("Voice control setup failed")
                controller.set_exception(error)
                return
            controller.set_result(voice_controller)
            voice_controller.run()

        voice_thread = threading.Thread(target=listen, daemon=True, name="voice-control")
        voice_thread.start()
        logger.info("Voice control thread launched")
        return PreparedVoice(controller, voice_thread)
    except Exception:
        logger.exception("Voice control setup failed")
        return None


def start_voice_control(
    prepared: PreparedVoice | None, *, dispatch_runner: DispatchLoopRunner,
) -> tuple[VoiceController | None, threading.Thread | None]:
    if prepared is None:
        return None, None
    try:
        voice_controller = prepared.controller.result(timeout=ASKING_BUDGET_S)
    except Exception:
        return None, None
    dispatch_runner.voice.controller = voice_controller
    dispatch_runner.hear_as_a_lock(voice_controller.hosted.lock_phrases)
    voice_controller.active_funestra = lambda: dispatch_runner.state.active_funestra
    voice_controller.hosted_satellites = lambda: hosted_satellites(dispatch_runner.state,
                                                              dispatch_runner.config)
    if not dispatch_runner.state.mic_muted:
        voice_controller.unmute()
    return voice_controller, prepared.thread


VOICE_START_BUDGET_S = 30.0


def wait_for_voice_under_the_loading_screen(
    voice_controller: VoiceController | None, *, progress: ProgressReporter,
) -> None:
    if voice_controller is None:
        return
    progress.announce(STARTING_VOICE_COMMANDS.key)
    with progress.loading_screen_kept_up():
        if not voice_controller.done_starting.wait(VOICE_START_BUDGET_S):
            logger.warning("Voice commands were not listening after %.0fs; opening Fun Time "
                           "without them", VOICE_START_BUDGET_S)


ORIGENERATOR_BOOT_BUDGET_S = 120.0
ORIGENERATOR_BOOT_POLL_S = 0.5


def activate_origenerator_under_the_loading_screen(
    runner: DispatchLoopRunner,
    *,
    progress: ProgressReporter,
) -> RfbMode:
    if not runner.opening.activates:
        return runner.state.rfb_mode
    started = time.monotonic()

    def wait_for_the_app(has_answered: Callable[[], bool]) -> bool:
        while not has_answered():
            waited = time.monotonic() - started
            if waited >= ORIGENERATOR_BOOT_BUDGET_S:
                return False
            progress.announce(ACTIVATING_ORIGENERATOR.key)
            time.sleep(ORIGENERATOR_BOOT_POLL_S)
        return True

    progress.announce(ACTIVATING_ORIGENERATOR.key)
    runner.activate_origenerator(wait_for_the_app=wait_for_the_app)
    return runner.state.rfb_mode


def finish_under_the_loading_screen(
    runner: DispatchLoopRunner,
    voice_controller: VoiceController | None,
    *,
    progress: ProgressReporter,
) -> RfbMode:
    rfb_mode = activate_origenerator_under_the_loading_screen(runner, progress=progress)
    wait_for_voice_under_the_loading_screen(voice_controller, progress=progress)
    return rfb_mode


def _build_the_dispatch_loop(
    result: StartupResult,
    launch: SessionLaunch,
    *,
    ahk_cmd_file: Path,
    hud_publisher,
    env: SessionEnvironment,
) -> tuple[DispatchLoopRunner, threading.Thread]:
    manifest = launch.manifest
    rfb_shortcut = Shortcut()
    if manifest.random_favs_browser.enabled:
        rfb_shortcut = resolve_shortcut(manifest.random_favs_browser.shortcut_path)

    return DispatchLoopRunner(
        launch.bridge_config,
        SessionFiles(
            dashboard_cmd=Path(manifest.commands.dashboard_cmd_file),
            shared_state=shared_state_path(launch.state_dir),
            ahk_cmd=ahk_cmd_file,
            manifest=launch.manifest_path),
        SessionStart(
            windows=FunTimeWindows(
                pids=ChildPids(
                    main_funestra=result.main_funestra_pid,
                    portrait=result.portrait_pid,
                    landscape=result.landscape_pid,
                    dashboard=result.dashboard_pid,
                    origenerator=result.origenerator_pid,
                ),
                rfb_hwnd=result.rfb_hwnd,
                hwnds_by_window=result.hwnds_by_window,
            ),
            rfb_shortcut=rfb_shortcut,
            rfb_slideshow=rfb_slideshow_on(result.rfb_hwnd) if result.rfb_hwnd else None,
            press_and_caret_watch=(
                PressAndCaretWatch()
                if result.rfb_hwnd or manifest.runtime.origenerator_dir.strip() else None),
            origenerator_already_open=result.origenerator_already_open,
            origenerator_slideshow_on=result.origenerator_slideshow_on),
        Publishing(
            dashboard_enabled=manifest.dashboard_enabled,
            hud_publisher=hud_publisher,
            secondary_rects=secondary_rects(manifest)),
        env=env,
    )


def _start_the_dispatch_loop(dispatch_runner: DispatchLoopRunner) -> threading.Thread:
    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    logger.info("Background dispatch loop started")
    return dispatch_thread


DISPATCH_LOOP_STOP_S = 1.0


def stop_everything(manifest: LaunchManifest,
                    dispatch: tuple[DispatchLoopRunner, threading.Thread]) -> None:
    runner, loop = dispatch
    runner.stop()
    loop.join(timeout=DISPATCH_LOOP_STOP_S)
    if loop.is_alive():
        logger.warning("The dispatch loop was still busy %.1fs after the session ended",
                       DISPATCH_LOOP_STOP_S)
    silence_the_funestras(manifest)
    hold_the_osr2(manifest.commands)


@dataclass(frozen=True)
class SessionEnd:
    exit_code: int
    closed: str | None = None


@dataclass(frozen=True)
class SessionOver:
    exit_code: int
    why: str = ""


def _wait_for_the_session_to_end(
    ahk_proc: subprocess.Popen, state_dir: Path, *, children: dict,
    poll_s: float = 0.1,
) -> SessionEnd:
    marker = session_end_marker_path(state_dir)
    while ahk_proc.poll() is None:
        if marker.exists():
            return SessionEnd(0)
        closed = the_part_that_closed(children)
        if closed is not None:
            return SessionEnd(1, closed)
        time.sleep(poll_s)
    return SessionEnd(ahk_proc.wait())


def _run_until_the_hotkeys_exit(
    launch: SessionLaunch,
    hotkeys: HotkeyScript,
    result: StartupResult,
    running: RunningSession,
    *,
    show_overlays: bool,
) -> SessionOver:
    state_dir = launch.state_dir
    children = running.children
    origenerator_cmd_file = launch.bridge_config.origenerator_cmd_file
    sibling_checkouts = launch.manifest.runtime.sibling_checkouts
    voice_controller, voice_thread = running.voice
    dispatch_runner, _loop = running.dispatch
    asked = False
    closed = None
    try:
        ended = _wait_for_the_session_to_end(hotkeys.proc, state_dir, children=children)
        exit_code, closed = ended.exit_code, ended.closed
        asked = session_end_marker_path(state_dir).exists()
        if closed is not None:
            logger.error("The session ended because %s closed itself", closed)
        else:
            logger.info("The session ended with code %s (%s)", exit_code,
                        _describe_session_end(state_dir, exit_code))
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        exit_code = 1
    finally:
        stop_everything(launch.manifest, running.dispatch)
        crossing = pending_handoff(state_dir)
        esc_cancels = (CANCEL_CLOSING_FUN_TIME
                       if asked and show_overlays and crossing is None else "")
        closing_screen = (
            _closing_screen(state_dir, sibling_checkouts=sibling_checkouts,
                            crossing=crossing, esc_cancels=esc_cancels)
            if show_overlays else contextlib.nullcontext(NullProgress()))
        with closing_screen as shutdown_progress:
            if voice_controller is not None:
                voice_controller.stop()
            if voice_thread is not None:
                voice_thread.join(timeout=2.0)
            dispatch_runner.close()
            if running.loopback_server is not None:
                running.loopback_server.shutdown()
                running.loopback_server.server_close()
            logger.info("The session is over — shutting down child processes")
            flag = state_dir / CANCEL_FILENAME
            parking = crossing is not None or (
                bool(esc_cancels) and what_the_flag_asks(flag) == CANCEL_WORD)
            _shutdown_children(
                result.rfb_hwnd, children, shutdown_progress, state_dir=state_dir,
                farewell=HostedAppFarewell(
                    command_file=(origenerator_cmd_file
                                  if parking or result.origenerator_taken_over else None),
                    keep=parking, taken_over=result.origenerator_taken_over),
            )
            if crossing is None:
                stop_hotkey_script(hotkeys.proc, hotkeys.cmd_file)
            if esc_cancels and what_the_flag_asks(flag) == CANCEL_WORD:
                logger.info("Esc called the quit off; opening Fun Time again")
                way_back = launch_the_way_back_screen(state_dir, sibling_checkouts=sibling_checkouts)
                _wait_for_closing_screen(ready_file_for(crossing_progress_path(state_dir)), way_back)
                request_handoff(state_dir, DESKTOP, cancelable=False)
            elif crossing is None:
                let_go_of_a_kept_origenerator(state_dir, origenerator_cmd_file)

    return SessionOver(exit_code, why="" if closed is None else part_closed_message(closed))


def run_session(
    *,
    manifest_path: str | Path,
    hotkeys: HotkeyScriptLaunch,
    state_dir: str | Path,
    loading_screen: RaisedLoadingScreen,
    env: SessionEnvironment = ORDINARY_SESSION,
) -> SessionOver:
    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)

    take_up_the_retired_state_file_names(state_dir)

    manifest = LaunchManifest.read(manifest_path)
    bridge_config = build_bridge_config_from_manifest(manifest)
    dashboard_enabled = manifest.dashboard_enabled
    launch = SessionLaunch(manifest_path, manifest, bridge_config, state_dir)

    add_dispatch_file_handler(Path(manifest.runtime.windows_bridge_log_file))

    dashboard_cmd_file = Path(manifest.commands.dashboard_cmd_file)
    ahk_cmd_file = state_dir / "ahk_cmd.txt"
    pids_file = state_dir / "bridge_pids.ini"
    clear_last_sessions_leftovers(state_dir, manifest.commands,
                                  pids_file=pids_file, ahk_cmd_file=ahk_cmd_file)
    prepared_voice = prepare_voice_control(
        manifest.runtime.config_path, dashboard_cmd_file=dashboard_cmd_file,
        hosted_cmd_file=bridge_config.origenerator_cmd_file,
        hosted_said_at_suffix=bridge_config.origenerator_said_at_suffix,
        ask_the_hosted_app=partial(what_it_answers_by_voice_in, manifest))

    progress = loading_screen.progress

    if env.integration:
        ahk_cmd_file.write_text("suspend_hotkeys", encoding="utf-8")
        logger.info("Pre-wrote suspend_hotkeys for integration test run")

    hotkey_script = HotkeyScript(
        launch_hotkey_script(ahk_exe=hotkeys.ahk_exe, script=hotkeys.script,
                             manifest_path=manifest_path, pids_file=pids_file,
                             project_dir=hotkeys.project_dir),
        ahk_cmd_file)

    hud = start_hud_priming(bridge_config, manifest, enabled=dashboard_enabled)
    hud_publisher = hud[0]

    logger.info("Running startup sequence")
    try:
        result = run_startup_sequence(
            manifest_path=manifest_path,
            state_dir=state_dir,
            progress=progress,
            hide_windows=env.show_overlays,
            env=env,
        )
    except StartupCancelled as cancelled:
        return _take_down_the_startup(
            StartupEnd("Startup cancelled by user", canceled=True),
            LaunchedSoFar(cancelled.launched_pids, cancelled.rfb_hwnd,
                          release_of(launch, cancelled.origenerator_taken_over)),
            launch, loading_screen, hotkey_script)
    except FunestraDied as died:
        _take_down_the_startup(
            StartupEnd(str(died)),
            LaunchedSoFar(died.launched_pids, died.rfb_hwnd,
                          release_of(launch, died.origenerator_taken_over)),
            launch, loading_screen, hotkey_script)
        said = funestra_died_message(died.funestra, died.said)
        logger.error(said)
        return SessionOver(1, why=said)

    launched = [getattr(result, key) for key in _CHILD_PID_KEYS
                if not (key == "origenerator_pid" and result.origenerator_taken_over)]
    started = LaunchedSoFar(launched, result.rfb_hwnd, release_of(launch, result.origenerator_taken_over))

    if progress.cancelled:
        return _take_down_the_startup(
            StartupEnd("Startup cancelled by user", canceled=True),
            started, launch, loading_screen, hotkey_script)

    logger.info(
        "Startup complete: main_funestra=%d portrait=%d landscape=%d dashboard=%d audio=%d "
        "origenerator=%d",
        result.main_funestra_pid, result.portrait_pid, result.landscape_pid,
        result.dashboard_pid, result.audio_pid, result.origenerator_pid,
    )

    try:
        dispatch_runner = _build_the_dispatch_loop(
            result, launch, ahk_cmd_file=ahk_cmd_file, hud_publisher=hud_publisher, env=env)
        voice_controller, voice_thread = start_voice_control(
            prepared_voice, dispatch_runner=dispatch_runner)

        finish = partial(finish_under_the_loading_screen, dispatch_runner, voice_controller,
                         progress=progress)

        if env.show_overlays:
            _reveal_the_app(result, manifest=manifest, loading_screen=loading_screen,
                            hud=hud, before_the_loading_screen_goes=finish)
        else:
            finish()

        children = identify_children(result)
        write_pids_file(pids_file, children)

        dispatch_thread = _start_the_dispatch_loop(dispatch_runner)
        loopback_server = _serve_loopback(manifest.loopback_port, dispatch_runner)
    except BaseException:
        _take_down_the_startup(
            StartupEnd("The session failed while opening"),
            started, launch, loading_screen, hotkey_script)
        raise

    return _run_until_the_hotkeys_exit(
        launch, hotkey_script, result,
        RunningSession(children, (voice_controller, voice_thread),
                       (dispatch_runner, dispatch_thread), loopback_server),
        show_overlays=env.show_overlays,
    )
