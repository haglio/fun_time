"""FunTimeVR session entry point: the desktop orchestrator, aimed at a headset.

Same config, same broker, same playlists, same dispatch loop / voice / AHK
hotkeys — the difference is what gets launched: instead of Nau, Genau and two
satellite windows, ONE VR player process (fun_time_vr.player) hosts all the
visual roles — the main player, Genau, both satellites — and the
desktop-window management goes unused (the dispatch loop's window ops resolve
no HWNDs and settle into no-ops).  The audio companion is launched as on the
desktop, sent to the headset's output.  Everything else the session does —
the two main-slot modes, omnipause, watch stats, the device arbiter's status
files, F-mode rebuilds — runs on the same state files it always did.

What a VR session does not launch, and what that waits on, is in
docs/known-issues.md.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import set_shortcut_app_user_model_id

from fun_time.branch_session import apply_genau_dirs_to_sys_path

# Before anything that reaches the dispatch loop: a worktree's
# genau_project_dirs override reaches Genau and Nau as subprocess PYTHONPATH,
# but a launcher's own process resolves player_core through the venv, which is
# the primary's.  (Every entry point needs it — see CLAUDE.md, "Standing
# rules".)
apply_genau_dirs_to_sys_path()

from player_core.playlist import read_playlist

from fun_time.broker_control import PARK_CMD, write_broker_command
from fun_time.child_log import open_child_log
from fun_time.config import DEFAULT_CONFIG_PATH, load_config
from fun_time.manifest import (
    LaunchManifest,
    build_windows_bridge_manifest,
    write_manifest_data,
)
from fun_time.modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_NAU,
    PLAYLIST_PORTRAIT,
    SatelliteBuild,
    build_all_playlists,
    build_main_playlist,
    build_playlist_file_path,
)
from fun_time.orchestrator import (
    ensure_runtime_files,
    require_dir,
    signal_startup_resolved,
    taskbar_pin_dir,
    validate_config,
)
from fun_time.player_status import read_nau_status
from fun_time.role_windows import ChildPids, WindowRoles
from fun_time.satellite_control import read_satellite_status
from fun_time.session_resume import (
    resume_playlists,
    resume_satellite_locks,
    resume_shared_state,
)
from fun_time.shared_state import shared_state_path
from fun_time.voice_control import VOICE_AVAILABLE, VoiceController, voice_import_error
from fun_time.win32_process import get_process_creation_time
from fun_time.win32_taskbar import VR_APP_USER_MODEL_ID
from fun_time.windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from fun_time.windows_bridge_orchestrator import (
    ChildProcess,
    kill_recorded_child,
    open_event_log,
    start_hud_priming,
    write_pids_file,
)
from fun_time.windows_bridge_sequencer import release_the_players
from fun_time.windows_bridge_startup import (
    ensure_broker,
    launch_audio_companion,
    reap_orphaned_satellites,
    reset_satellite_paused_states,
    seed_startup_states,
)

from . import vr_runtime
from .genau_settings import GenauSettings
from .projection import is_vr_video

VR_STARTUP_MARKER_NAME = "vr_launcher.ready"
VR_PLAYER_MODULE = "fun_time_vr.player"

# How long startup waits for the VR player's first status: a cold PimaxXR
# auto-start alone may take 45s, though a healthy launch answers in seconds.
PLAYER_READY_TIMEOUT_S = 120.0

# Named, not __name__: started with `-m`, where __name__ is "__main__".
logger = logging.getLogger("fun_time_vr.orchestrator")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the FunTimeVR session.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--check", action="store_true", help="Validate config and exit.")
    return parser


def vr_main_sources(config) -> str:
    """The main rotation's sources: the VR library, then the desktop primary's dirs."""
    dirs = [*config.vr.library_dirs, *config.paths.nau_library_dirs]
    return "|".join(str(path) for path in dirs)


def main_playlist_has_vr(playlist_file: Path, vr_dirs: Sequence[Path]) -> bool:
    """Whether the main playlist holds any VR-mastered video: a desktop session's
    never does, and resumed into a headset it gives nothing but flat screens.
    A missing playlist reads as holding none."""
    return any(
        is_vr_video(video, vr_dirs) for video, _funscript in read_playlist(playlist_file)
    )


def is_vr_pin(stem: str) -> bool:
    """"Fun Time VR" and its copies -- never the desktop's "Fun Time", another app."""
    return stem.strip().lower().startswith("fun time vr")


def stamp_vr_shortcut_aumid() -> None:
    """Stamp the VR session's identity on its pin, so the VR player's window
    lights that button; logged and never fatal when it cannot."""
    pin_dir = taskbar_pin_dir()
    if not pin_dir.is_dir():
        return
    for lnk in pin_dir.glob("*.lnk"):
        if not is_vr_pin(lnk.stem):
            continue
        try:
            set_shortcut_app_user_model_id(str(lnk), VR_APP_USER_MODEL_ID)
            logger.info("Stamped AppUserModelID on %s", lnk)
        except OSError as exc:
            logger.warning("Could not stamp AppUserModelID on %s: %s", lnk, exc)


def build_vr_manifest(config) -> dict[str, dict[str, str]]:
    """The desktop manifest, amended for a VR session.

    The primary's sources swap to the VR-merged spec — every reader
    (playlist rebuilds, the file dialog default) then sees the VR rotation —
    and a ``[vr]`` section carries what only the VR player needs.
    """
    manifest = build_windows_bridge_manifest(config)
    manifest["media"]["nau_library_sources"] = vr_main_sources(config)
    manifest["vr"] = {
        "player_module": VR_PLAYER_MODULE,
        "library_dirs": "|".join(str(path) for path in config.vr.library_dirs),
        "tcode_udp_host": config.vr.tcode_udp_host,
        "tcode_udp_port": str(config.vr.tcode_udp_port),
        "audio_device": config.vr.audio_device or "",
        "compositor_layers": "1" if config.vr.compositor_layers else "0",
        # Genau's role: its folder (the desktop's when no VR one is named), its
        # companion's address, and its engine's numbers off Genau's own config.
        "clips_dir": str(config.vr.clips_dir or config.paths.clips_dir),
        "notify_host": config.audio_companion.host,
        "notify_port": str(config.audio_companion.port),
        **GenauSettings.read(config.paths.genau_config_path).manifest_fields(),
    }
    return manifest


def validate_vr_config(config) -> None:
    for library_dir in config.vr.library_dirs:
        require_dir(library_dir)


def launch_vr_player(
    *, python_exe: str | Path, manifest_path: Path, log_file: Path
) -> subprocess.Popen:
    command = [str(python_exe), "-m", VR_PLAYER_MODULE, "--manifest", str(manifest_path)]
    with open_child_log(log_file, command) as log:
        return subprocess.Popen(command, stdout=log, stderr=log, **hidden_subprocess_kwargs())


def _wait_for_session_end(ahk_proc, player, *, poll_s: float = 0.5) -> str:
    """Block until the AHK bridge or the VR player exits; name which went.  The
    player's close button is a quit gesture too, and waiting on AHK past it
    held the single-instance mutex with nothing left to orchestrate."""
    while True:
        if ahk_proc.poll() is not None:
            return "ahk"
        if player.poll() is not None:
            return "player"
        time.sleep(poll_s)


def _wait_for_player(status_file: Path, player: subprocess.Popen) -> bool:
    """Until the player's first status write — or its early death, reported at
    once rather than after the full timeout."""
    deadline = time.monotonic() + PLAYER_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if read_nau_status(status_file).video:
            return True
        if player.poll() is not None:
            logger.error("VR player exited during startup (code %s)", player.returncode)
            return False
        time.sleep(0.25)
    logger.error("VR player published no status within %.0fs", PLAYER_READY_TIMEOUT_S)
    return False


def stock_the_playlists(
    manifest,
    *,
    state_dir: Path,
    metadata_root: Path,
    vr_library_dirs,
    resumed: bool,
    main_f_mode: bool,
    main_recent: bool,
) -> None:
    """The three playlists a VR session opens on: built fresh with nothing to
    resume, else left alone -- but a primary carried over from a desktop session
    holds no VR video and is rebuilt from the merged sources."""
    nau_playlist = build_playlist_file_path(state_dir, PLAYLIST_NAU)
    if not resumed:
        build_all_playlists(
            main_sources=manifest.media.nau_library_sources,
            portrait=SatelliteBuild(sources=manifest.media.portrait_dirs),
            landscape=SatelliteBuild(sources=manifest.media.landscape_dirs),
            favs_file=Path(manifest.media.favs_file),
            state_dir=state_dir,
            metadata_root=metadata_root,
        )
        logger.info("Nothing to resume; built fresh playlists")
        return
    if not main_playlist_has_vr(nau_playlist, vr_library_dirs):
        build_main_playlist(
            nau_playlist, manifest.media.nau_library_sources,
            f_mode=main_f_mode, recent=main_recent,
        )
        logger.info("Resumed playlists; rebuilt the main player's, which held no VR video")
    else:
        logger.info("Resumed last session's playlists")


def run_vr_bridge(config) -> int:
    state_dir = config.paths.state_dir
    manifest_path = write_manifest_data(
        build_vr_manifest(config), state_dir / "windows_bridge_launch.ini"
    )
    open_event_log(state_dir)
    manifest = LaunchManifest.read(manifest_path)
    bridge_config = build_bridge_config_from_manifest(manifest, vr_main_player=True)
    commands = manifest.commands

    # --- The core-session bootstrap, minus the windows ---
    write_broker_command(Path(commands.broker_cmd_file), PARK_CMD)
    ensure_broker(
        commands.broker_heartbeat_file,
        Path(v) if (v := commands.broker_tray_launcher.strip()) else None,
    )
    reset_satellite_paused_states(
        commands.portrait_paused_file, commands.landscape_paused_file,
    )
    # A desktop session's stranded satellites hold the same files this session
    # is claiming; so would a stranded VR player (matched by the manifest on
    # its command line).
    reap_orphaned_satellites(
        manifest.modules.satellite_module,
        [commands.portrait_status_file, commands.landscape_status_file],
    )
    reap_orphaned_satellites(VR_PLAYER_MODULE, [str(manifest_path)])

    portrait_playlist = build_playlist_file_path(state_dir, PLAYLIST_PORTRAIT)
    landscape_playlist = build_playlist_file_path(state_dir, PLAYLIST_LANDSCAPE)
    nau_playlist = build_playlist_file_path(state_dir, PLAYLIST_NAU)
    resumed = resume_playlists([
        (portrait_playlist, read_satellite_status(Path(commands.portrait_status_file)).video),
        (landscape_playlist, read_satellite_status(Path(commands.landscape_status_file)).video),
        (nau_playlist, read_nau_status(Path(commands.nau_status_file)).video),
    ])
    # And the state that session was in: F-mode, each side's filter, order and
    # lock, any group loop, the sound level.  The dispatch loop opens on this
    # file, so resuming the files without it leaves every HUD describing a
    # different session.  Read before the flags below are seeded, because two of
    # them are what those flags have to be seeded to.
    carried = resume_shared_state(shared_state_path(state_dir), resumed=resumed)
    # The mode comes across with the rest: the VR player hosts Genau too.
    seed_startup_states(
        commands.genau_paused_file, commands.audio_paused_file,
        commands.nau_paused_file, commands.audio_volume_file,
        commands.genau_cmd_file, nau_cmd_file=commands.nau_cmd_file,
        volume=carried.volume, muted=carried.muted, f_mode=carried.main_f_mode,
        mode=carried.main_mode,
    )
    # A lock lives in the player process, so it has to be re-sent; the roles read
    # the satellites' own command files, and the VR player is not up yet.
    resume_satellite_locks([
        (Path(commands.portrait_cmd_file), carried.locked2),
        (Path(commands.landscape_cmd_file), carried.locked3),
    ])
    stock_the_playlists(
        manifest,
        state_dir=state_dir,
        metadata_root=bridge_config.regen_metadata_root,
        vr_library_dirs=config.vr.library_dirs,
        resumed=resumed,
        main_f_mode=carried.main_f_mode,
        main_recent=carried.main_latest,
    )

    # --- The children: the audio companion, then the VR player ---
    # The companion first, as on the desktop, so it is listening when Genau's
    # role says which clip is up; on the headset's output, like every sound here.
    audio = launch_audio_companion(
        python_exe=manifest.executables.python_exe,
        audio_module=manifest.modules.audio_module,
        config_path=manifest.runtime.config_path,
        audio_folder=manifest.media.genau_audio,
        audio_device=config.vr.audio_device,
    )
    logger.info("Audio companion launched (pid=%d)", audio.pid)
    children = {
        "audio_pid": ChildProcess(
            pid=audio.pid, created_at=get_process_creation_time(audio.pid) or 0
        ),
    }
    runtime_was_up = vr_runtime.runtime_was_running()  # before ensure_ready() moves it
    nau_status_file = Path(commands.nau_status_file)
    nau_status_file.unlink(missing_ok=True)
    player = launch_vr_player(
        python_exe=manifest.executables.python_exe,
        manifest_path=manifest_path,
        log_file=state_dir / "vr_player.log",
    )
    logger.info("VR player launched (pid=%d)", player.pid)
    children["vr_player_pid"] = ChildProcess(
        pid=player.pid, created_at=get_process_creation_time(player.pid) or 0
    )
    write_pids_file(state_dir / "bridge_pids.ini", children)

    if not _wait_for_player(nau_status_file, player):
        for child in children.values():
            kill_recorded_child(child)
        _release_vr_runtime(runtime_was_up)
        return 1
    # The reveal: whatever the mode puts to work starts the moment the player is up.
    release_the_players(manifest, carried.main_mode)

    # Command files only: the shared state file already holds this session's
    # opening state, written above with whatever the resumed playlists were
    # built under, and deleting it here would drop all of it back to defaults.
    dashboard_cmd_file = Path(commands.dashboard_cmd_file)
    for stale in (
        state_dir / "ahk_cmd.txt",
        dashboard_cmd_file,
        dashboard_cmd_file.with_suffix(".processing"),
    ):
        stale.unlink(missing_ok=True)

    hud_publisher, _hud_primed = start_hud_priming(bridge_config, manifest, enabled=True)
    dispatch_runner = DispatchLoopRunner(
        config=bridge_config,
        dashboard_cmd_file=dashboard_cmd_file,
        shared_state_file=shared_state_path(state_dir),
        ahk_cmd_file=state_dir / "ahk_cmd.txt",
        # Every role pid stays 0: the roles live inside the VR player, there
        # are no per-role windows, and unresolved HWNDs are exactly what makes
        # the desktop window ops settle into no-ops.
        windows=WindowRoles(pids=ChildPids()),
        dashboard_enabled=False,
        hud_publisher=hud_publisher,
    )
    dispatch_thread = threading.Thread(target=dispatch_runner.run, daemon=True, name="dispatch-loop")
    dispatch_thread.start()

    voice_controller: VoiceController | None = None
    voice_thread: threading.Thread | None = None
    if VOICE_AVAILABLE and config.voice_control.enabled:
        voice_controller = VoiceController(
            cmd_file=dashboard_cmd_file,
            model_path=config.voice_control.model_path,
            confidence_threshold=config.voice_control.confidence_threshold,
            device_name=config.voice_control.device_name,
            sample_rate=config.voice_control.sample_rate,
        )
        dispatch_runner.voice_controller = voice_controller
        voice_thread = threading.Thread(target=voice_controller.run, daemon=True, name="voice-control")
        voice_thread.start()
        logger.info("Voice control thread launched")
    elif config.voice_control.enabled:
        logger.warning("Voice control enabled but import failed: %s", voice_import_error())

    command = [
        str(config.paths.ahk_exe),
        str(config.project_dir / "windows_bridge_hotkeys.ahk"),
        str(manifest_path),
        str(state_dir / "bridge_pids.ini"),
    ]
    logger.info("Launching AHK hotkey script: %s", " ".join(command))
    ahk_proc = subprocess.Popen(command, cwd=config.project_dir)

    try:
        ended_by = _wait_for_session_end(ahk_proc, player)
        if ended_by == "player":
            logger.info("VR player exited -- ending the session")
            ahk_proc.terminate()
            ahk_proc.wait()
            exit_code = 0
        else:
            logger.info("AHK exited -- ending the session")
            exit_code = ahk_proc.wait()
    except KeyboardInterrupt:
        logger.info("Interrupted -- shutting down")
        exit_code = 1
    finally:
        if voice_controller is not None:
            voice_controller.stop()
        if voice_thread is not None:
            voice_thread.join(timeout=2.0)
        dispatch_runner.stop()
        dispatch_thread.join(timeout=2.0)
        for child in children.values():
            kill_recorded_child(child)
        _release_vr_runtime(runtime_was_up)  # after the player: it held an XR session
    return exit_code


def _release_vr_runtime(was_up: bool) -> None:
    if was_up:
        return
    logger.info("Stopping the VR runtime this session started")
    vr_runtime.stop_runtime()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    configure_logging(logger.name, config.log_file("vr_orchestrator"), console=True)
    install_exception_logging(logger)

    # Mirrors fun_time.orchestrator.main.
    from app_support.win32 import mutex_name, try_acquire_mutex

    from fun_time.single_instance import MUTEX_ORCHESTRATOR, show_already_running_message

    # The SAME mutex as the desktop session: both drive the same state files
    # and the same players' channels, so they must never run together.
    _mutex_handle = try_acquire_mutex(mutex_name(MUTEX_ORCHESTRATOR, config.instance_id))
    if _mutex_handle is None:
        logger.warning("Another Fun Time session (desktop or VR) is already running; exiting")
        signal_startup_resolved(config, VR_STARTUP_MARKER_NAME)
        show_already_running_message(
            "Another copy of Fun Time (desktop or VR) is already running."
        )
        return 1

    logger.info("Loaded config from %s", config.config_path)
    ensure_runtime_files(config)
    validate_config(config)
    validate_vr_config(config)
    # Only the session the pin launches relabels it -- the desktop's rule.
    if config.config_path == DEFAULT_CONFIG_PATH:
        stamp_vr_shortcut_aumid()

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    signal_startup_resolved(config, VR_STARTUP_MARKER_NAME)
    return run_vr_bridge(config)


if __name__ == "__main__":
    raise SystemExit(main())
