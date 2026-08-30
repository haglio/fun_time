"""FunTimeVR session entry point: the desktop orchestrator, aimed at a headset.

Same config, same broker, same playlists, same dispatch loop / voice / AHK
hotkeys — the difference is what gets launched: instead of Nau, Genau and two
satellite windows, ONE VR player process (fun_time_vr.player) hosts all the
visual roles, and the desktop-window management goes unused (the dispatch
loop's window ops resolve no HWNDs and settle into no-ops).  Everything else
the session does — omnipause, watch stats, the hybrid arbiter's status files,
F-mode rebuilds — runs on the same state files it always did.

What a VR session does not launch, and what that waits on, is in
docs/known-issues.md.
"""
from __future__ import annotations

import argparse
import configparser
import logging
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.branch_session import apply_genau_dirs_to_sys_path

# Before anything that reaches the dispatch loop: a worktree's
# genau_project_dirs override reaches Genau and Nau as subprocess PYTHONPATH,
# but a launcher's own process resolves player_core through the venv, which is
# the primary's.  (Every entry point needs it — see CLAUDE.md, "Standing
# rules".)
apply_genau_dirs_to_sys_path()

from fun_time.broker_control import PARK_CMD, write_broker_command
from fun_time.child_log import open_child_log
from fun_time.config import load_config
from fun_time.dashboard_runtime import read_nau_status
from fun_time.manifest import build_windows_bridge_manifest, write_manifest_data
from fun_time.modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_NAU,
    PLAYLIST_PORTRAIT,
    SatelliteLibraryContext,
    build_all_playlists,
    build_playlist_file_path,
    build_main_playlist,
)
from fun_time.orchestrator import (
    ensure_broker_running,
    ensure_runtime_files,
    require_dir,
    signal_startup_resolved,
    validate_config,
)
from fun_time.mode_plan import STARTUP_MAIN_MODE
from fun_time.role_windows import ChildPids, WindowRoles
from fun_time.satellite_control import read_satellite_status
from fun_time.session_resume import (
    resume_playlists,
    resume_satellite_locks,
    resume_shared_state,
)
from fun_time.shared_state import shared_state_path, write_shared_state
from fun_time.voice_control import VOICE_AVAILABLE, VoiceController, _VOICE_IMPORT_ERROR
from fun_time.watch_stats import watch_stats_path
from fun_time.windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from fun_time.windows_bridge_orchestrator import (
    ChildProcess,
    _open_event_log,
    _start_hud_priming,
    kill_recorded_child,
    write_pids_file,
)
from fun_time.windows_bridge_startup import (
    ensure_broker,
    reap_orphaned_satellites,
    reset_satellite_paused_states,
    seed_startup_states,
)
from fun_time.win32 import get_process_creation_time
from fun_time.runtime_flow import write_flag_file
from player_core.playlist import read_playlist

from .projection import is_vr_video

VR_STARTUP_MARKER_NAME = "vr_launcher.ready"
VR_PLAYER_MODULE = "fun_time_vr.player"

# How long startup waits for the VR player to publish its first status.  It
# has real work behind it — a cold PimaxXR auto-start alone may take 45s, then
# the OpenXR session and three mpv players come up — so the ceiling is wide.
# The player writes status from its very first pump, well before the headset
# turns visible, so a healthy launch answers in seconds.
PLAYER_READY_TIMEOUT_S = 120.0

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the FunTimeVR session.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--check", action="store_true", help="Validate config and exit.")
    return parser


def vr_main_sources(config) -> str:
    """The main rotation's source spec: the VR library joined with the
    desktop primary's own dirs — the user's "VR videos and non-VR videos"."""
    dirs = [*config.vr.library_dirs, *config.paths.nau_library_dirs]
    return "|".join(str(path) for path in dirs)


def main_playlist_has_vr(playlist_file: Path, vr_dirs: Sequence[Path]) -> bool:
    """Whether the main player's playlist holds any VR-mastered video at all.

    A desktop session's main playlist never does — it was built from the 2D
    library alone — and resuming it into a VR session gives a headset nothing
    but flat screens until something rebuilds.  That is exactly what the first
    headset run got, so the VR session asks this before honoring a resume.  A
    missing playlist reads as empty, and so as holding none.
    """
    return any(
        is_vr_video(video, vr_dirs) for video, _funscript in read_playlist(playlist_file)
    )


def resume_vr_state(state_file: Path, *, resumed: bool):
    """The state a VR session comes back in: last session's, minus its mode.

    One player hosts every role here and Genau is not launched at all, so there
    is no main slot to hand over and nobody to hand it to — while the desktop
    session, which shares this state dir, can perfectly well have been closed in
    genau mode.  Carried across, that mode would put every VR HUD, and the
    dispatch loop's whole idea of who owns the display, on a player that is not
    running.  Everything else comes across untouched: both apps play the same
    playlists with the same F-mode, filters, locks and sound.

    Written back rather than merely corrected in hand, because the state file is
    what the dispatch loop and the HUDs actually read.
    """
    carried = replace(
        resume_shared_state(state_file, resumed=resumed),
        main_mode=STARTUP_MAIN_MODE,
    )
    write_shared_state(state_file, carried)
    return carried


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
    """Block until the AHK bridge or the VR player exits; name which went.

    On the desktop the AHK bridge's exit is the session's one natural end.
    In VR the player window's close button is a quit gesture too — it is the
    only window the session has — and an orchestrator that kept waiting on
    AHK after the player died held the single-instance mutex with nothing
    left to orchestrate, so every relaunch bounced off "already running".
    """
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


def run_vr_bridge(config, logger_) -> int:
    state_dir = config.paths.state_dir
    manifest_path = write_manifest_data(
        build_vr_manifest(config), state_dir / "windows_bridge_launch.ini"
    )
    _open_event_log(state_dir)
    manifest = configparser.ConfigParser()
    manifest.optionxform = str
    manifest.read(str(manifest_path), encoding="utf-8")
    bridge_config = build_bridge_config_from_manifest(manifest)
    commands = manifest["commands"]

    # --- The core-session bootstrap, minus the windows ---
    write_broker_command(Path(commands["broker_cmd_file"]), PARK_CMD)
    ensure_broker(
        commands["broker_heartbeat_file"],
        Path(v) if (v := commands.get("broker_tray_launcher", "").strip()) else None,
    )
    reset_satellite_paused_states(
        commands["portrait_paused_file"], commands["landscape_paused_file"],
    )
    # A desktop session's stranded satellites hold the same files this session
    # is claiming; so would a stranded VR player (matched by the manifest on
    # its command line).
    reap_orphaned_satellites(
        manifest["modules"]["satellite_module"],
        [commands["portrait_status_file"], commands["landscape_status_file"]],
    )
    reap_orphaned_satellites(VR_PLAYER_MODULE, [str(manifest_path)])

    portrait_playlist = build_playlist_file_path(state_dir, PLAYLIST_PORTRAIT)
    landscape_playlist = build_playlist_file_path(state_dir, PLAYLIST_LANDSCAPE)
    nau_playlist = build_playlist_file_path(state_dir, PLAYLIST_NAU)
    resumed = resume_playlists([
        (portrait_playlist, read_satellite_status(Path(commands["portrait_status_file"])).video),
        (landscape_playlist, read_satellite_status(Path(commands["landscape_status_file"])).video),
        (nau_playlist, read_nau_status(Path(commands["nau_status_file"])).video),
    ])
    # And the state that session was in: F-mode, each side's filter, order and
    # lock, any group loop, the sound level.  The dispatch loop opens on this
    # file, so resuming the files without it leaves every HUD describing a
    # different session.  Read before the flags below are seeded, because two of
    # them are what those flags have to be seeded to.
    carried = resume_vr_state(shared_state_path(state_dir), resumed=resumed)
    seed_startup_states(
        commands["genau_paused_file"], commands["audio_paused_file"],
        commands["nau_paused_file"], commands["audio_volume_file"],
        commands["genau_cmd_file"], nau_cmd_file=commands["nau_cmd_file"],
        volume=carried.volume, muted=carried.muted, f_mode=carried.main_f_mode,
    )
    # A lock lives in the player process, so it has to be re-sent; the roles read
    # the satellites' own command files, and the VR player is not up yet.
    resume_satellite_locks([
        (Path(commands["portrait_cmd_file"]), carried.locked2),
        (Path(commands["landscape_cmd_file"]), carried.locked3),
    ])
    if not resumed:
        build_all_playlists(
            main_sources=manifest["media"]["nau_library_sources"],
            portrait_sources=manifest["media"]["portrait_dirs"],
            landscape_sources=manifest["media"]["landscape_dirs"],
            favs_file=Path(manifest["media"]["favs_file"]),
            state_dir=state_dir,
            library=SatelliteLibraryContext(
                metadata_root=bridge_config.regen_metadata_root,
                watch_stats_file=watch_stats_path(state_dir),
            ),
        )
    elif not main_playlist_has_vr(nau_playlist, config.vr.library_dirs):
        # Resumed from a desktop session, whose primary playlist is 2D only:
        # keep the satellites where they were, but rebuild the primary from the
        # VR-merged sources so a headset session actually gets VR videos.  Under
        # the order it is coming back in, like its F-mode: the state carried
        # forward has to describe the file this writes, not the one it replaced.
        build_main_playlist(
            nau_playlist, manifest["media"]["nau_library_sources"],
            f_mode=carried.main_f_mode, recent=carried.main_latest,
        )
        logger_.info("Resumed playlists; rebuilt the main player's, which held no VR video")
    logger_.info(
        "Resumed last session's playlists" if resumed else "Nothing to resume; built fresh playlists"
    )

    # --- The one child: the VR player ---
    nau_status_file = Path(commands["nau_status_file"])
    nau_status_file.unlink(missing_ok=True)
    player = launch_vr_player(
        python_exe=manifest["executables"]["python_exe"],
        manifest_path=manifest_path,
        log_file=state_dir / "vr_player.log",
    )
    logger_.info("VR player launched (pid=%d)", player.pid)
    children = {
        "vr_player_pid": ChildProcess(
            pid=player.pid, created_at=get_process_creation_time(player.pid) or 0
        )
    }
    write_pids_file(state_dir / "bridge_pids.ini", children)

    if not _wait_for_player(nau_status_file, player):
        kill_recorded_child(children["vr_player_pid"])
        return 1
    # The reveal: playback starts the moment the player is up.
    write_flag_file(commands["nau_paused_file"], False)

    # Command files only: the shared state file already holds this session's
    # opening state, written above with whatever the resumed playlists were
    # built under, and deleting it here would drop all of it back to defaults.
    dashboard_cmd_file = Path(commands["dashboard_cmd_file"])
    for stale in (
        state_dir / "ahk_cmd.txt",
        dashboard_cmd_file,
        dashboard_cmd_file.with_suffix(".processing"),
    ):
        stale.unlink(missing_ok=True)

    hud_publisher, _hud_primed = _start_hud_priming(bridge_config, manifest, enabled=True)
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
        logger_.info("Voice control thread launched")
    elif config.voice_control.enabled:
        logger_.warning("Voice control enabled but import failed: %s", _VOICE_IMPORT_ERROR)

    command = [
        str(config.paths.ahk_exe),
        str(config.project_dir / "windows_bridge_hotkeys.ahk"),
        str(manifest_path),
        str(state_dir / "bridge_pids.ini"),
    ]
    logger_.info("Launching AHK hotkey script: %s", " ".join(command))
    ahk_proc = subprocess.Popen(command, cwd=config.project_dir)

    try:
        ended_by = _wait_for_session_end(ahk_proc, player)
        if ended_by == "player":
            logger_.info("VR player exited -- ending the session")
            ahk_proc.terminate()
            ahk_proc.wait()
            exit_code = 0
        else:
            logger_.info("AHK exited -- ending the session")
            exit_code = ahk_proc.wait()
    except KeyboardInterrupt:
        logger_.info("Interrupted -- shutting down")
        exit_code = 1
    finally:
        if voice_controller is not None:
            voice_controller.stop()
        if voice_thread is not None:
            voice_thread.join(timeout=2.0)
        dispatch_runner.stop()
        dispatch_thread.join(timeout=2.0)
        kill_recorded_child(children["vr_player_pid"])
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logger_ = configure_logging(
        "fun_time_vr.orchestrator", config.log_file("vr_orchestrator"), console=True
    )
    install_exception_logging(logger_)

    from fun_time.single_instance import (  # mirrors fun_time.orchestrator.main
        MUTEX_ORCHESTRATOR,
        mutex_name_for_config,
        show_already_running_message,
        try_acquire_mutex,
    )

    # The SAME mutex as the desktop session: both drive the same state files
    # and the same players' channels, so they must never run together.
    _mutex_handle = try_acquire_mutex(mutex_name_for_config(MUTEX_ORCHESTRATOR, config.instance_id))
    if _mutex_handle is None:
        logger_.warning("Another Fun Time session (desktop or VR) is already running; exiting")
        signal_startup_resolved(config, VR_STARTUP_MARKER_NAME)
        show_already_running_message(
            "Another copy of Fun Time (desktop or VR) is already running."
        )
        return 1

    logger_.info("Loaded config from %s", config.config_path)
    ensure_runtime_files(config)
    validate_config(config)
    validate_vr_config(config)

    if args.check:
        logger_.info("Config validation succeeded")
        return 0

    signal_startup_resolved(config, VR_STARTUP_MARKER_NAME)
    ensure_broker_running(config, logger_)
    return run_vr_bridge(config, logger_)


if __name__ == "__main__":
    raise SystemExit(main())
