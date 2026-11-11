from __future__ import annotations

import argparse
import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import process_started
from funestra.file_channel import append_command
from funestra.flick_folder import vr_flicks_in

from fun_time.branch_session import describe_the_session_on_the_taskbar
from fun_time.bridge_records import BridgeConfig
from fun_time.checkout_overrides import apply_this_checkouts_overrides, sibling_checkouts_kwargs
from fun_time.child_launch import open_child_log
from fun_time.config import DEFAULT_CONFIG_PATH, load_config
from fun_time.event_log import open_event_log
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.fun_time_windows import ChildPids, FunTimeWindows
from fun_time.funestra_deaths import (
    ORIGENERATOR_NEVER_CAME_UP,
    part_closed_message,
    show_funestra_died_alert,
)
from fun_time.funestra_engine import engine_missing_abort
from fun_time.funestra_status import read_main_funestra_status
from fun_time.genau_config import GenauSettings
from fun_time.headset_power import put_the_headset_to_sleep
from fun_time.hosted_origenerator import (
    HostedApp,
    bring_up_the_hosted_app,
    what_it_answers_by_voice_in,
)
from fun_time.main_list_commands import genau_flick_shapes
from fun_time.manifest import (
    LaunchManifest,
    build_windows_bridge_manifest,
    write_manifest_data,
)
from fun_time.modes import (
    VideoShapes,
)
from fun_time.orchestrator import (
    ensure_runtime_files,
    no_origenerator_set_up,
    require_dir,
    stamp_the_users_pin_outside_every_checkout,
    tell_the_hidden_launcher_startup_resolved,
    validate_config,
)
from fun_time.origenerator_opening import OrigeneratorNeverCameUp
from fun_time.overlay_progress import (
    CANCEL_CLOSING_FUN_TIME_VR,
    CANCEL_FILENAME,
    CANCEL_OPENING_FUN_TIME_VR,
    CANCEL_WORD,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    NullProgress,
    PhaseProgress,
    ProgressReporter,
    StartupCancelled,
    ready_file_for,
    what_the_flag_asks,
)
from fun_time.project_paths import HOTKEY_SCRIPT_NAME, PROJECT_DIR
from fun_time.satellite_launch import satellite_feed
from fun_time.session_end import (
    A_PART,
    ASKED,
    SessionEnd,
    describe_session_end,
    wait_for_the_session_to_end,
)
from fun_time.session_environment import SessionEnvironment
from fun_time.session_handoff import (
    DESKTOP,
    VR,
    drop_transition_screen,
    forget_a_request_a_crash_left_behind,
    hand_over_if_asked,
    headset_is_held,
    hold_the_headset,
    keep_the_transition_screen,
    launch_the_way_back_screen,
    launch_transition_screen,
    pending_handoff,
    release_the_headset,
    request_handoff,
    say_the_crossing_is_cancelled,
    what_esc_offers,
)
from fun_time.shared_state import BridgeState, shared_state_path
from fun_time.single_instance import (
    claim_the_session,
    show_already_running_message,
    the_one_sessions_mutex,
)
from fun_time.state_file_names import take_up_the_retired_state_file_names
from fun_time.voice_control import VoiceController
from fun_time.vr_videos import shapes_verb
from fun_time.windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    Publishing,
    SessionFiles,
    SessionStart,
    build_bridge_config_from_manifest,
)
from fun_time.windows_bridge_orchestrator import (
    CLOSING_SCREEN_READY_CEILING_S,
    ChildProcess,
    PreparedVoice,
    clear_last_sessions_leftovers,
    finish_under_the_loading_screen,
    kill_recorded_child,
    launch_hotkey_script,
    let_go_of_a_kept_origenerator,
    log_into_the_hotkey_scripts_log,
    prepare_voice_control,
    see_the_hosted_app_out,
    start_hud_priming,
    start_voice_control,
    stop_everything,
    stop_hotkey_script,
    write_pids_file,
)
from fun_time.windows_bridge_sequencer import release_the_funestras
from fun_time.windows_bridge_startup import (
    carry_the_last_session_in,
    launch_audio_companion,
    reap_orphaned_satellites,
)

from . import vr_runtime
from .loading_screen import (
    VR_SHUTDOWN_PHASES,
    VR_STARTUP_PHASES,
    scene_ready_file,
    wait_for_loading_screen_painted,
)

VR_STARTUP_MARKER_NAME = VR.ready_marker
VR_FUNESTRAS_MODULE = "fun_time_vr.app"

FUNESTRA_READY_TIMEOUT_S = 120.0

SCENE_READY_TIMEOUT_S = 35.0

logger = logging.getLogger("fun_time_vr.orchestrator")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the Fun Time VR session.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--check", action="store_true", help="Validate config and exit.")
    parser.add_argument("--no-cancel", action="store_true",
                        help="The way back from a crossing Esc called off: offer no Esc.")
    return parser


def vr_main_sources(config) -> str:
    dirs = [*config.vr.library_dirs, *config.paths.main_funestra_library_dirs]
    return "|".join(str(path) for path in dirs)


def build_vr_manifest(config, *, dashboard_enabled: bool = True) -> dict[str, dict[str, str]]:
    manifest = build_windows_bridge_manifest(config, dashboard_enabled=dashboard_enabled)
    manifest["media"]["main_funestra_library_sources"] = vr_main_sources(config)
    manifest["media"]["vr_library_dirs"] = "|".join(
        str(path) for path in config.vr.library_dirs)
    manifest["media"]["genau_vr_flicks"] = str(vr_flicks_in(config.paths.flicks_dir))
    manifest["vr"] = {
        "funestra_module": VR_FUNESTRAS_MODULE,
        "library_dirs": "|".join(str(path) for path in config.vr.library_dirs),
        "audio_device": config.vr.audio_device or "",
        "compositor_layers": "1" if config.vr.compositor_layers else "0",
        "flicks_folder": str(config.paths.flicks_dir),
        "notify_host": config.audio_companion.host,
        "notify_port": str(config.audio_companion.port),
        **GenauSettings.read(config.config_path).manifest_fields(),
    }
    return manifest


def validate_vr_config(config) -> None:
    for library_dir in config.vr.library_dirs:
        require_dir(library_dir)


def launch_vr_funestras(
    *, python_exe: str | Path, manifest_path: Path, log_file: Path,
    sibling_checkouts: str | None = None,
) -> subprocess.Popen:
    command = [str(python_exe), "-m", VR_FUNESTRAS_MODULE, "--manifest", str(manifest_path)]
    with open_child_log(log_file, command) as log:
        return subprocess.Popen(command, stdout=log, stderr=log,
                                **sibling_checkouts_kwargs(sibling_checkouts),
                                **hidden_subprocess_kwargs())


def _wait_for_funestra(
    status_file: Path, funestra: subprocess.Popen, progress: ProgressReporter | None = None,
) -> bool:
    deadline = time.monotonic() + FUNESTRA_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if progress is not None and progress.cancelled:
            raise StartupCancelled()
        if read_main_funestra_status(status_file).video:
            return True
        if funestra.poll() is not None:
            logger.error("headset exited during startup (code %s)", funestra.returncode)
            return False
        time.sleep(0.25)
    logger.error("headset published no status within %.0fs", FUNESTRA_READY_TIMEOUT_S)
    return False


def _wait_for_the_room(
    marker_file: Path, funestra: subprocess.Popen, progress: ProgressReporter | None = None,
) -> bool:
    deadline = time.monotonic() + SCENE_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if progress is not None and progress.cancelled:
            raise StartupCancelled()
        if marker_file.exists():
            return True
        if funestra.poll() is not None:
            return False
        time.sleep(0.1)
    logger.warning("The room was not reported up within %.0fs; revealing anyway",
                   SCENE_READY_TIMEOUT_S)
    return False


def resume_genau_shapes(genau_cmd_file: Path, shapes: VideoShapes) -> None:
    if shapes.narrows and (shapes.plays_vr or shapes.plays_flat):
        append_command(genau_cmd_file,
                       shapes_verb(plays_vr=shapes.plays_vr, plays_flat=shapes.plays_flat))


HEADSET_CLOSES_ITS_CHANNELS_TIMEOUT_S = 15.0


class _LoadingScreen:
    def __init__(self, state_dir: Path, *, cancelable: bool = True) -> None:
        offer = what_esc_offers(state_dir, cancelable=cancelable, arriving_in=VR,
                                came_from=DESKTOP, opening=CANCEL_OPENING_FUN_TIME_VR)
        esc_cancels = offer.hint
        self.turns_back_to = offer.turns_back_to
        self.progress_file = state_dir / PROGRESS_FILENAME
        self.cancel_file = state_dir / CANCEL_FILENAME
        if self.turns_back_to is None:
            self.cancel_file.unlink(missing_ok=True)
        self.progress: ProgressReporter = PhaseProgress(
            self.progress_file, phases=VR_STARTUP_PHASES, cancel_file=self.cancel_file,
            hint=esc_cancels,
        )

    def where_esc_hands_back_to(self):
        return (self.turns_back_to
                if what_the_flag_asks(self.cancel_file) == CANCEL_WORD else None)

    def clear(self) -> None:
        self.progress_file.unlink(missing_ok=True)
        self.cancel_file.unlink(missing_ok=True)


def _take_down_the_launch(
    launch: _Launch, launched: _Launched, ahk_proc: subprocess.Popen,
) -> None:
    say_the_crossing_is_cancelled(launch.state_dir)
    stop_hotkey_script(ahk_proc, launch.ahk_cmd_file)
    funestra_showing_the_loading_screen = launched.children.get("vr_funestras_pid")
    with launch.progress.loading_screen_kept_up():
        for key, child in launched.children.items():
            if key != "vr_funestras_pid":
                kill_recorded_child(child)
        if funestra_showing_the_loading_screen is not None:
            kill_recorded_child(funestra_showing_the_loading_screen)
    _release_vr_runtime(launched.runtime_was_up, launch.state_dir)
    launch.loading_screen.clear()


def _cancel_vr_startup(
    launch: _Launch, launched: _Launched, ahk_proc: subprocess.Popen,
) -> int:
    logger.info("Startup cancelled by user; tearing down %d launched child(ren)",
                len(launched.children))
    back_to = launch.loading_screen.where_esc_hands_back_to()
    if (app := launched.children.get("origenerator_pid")) is not None and see_the_hosted_app_out(
            launch.state_dir, app, Path(launch.commands.origenerator_cmd_file),
            keep=back_to is not None, taken_over=launched.hosted_taken_over):
        launched.children.pop("origenerator_pid")
    _take_down_the_launch(launch, launched, ahk_proc)
    if back_to is not None:
        logger.info("Canceled; handing back to %s", back_to.app_name)
        request_handoff(launch.state_dir, back_to, cancelable=False)
    else:
        logger.info("Canceled; closing")
        drop_transition_screen(launch.state_dir)
        let_go_of_a_kept_origenerator(
            launch.state_dir, Path(launch.commands.origenerator_cmd_file))
    return 0


@contextlib.contextmanager
def _closing_screen(
    state_dir: Path, funestra: subprocess.Popen, *, enabled: bool, esc_cancels: str = "",
) -> Iterator[ProgressReporter]:
    if not enabled:
        yield NullProgress()
        return

    progress_file = state_dir / SHUTDOWN_PROGRESS_FILENAME
    ready_file = ready_file_for(progress_file)
    ready_file.unlink(missing_ok=True)
    shutdown = PhaseProgress(progress_file, phases=VR_SHUTDOWN_PHASES, hint=esc_cancels)
    shutdown.advance("controls")
    wait_for_loading_screen_painted(
        ready_file,
        still_alive=lambda: funestra.poll() is None,
        timeout_s=CLOSING_SCREEN_READY_CEILING_S,
    )
    try:
        yield shutdown
    finally:
        progress_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


@dataclass(frozen=True)
class _Launch:
    state_dir: Path
    manifest_path: Path
    manifest: LaunchManifest
    bridge_config: BridgeConfig
    pids_file: Path
    ahk_cmd_file: Path
    prepared_voice: PreparedVoice | None
    loading_screen: _LoadingScreen

    @property
    def commands(self):
        return self.manifest.commands

    @property
    def progress(self) -> ProgressReporter:
        return self.loading_screen.progress

    @property
    def dashboard_cmd_file(self) -> Path:
        return Path(self.commands.dashboard_cmd_file)

    @property
    def main_funestra_status_file(self) -> Path:
        return Path(self.commands.main_funestra_status_file)

    @property
    def room_ready_file(self) -> Path:
        return scene_ready_file(self.state_dir)


def _prepare_the_launch(config, env: SessionEnvironment, *, cancelable: bool) -> _Launch:
    state_dir = config.paths.state_dir
    manifest_path = write_manifest_data(
        build_vr_manifest(config, dashboard_enabled=env.dashboard_enabled),
        state_dir / "windows_bridge_launch.ini",
    )
    take_up_the_retired_state_file_names(state_dir)
    open_event_log(state_dir)
    manifest = LaunchManifest.read(manifest_path)
    log_into_the_hotkey_scripts_log(Path(manifest.runtime.windows_bridge_log_file))
    bridge_config = build_bridge_config_from_manifest(manifest, vr_main_funestra=True)
    pids_file = state_dir / "bridge_pids.ini"
    ahk_cmd_file = state_dir / "ahk_cmd.txt"
    dashboard_cmd_file = Path(manifest.commands.dashboard_cmd_file)
    clear_last_sessions_leftovers(
        state_dir, manifest.commands, pids_file=pids_file, ahk_cmd_file=ahk_cmd_file)
    prepared_voice = prepare_voice_control(
        manifest.runtime.config_path, dashboard_cmd_file=dashboard_cmd_file,
        hosted_cmd_file=bridge_config.origenerator_cmd_file,
        hosted_said_at_suffix=bridge_config.origenerator_said_at_suffix,
        ask_the_hosted_app=partial(what_it_answers_by_voice_in, manifest), state_dir=state_dir)
    return _Launch(
        state_dir=state_dir, manifest_path=manifest_path, manifest=manifest,
        bridge_config=bridge_config, pids_file=pids_file, ahk_cmd_file=ahk_cmd_file,
        prepared_voice=prepared_voice, loading_screen=_LoadingScreen(state_dir, cancelable=cancelable),
    )


@dataclass
class _Launched:
    children: dict[str, ChildProcess] = field(default_factory=dict)
    hosted: HostedApp | None = None
    funestra: subprocess.Popen | None = None
    runtime_was_up: bool = True

    @property
    def hosted_taken_over(self) -> bool:
        return self.hosted is not None and self.hosted.taken_over

    def record(self, name: str, pid: int) -> None:
        self.children[name] = ChildProcess(
            pid=pid, created_at=process_started(pid) or 0)


@dataclass(frozen=True)
class _Running:
    dispatch_runner: DispatchLoopRunner
    dispatch_thread: threading.Thread
    voice_controller: VoiceController | None
    voice_thread: threading.Thread | None


def run_vr_bridge(config, env: SessionEnvironment, *, cancelable: bool = True) -> int:
    launch = _prepare_the_launch(config, env, cancelable=cancelable)
    launched = _Launched()
    progress = launch.progress

    ahk_proc = launch_hotkey_script(
        ahk_exe=config.paths.ahk_exe, script=config.project_dir / HOTKEY_SCRIPT_NAME,
        manifest_path=launch.manifest_path, pids_file=launch.pids_file,
        project_dir=config.project_dir)

    try:
        carried = _resume_the_room(launch)
        _launch_the_children(launch, config, launched)
        with progress.loading_screen_kept_up():
            joined = _wait_for_funestra(
                launch.main_funestra_status_file, launched.funestra, progress)
            if joined:
                _wait_for_the_room(launch.room_ready_file, launched.funestra, progress)
        if not joined:
            return _end_a_launch_the_headset_never_joined(launch, launched, ahk_proc)
        hud_publisher = start_hud_priming(
            launch.bridge_config, launch.manifest, dashboard_enabled=True)
        if progress.cancelled:
            raise StartupCancelled()
    except StartupCancelled:
        return _cancel_vr_startup(launch, launched, ahk_proc)

    try:
        running = _open_the_room(launch, launched, carried, hud_publisher)
    except OrigeneratorNeverCameUp:
        logger.error("Origenerator never finished starting; tearing down %d launched "
                     "child(ren)", len(launched.children))
        _take_down_the_launch(launch, launched, ahk_proc)
        drop_transition_screen(launch.state_dir)
        show_funestra_died_alert(ORIGENERATOR_NEVER_CAME_UP)
        return 1
    except BaseException:
        logger.info("The session failed while opening; tearing down %d launched child(ren)",
                    len(launched.children))
        _take_down_the_launch(launch, launched, ahk_proc)
        drop_transition_screen(launch.state_dir)
        raise
    return _hold_the_session(launch, launched, running, ahk_proc)


def _resume_the_room(launch: _Launch) -> BridgeState:
    manifest, commands, state_dir = launch.manifest, launch.commands, launch.state_dir
    launch.progress.advance("services")
    _end_a_funestra_left_holding_the_headset(launch)
    carried = carry_the_last_session_in(
        manifest, [satellite_feed(manifest, funestra, state_dir)
                   for funestra in FunTimeFunestra.SATELLITES],
        state_dir=state_dir)
    resume_genau_shapes(Path(commands.genau_cmd_file),
                        genau_flick_shapes(carried, launch.bridge_config))
    return carried


def _end_a_funestra_left_holding_the_headset(launch: _Launch) -> None:
    reap_orphaned_satellites(VR_FUNESTRAS_MODULE, [str(launch.manifest_path)])
    release_the_headset(launch.state_dir)


def _launch_the_children(launch: _Launch, config, launched: _Launched) -> None:
    manifest, state_dir, progress = launch.manifest, launch.state_dir, launch.progress
    progress.advance("companions")
    hosted = bring_up_the_hosted_app(
        manifest, sibling_checkouts=manifest.runtime.sibling_checkouts,
        in_a_headset=True)
    launched.hosted = hosted
    launched.record("origenerator_pid", hosted.pid)
    audio = launch_audio_companion(manifest, audio_device=config.vr.audio_device)
    logger.info("Audio companion launched (pid=%d)", audio.pid)
    launched.record("audio_pid", audio.pid)

    progress.advance("funestras")
    launched.runtime_was_up = vr_runtime.runtime_was_running()
    if not launched.runtime_was_up:
        vr_runtime.remember_the_sound_devices(state_dir)
    launch.main_funestra_status_file.unlink(missing_ok=True)
    launch.room_ready_file.unlink(missing_ok=True)
    launched.funestra = launch_vr_funestras(
        python_exe=manifest.executables.python_exe,
        manifest_path=launch.manifest_path,
        log_file=state_dir / "vr_funestras.log",
        sibling_checkouts=manifest.runtime.sibling_checkouts,
    )
    logger.info("headset launched (pid=%d)", launched.funestra.pid)
    launched.record("vr_funestras_pid", launched.funestra.pid)


def _end_a_launch_the_headset_never_joined(
    launch: _Launch, launched: _Launched, ahk_proc: subprocess.Popen,
) -> int:
    stop_hotkey_script(ahk_proc, launch.ahk_cmd_file)
    with launch.progress.loading_screen_kept_up():
        for child in launched.children.values():
            kill_recorded_child(child)
    _release_vr_runtime(launched.runtime_was_up, launch.state_dir)
    launch.loading_screen.clear()
    return 1


def _no_windows_on_the_desktop() -> FunTimeWindows:
    return FunTimeWindows(pids=ChildPids())


def _open_the_room(
    launch: _Launch, launched: _Launched, carried: BridgeState, hud_publisher,
) -> _Running:
    manifest, state_dir, progress = launch.manifest, launch.state_dir, launch.progress
    hosted = launched.hosted
    publishing_to_the_dashboard_in_the_room = Publishing(
        dashboard_enabled=True, hud_publisher=hud_publisher)
    dispatch_runner = DispatchLoopRunner(
        launch.bridge_config,
        SessionFiles(
            dashboard_cmd=launch.dashboard_cmd_file,
            shared_state=shared_state_path(state_dir),
            ahk_cmd=launch.ahk_cmd_file),
        SessionStart(
            windows=_no_windows_on_the_desktop(),
            origenerator_showing_a_slideshow=hosted is not None and hosted.showing_a_slideshow),
        publishing_to_the_dashboard_in_the_room,
    )
    voice_controller, voice_thread = start_voice_control(
        launch.prepared_voice, dispatch_runner=dispatch_runner)
    finish_under_the_loading_screen(dispatch_runner, voice_controller, progress=progress)
    progress.advance("finalizing")

    progress.finish()
    release_the_funestras(manifest, carried.main_mode)
    drop_transition_screen(state_dir)
    write_pids_file(launch.pids_file, launched.children)
    launch.loading_screen.clear()
    launch.room_ready_file.unlink(missing_ok=True)

    dispatch_thread = threading.Thread(
        target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()
    return _Running(dispatch_runner, dispatch_thread, voice_controller, voice_thread)


def _hold_the_session(
    launch: _Launch, launched: _Launched, running: _Running, ahk_proc: subprocess.Popen,
) -> int:
    state_dir, funestra = launch.state_dir, launched.funestra
    parts = {key: child for key, child in launched.children.items() if key != "vr_funestras_pid"}
    asked = False
    closed = None
    try:
        ended = wait_for_the_session_to_end(ahk_proc, state_dir, parts=parts,
                                            ending_it={"funestras": funestra})
        asked = ended.by == ASKED
        exit_code, closed = _the_sessions_end(ended, ahk_proc, funestra, state_dir)
    except KeyboardInterrupt:
        logger.info("Interrupted -- shutting down")
        exit_code = 1
    finally:
        _close_the_session(launch, launched, running, ahk_proc, asked=asked)
    if closed is not None:
        show_funestra_died_alert(part_closed_message(closed))
    return exit_code


def _the_sessions_end(
    ended: SessionEnd, ahk_proc: subprocess.Popen, funestra: subprocess.Popen, state_dir: Path,
) -> tuple[int, str | None]:
    if ended.by == "funestras":
        logger.info("headset exited with code %s -- ending the session", funestra.returncode)
        ahk_proc.terminate()
        ahk_proc.wait()
        return 0, None
    if ended.by == A_PART:
        logger.error("The session ended because %s closed itself", ended.closed)
        return 1, ended.closed
    logger.info("The session ended with code %s (%s)", ended.exit_code,
                describe_session_end(state_dir, ended.exit_code))
    return ended.exit_code, None


def _close_the_session(
    launch: _Launch, launched: _Launched, running: _Running, ahk_proc: subprocess.Popen, *,
    asked: bool,
) -> None:
    manifest, commands, state_dir = launch.manifest, launch.commands, launch.state_dir
    funestra = launched.funestra
    stop_everything(manifest, (running.dispatch_runner, running.dispatch_thread))
    held = back_to_vr = False
    if (crossing := pending_handoff(state_dir)) is not None:
        launch_transition_screen(state_dir, crossing,
                              sibling_checkouts=manifest.runtime.sibling_checkouts)
    esc_cancels = (crossing.crossing_hint if crossing is not None
                   else CANCEL_CLOSING_FUN_TIME_VR if asked else "")
    the_funestra_can_still_draw_it = funestra.poll() is None
    with _closing_screen(
        state_dir, funestra, enabled=the_funestra_can_still_draw_it, esc_cancels=esc_cancels,
    ) as shutdown:
        if crossing is not None:
            hold_the_headset(state_dir, stop_runtime=not launched.runtime_was_up)
        _stop_the_controls(running)
        shutdown.advance("companions")
        with shutdown.loading_screen_kept_up():
            kill_recorded_child(launched.children["audio_pid"])
        shutdown.advance("funestras")
        back_to_vr = (crossing is None and esc_cancels != ""
                      and what_the_flag_asks(state_dir / CANCEL_FILENAME) == CANCEL_WORD)
        if back_to_vr:
            logger.info("Esc called the quit off; opening Fun Time VR again")
            launch_the_way_back_screen(
                state_dir, sibling_checkouts=manifest.runtime.sibling_checkouts,
            )
            request_handoff(state_dir, VR, cancelable=False)
            hold_the_headset(state_dir, stop_runtime=False)
        hosted_app = launched.children.get("origenerator_pid")
        if hosted_app is not None and not see_the_hosted_app_out(
                state_dir, hosted_app, Path(commands.origenerator_cmd_file),
                keep=crossing is not None or back_to_vr,
                taken_over=launched.hosted_taken_over):
            with shutdown.loading_screen_kept_up():
                kill_recorded_child(hosted_app)
        held = (crossing is not None or back_to_vr) and _wait_for_the_headset_hold(
            state_dir, funestra,
        )
        if not held:
            with shutdown.loading_screen_kept_up():
                kill_recorded_child(launched.children["vr_funestras_pid"])
        if crossing is None:
            stop_hotkey_script(ahk_proc, launch.ahk_cmd_file)
    if not held and not back_to_vr:
        _release_vr_runtime(launched.runtime_was_up, state_dir)


def _stop_the_controls(running: _Running) -> None:
    if running.voice_controller is not None:
        running.voice_controller.stop()
    if running.voice_thread is not None:
        running.voice_thread.join(timeout=2.0)
    running.dispatch_runner.close()

def _wait_for_the_headset_hold(state_dir: Path, funestra: subprocess.Popen) -> bool:
    deadline = time.monotonic() + HEADSET_CLOSES_ITS_CHANNELS_TIMEOUT_S
    while time.monotonic() < deadline:
        if headset_is_held(state_dir):
            logger.info("The headset is holding the headset covered")
            return True
        if funestra.poll() is not None:
            release_the_headset(state_dir)
            logger.warning("The headset exited without taking the headset hold")
            return False
        time.sleep(0.1)
    release_the_headset(state_dir)
    logger.warning("The headset did not take the headset hold; closing it")
    return False


def _release_vr_runtime(was_up: bool, state_dir: Path) -> None:
    if put_the_headset_to_sleep(state_dir, crossing=False):
        logger.info("Stopped the VR runtime Fun Time woke as it opened")
        return
    if was_up:
        return
    logger.info("Stopping the VR runtime this session started")
    vr_runtime.stop_the_runtime_a_session_started(state_dir)


def set_up_logging(config) -> logging.Logger:
    session_log = configure_logging(
        __package__, config.log_file("vr_orchestrator"), console=True)
    install_exception_logging(logger)
    return session_log


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = SessionEnvironment.from_environ(os.environ)
    config = apply_this_checkouts_overrides(load_config(args.config), integration=env.integration)
    set_up_logging(config)

    _mutex_handle = claim_the_session(the_one_sessions_mutex(config))
    if _mutex_handle is None:
        logger.warning("Another Fun Time session (desktop or VR) is already running; exiting")
        tell_the_hidden_launcher_startup_resolved(config, VR_STARTUP_MARKER_NAME)
        show_already_running_message(
            "Another copy of Fun Time (desktop or VR) is already running."
        )
        return 1

    logger.info("Loaded config from %s", config.config_path)
    keep_the_transition_screen(config.paths.state_dir)
    ensure_runtime_files(config)
    forget_a_request_a_crash_left_behind(config.paths.state_dir)
    validate_config(config)
    validate_vr_config(config)
    if config.config_path == DEFAULT_CONFIG_PATH:
        stamp_the_users_pin_outside_every_checkout()
    describe_the_session_on_the_taskbar(PROJECT_DIR)

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    if no_origenerator_set_up(config, log=logger.error):
        tell_the_hidden_launcher_startup_resolved(config, VR_STARTUP_MARKER_NAME)
        return 1

    if engine_missing_abort(config, log=logger.error):
        tell_the_hidden_launcher_startup_resolved(config, VR_STARTUP_MARKER_NAME)
        return 1

    tell_the_hidden_launcher_startup_resolved(config, VR_STARTUP_MARKER_NAME)
    try:
        exit_code = run_vr_bridge(config, env, cancelable=not args.no_cancel)
    except BaseException:
        put_the_headset_to_sleep(config.paths.state_dir, crossing=False)
        raise
    hand_over_if_asked(config, logger)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
