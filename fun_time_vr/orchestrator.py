"""FunTimeVR session entry point: the desktop orchestrator, aimed at a headset.

Same config, same broker, same playlists, same dispatch loop / voice / AHK
hotkeys — the difference is what gets launched: instead of Nau, Genau and two
satellite windows, ONE VR player process (fun_time_vr.player) hosts every
visual role, and the audio companion goes to the headset's output.  Everything
else runs on the state files it always did.

What a VR session does not launch, what each control it sends reaches, and
what that waits on, is in docs/known-issues.md and
tests/test_vr_control_parity.py.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence
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
    stamp_shortcut_aumid,
    validate_config,
)
from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    NullProgress,
    PhaseProgress,
    ProgressReporter,
    StartupCancelled,
    ready_file_for,
)
from fun_time.player_status import read_nau_status
from fun_time.role_windows import ChildPids, WindowRoles
from fun_time.satellite_control import read_satellite_status
from fun_time.session_environment import SessionEnvironment
from fun_time.session_handoff import (
    DESKTOP,
    clear_handoff_request,
    drop_crossing_cover,
    hand_over_if_asked,
    headset_is_held,
    hold_the_headset,
    keep_the_crossing_cover,
    launch_crossing_cover,
    pending_handoff,
    release_the_headset,
    request_handoff,
    say_the_crossing_is_cancelled,
)
from fun_time.session_resume import (
    resume_main_video,
    resume_playlists,
    resume_satellite_locks,
    resume_shared_state,
)
from fun_time.shared_state import shared_state_path
from fun_time.voice_control import VOICE_AVAILABLE, VoiceController, voice_import_error
from fun_time.win32_process import get_process_creation_time
from fun_time.windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
)
from fun_time.windows_bridge_orchestrator import (
    ChildProcess,
    add_dispatch_file_handler,
    close_a_kept_origenerator,
    kill_recorded_child,
    open_event_log,
    silence_the_players,
    start_hud_priming,
    stop_hotkey_script,
    write_pids_file,
)
from fun_time.windows_bridge_sequencer import release_the_players
from fun_time.windows_bridge_startup import (
    ensure_broker,
    genau_project_kwargs,
    launch_audio_companion,
    reap_orphaned_satellites,
    reset_satellite_paused_states,
    seed_startup_states,
)

from . import vr_runtime
from .cover import (
    VR_SHUTDOWN_PHASES,
    VR_STARTUP_PHASES,
    scene_ready_file,
    wait_for_cover_painted,
)
from .genau_settings import GenauSettings
from .projection import is_vr_video

VR_STARTUP_MARKER_NAME = "vr_launcher.ready"
VR_PLAYER_MODULE = "fun_time_vr.player"

# How long startup waits for the VR player's first status: a cold PimaxXR
# auto-start alone may take 45s, though a healthy launch answers in seconds.
PLAYER_READY_TIMEOUT_S = 120.0

# And for the room under it: that status is a role having PICKED a video.
SCENE_READY_TIMEOUT_S = 35.0

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


def genau_clip_folders(config) -> tuple[Path, ...]:
    """Genau mode's folders in the headset: the VR clips, then the desktop's flat ones."""
    vr = (config.vr.clips_dir,) if config.vr.clips_dir else ()
    return (*vr, config.paths.clips_dir)


def main_playlist_has_vr(playlist_file: Path, vr_dirs: Sequence[Path]) -> bool:
    """Whether the main playlist holds any VR-mastered video: a desktop session's
    never does, and resumed into a headset it gives nothing but flat screens.
    A missing playlist reads as holding none."""
    return any(
        is_vr_video(video, vr_dirs) for video, _funscript in read_playlist(playlist_file)
    )


def build_vr_manifest(config, *, dashboard_enabled: bool = True) -> dict[str, dict[str, str]]:
    """The desktop manifest, amended for a VR session.

    The primary's sources swap to the VR-merged spec — every reader
    (playlist rebuilds, the file dialog default) then sees the VR rotation —
    and a ``[vr]`` section carries what only the VR player needs.
    """
    manifest = build_windows_bridge_manifest(config, dashboard_enabled=dashboard_enabled)
    manifest["media"]["nau_library_sources"] = vr_main_sources(config)
    # Which half of that merged rotation is the VR half, so the main player's
    # browse can be narrowed to one shape or the other.
    manifest["media"]["vr_library_dirs"] = "|".join(
        str(path) for path in config.vr.library_dirs)
    manifest["runtime"]["origenerator_dir"] = ""  # nothing here hosts one, so no such mode
    manifest["executables"]["origenerator_python_exe"] = ""  # nor a python to run it with
    manifest["vr"] = {
        "player_module": VR_PLAYER_MODULE,
        "library_dirs": "|".join(str(path) for path in config.vr.library_dirs),
        "tcode_udp_host": config.vr.tcode_udp_host,
        "tcode_udp_port": str(config.vr.tcode_udp_port),
        "audio_device": config.vr.audio_device or "",
        "compositor_layers": "1" if config.vr.compositor_layers else "0",
        # Genau's role: its folders and which of them hold VR masters, its
        # companion's address, and its engine's numbers off Genau's own config.
        "clips_dirs": "|".join(str(folder) for folder in genau_clip_folders(config)),
        "vr_clip_dirs": str(config.vr.clips_dir or ""),
        "notify_host": config.audio_companion.host,
        "notify_port": str(config.audio_companion.port),
        **GenauSettings.read(config.paths.genau_config_path).manifest_fields(),
    }
    return manifest


def validate_vr_config(config) -> None:
    for library_dir in config.vr.library_dirs:
        require_dir(library_dir)


def launch_vr_player(
    *, python_exe: str | Path, manifest_path: Path, log_file: Path,
    project_dirs: str | None = None,
) -> subprocess.Popen:
    """Start the process that IS this session's four players.  *project_dirs*
    has to reach it: it draws every HUD and both panels out of ``player_core``
    and ``shared_ui``, and would otherwise run the landed copies."""
    command = [str(python_exe), "-m", VR_PLAYER_MODULE, "--manifest", str(manifest_path)]
    with open_child_log(log_file, command) as log:
        return subprocess.Popen(command, stdout=log, stderr=log,
                                **genau_project_kwargs(project_dirs),
                                **hidden_subprocess_kwargs())


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


def _wait_for_player(
    status_file: Path, player: subprocess.Popen, progress: ProgressReporter | None = None,
) -> bool:
    """Until the player's first status write, or its early death, reported at
    once rather than after the timeout.  A checkpoint too: a cold headset
    spends minutes here."""
    deadline = time.monotonic() + PLAYER_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if progress is not None and progress.cancelled:
            raise StartupCancelled()
        if read_nau_status(status_file).video:
            return True
        if player.poll() is not None:
            logger.error("VR player exited during startup (code %s)", player.returncode)
            return False
        time.sleep(0.25)
    logger.error("VR player published no status within %.0fs", PLAYER_READY_TIMEOUT_S)
    return False


def _wait_for_the_room(
    marker_file: Path, player: subprocess.Popen, progress: ProgressReporter | None = None,
) -> bool:
    """Until the player says the room is up and its cover has been seen.  Never
    fatal: the player caps its own wait, so holding a launch on a missing marker
    is worse than revealing one blank screen.  A checkpoint too."""
    deadline = time.monotonic() + SCENE_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if progress is not None and progress.cancelled:
            raise StartupCancelled()
        if marker_file.exists():
            return True
        if player.poll() is not None:
            return False
        time.sleep(0.1)
    logger.warning("The room was not reported up within %.0fs; revealing anyway",
                   SCENE_READY_TIMEOUT_S)
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
    main_video: str = "",
) -> None:
    """The three playlists a VR session opens on: built fresh with nothing to
    resume, else left alone -- but a primary carried over from a desktop session
    holds no VR video, and is rebuilt from the merged sources and rotated back
    onto *main_video*, the clip that was on screen (resume_main_video).
    """
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
        logger.info(
            "Resumed playlists; rebuilt the main player's around the video it was on"
            if resume_main_video(nau_playlist, main_video)
            else "Resumed playlists; rebuilt the main player's, which held no VR video"
        )
    else:
        logger.info("Resumed last session's playlists")


# A running frame loop answers within a frame or two; a headset presenting
# nothing answers at once (player._CoverUnit.settled).
CLOSING_COVER_READY_TIMEOUT_S = 5.0

# What the player needs to break its loop and close every channel; past it the
# hold is refused and the player closed, as before.
HEADSET_HOLD_ACK_TIMEOUT_S = 15.0


class _Cover:
    """The loading cover from the orchestrator's side: the progress file the
    player reads, and the cancel flag it and the hotkey script drop."""

    def __init__(self, state_dir: Path) -> None:
        self.progress_file = state_dir / PROGRESS_FILENAME
        self.cancel_file = state_dir / CANCEL_FILENAME
        # A cancel flag left over from a previous session would abort this one
        # before the user has touched anything.
        self.cancel_file.unlink(missing_ok=True)
        self.progress: ProgressReporter = PhaseProgress(
            self.progress_file, phases=VR_STARTUP_PHASES, cancel_file=self.cancel_file,
        )

    def clear(self) -> None:
        """Drop both files -- after a DONE where a session is being revealed,
        without one where the cover goes with the player."""
        self.progress_file.unlink(missing_ok=True)
        self.cancel_file.unlink(missing_ok=True)


def _cancel_was_a_quit(cancel_file: Path) -> bool:
    try:
        return "quit" in cancel_file.read_text(encoding="utf-8").split()
    except OSError:
        return False  # the flag's own word; a crossing's exit leaves a marker too


def _cancel_vr_startup(
    *,
    state_dir: Path,
    children: dict[str, ChildProcess],
    ahk_proc: subprocess.Popen,
    ahk_cmd_file: Path,
    cover: _Cover,
    runtime_was_up: bool,
) -> int:
    """Tear down a launch the user called off, then exit.  The player goes LAST
    (it wears the cover) and the hotkey script first; then the monitors."""
    logger.info("Startup cancelled by user; tearing down %d launched child(ren)", len(children))
    quitting = _cancel_was_a_quit(cover.cancel_file)  # before cover.clear() takes it
    say_the_crossing_is_cancelled(state_dir)  # a teardown of seconds looks like nothing
    stop_hotkey_script(ahk_proc, ahk_cmd_file)
    player = children.get("vr_player_pid")
    for key, child in children.items():
        if key != "vr_player_pid":
            kill_recorded_child(child)
    if player is not None:
        kill_recorded_child(player)
    _release_vr_runtime(runtime_was_up)
    cover.clear()
    if quitting:
        logger.info("Cancelled by the quit chord; taking the monitors back")
        drop_crossing_cover(state_dir)  # nothing is coming to do it for us
    else:
        logger.info("Cancelled; handing back to Fun Time")
        request_handoff(state_dir, DESKTOP)  # it drops the cover once it is up
    return 0  # a clean, user-initiated exit, as the desktop's cancel is


@contextlib.contextmanager
def _closing_cover(
    state_dir: Path, player: subprocess.Popen, *, enabled: bool
) -> Iterator[ProgressReporter]:
    """Raise the headset's cover over the teardown, and hold the first kill for
    it.  No DONE: the cover goes when the player drawing it does, and a DONE
    would uncover a half-dismantled scene.  Off when the player is what ended."""
    if not enabled:
        yield NullProgress()
        return

    progress_file = state_dir / SHUTDOWN_PROGRESS_FILENAME
    ready_file = ready_file_for(progress_file)
    # A flag left by a previous session would let this teardown start with
    # nothing yet covering the view.
    ready_file.unlink(missing_ok=True)
    shutdown = PhaseProgress(progress_file, phases=VR_SHUTDOWN_PHASES)
    # Written before the wait so the player has something to read on its first
    # poll, and so its staleness clock starts here rather than never.
    shutdown.advance("controls")
    wait_for_cover_painted(
        ready_file,
        still_alive=lambda: player.poll() is None,
        timeout_s=CLOSING_COVER_READY_TIMEOUT_S,
    )
    try:
        yield shutdown
    finally:
        progress_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


def run_vr_bridge(config, env: SessionEnvironment) -> int:
    state_dir = config.paths.state_dir
    manifest_path = write_manifest_data(
        build_vr_manifest(config, dashboard_enabled=env.dashboard_enabled),
        state_dir / "windows_bridge_launch.ini",
    )
    open_event_log(state_dir)
    manifest = LaunchManifest.read(manifest_path)
    # The dispatch loop and voice controller log under fun_time.*, which
    # configure_logging wired up for fun_time_vr.orchestrator alone; without this
    # they reach only the event log open_event_log has just truncated.
    add_dispatch_file_handler(Path(manifest.runtime.windows_bridge_log_file))
    bridge_config = build_bridge_config_from_manifest(manifest, vr_main_player=True)
    commands = manifest.commands
    pids_file = state_dir / "bridge_pids.ini"
    ahk_cmd_file = state_dir / "ahk_cmd.txt"
    dashboard_cmd_file = Path(commands.dashboard_cmd_file)
    # Before the hotkey script reads two of these: a dead session's pids file
    # would tell it THIS session is up, taking Esc's cancel with it, and an
    # "exit" in its mailbox would be read on its first tick.
    # ...and a desktop session's press-hint port, which would take this
    # session's presses to whatever now answers there.
    for stale in (pids_file, ahk_cmd_file, dashboard_cmd_file,
                  dashboard_cmd_file.with_suffix(".processing"),
                  state_dir / "dashboard_press_port.txt"):
        stale.unlink(missing_ok=True)

    cover = _Cover(state_dir)
    progress = cover.progress
    children: dict[str, ChildProcess] = {}
    # Nothing of ours has touched the VR runtime yet, so there is nothing to put
    # back if the paths below bail before the player is launched.
    runtime_was_up = True

    # --- The hotkey script, up before the session it drives ---
    # Ahead of everything, as on the desktop: in a headset the user cannot see
    # the desktop, so a launch is only cancellable through a focus-free key.
    ahk_command = [
        str(config.paths.ahk_exe),
        str(config.project_dir / "windows_bridge_hotkeys.ahk"),
        str(manifest_path),
        str(pids_file),
    ]
    logger.info("Launching AHK hotkey script: %s", " ".join(ahk_command))
    ahk_proc = subprocess.Popen(ahk_command, cwd=config.project_dir)

    # --- The core-session bootstrap, minus the windows ---
    try:  # every ``advance`` below is a cancellation checkpoint too
        progress.advance("services")
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
        nau_status = read_nau_status(Path(commands.nau_status_file))
        resumed = resume_playlists([
            (portrait_playlist, read_satellite_status(Path(commands.portrait_status_file)).video),
            (landscape_playlist, read_satellite_status(Path(commands.landscape_status_file)).video),
            (nau_playlist, nau_status.video),
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
            main_video=nau_status.video,
        )

        # --- The children: the audio companion, then the VR player ---
        # The companion first, as on the desktop, so it is listening when Genau's
        # role says which clip is up; on the headset's output, like every sound here.
        progress.advance("companions")
        audio = launch_audio_companion(
            python_exe=manifest.executables.python_exe,
            audio_module=manifest.modules.audio_module,
            config_path=manifest.runtime.config_path,
            audio_folder=manifest.media.genau_audio,
            audio_device=config.vr.audio_device,
        )
        logger.info("Audio companion launched (pid=%d)", audio.pid)
        children["audio_pid"] = ChildProcess(
            pid=audio.pid, created_at=get_process_creation_time(audio.pid) or 0
        )

        # The long phase, and the only one the headset shows: the cover is a
        # surface of the process launched here, so it covers the rest of it.
        progress.advance("players")
        runtime_was_up = vr_runtime.runtime_was_running()  # before ensure_ready() moves it
        nau_status_file = Path(commands.nau_status_file)
        nau_status_file.unlink(missing_ok=True)
        room_ready_file = scene_ready_file(state_dir)
        room_ready_file.unlink(missing_ok=True)  # a previous session's vouches
        player = launch_vr_player(
            python_exe=manifest.executables.python_exe,
            manifest_path=manifest_path,
            log_file=state_dir / "vr_player.log",
            project_dirs=manifest.runtime.genau_project_dirs,
        )
        logger.info("VR player launched (pid=%d)", player.pid)
        children["vr_player_pid"] = ChildProcess(
            pid=player.pid, created_at=get_process_creation_time(player.pid) or 0
        )

        if not _wait_for_player(nau_status_file, player, progress):
            # The script too: one left running swallows every key it binds.
            stop_hotkey_script(ahk_proc, ahk_cmd_file)
            for child in children.values():
                kill_recorded_child(child)
            _release_vr_runtime(runtime_was_up)
            cover.clear()
            return 1
        _wait_for_the_room(room_ready_file, player, progress)

        progress.advance("finalizing")
        hud_publisher, _hud_primed = start_hud_priming(bridge_config, manifest, enabled=True)
        # Esc can land after the last checkpoint, the launch finished but the
        # flag set: do not reveal a session the user asked to abort.
        if progress.cancelled:
            raise StartupCancelled()
    except StartupCancelled:
        # The checkpoint raised before its phase ran, so *children* is exact.
        return _cancel_vr_startup(
            state_dir=state_dir, children=children, ahk_proc=ahk_proc,
            ahk_cmd_file=ahk_cmd_file, cover=cover, runtime_was_up=runtime_was_up,
        )

    # --- The reveal ---
    # DONE first, then the players: released before it, the first seconds of a
    # video play under a panel nobody can see through (release_the_players).
    # The player takes the cover down within a poll of this line.
    progress.finish()
    release_the_players(manifest, carried.main_mode)
    # The headset is showing the session: the monitors' cover can go.
    drop_crossing_cover(state_dir)
    # Records the children for teardown and hands the keyboard over: the hotkey
    # script takes its startup hold off, so Esc now means omnipause.
    write_pids_file(pids_file, children)
    cover.clear()
    room_ready_file.unlink(missing_ok=True)

    dispatch_runner = DispatchLoopRunner(
        config=bridge_config,
        dashboard_cmd_file=dashboard_cmd_file,
        shared_state_file=shared_state_path(state_dir),
        ahk_cmd_file=ahk_cmd_file,
        # Every role pid stays 0: the roles are surfaces of the VR player, and
        # unresolved HWNDs are what make the window ops no-ops.
        windows=WindowRoles(pids=ChildPids()),
        # There IS a dashboard now, hanging in the scene; this publishes what
        # its bar reads.
        dashboard_enabled=True,
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
        voice_controller.active_side = lambda: dispatch_runner.state.active_side
        voice_thread = threading.Thread(target=voice_controller.run, daemon=True, name="voice-control")
        voice_thread.start()
        logger.info("Voice control thread launched")
    elif config.voice_control.enabled:
        logger.warning("Voice control enabled but import failed: %s", voice_import_error())

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
        silence_the_players(commands)
        # This teardown's cover hangs in the headset (docs/entering-vr.md).
        held = False
        if (crossing := pending_handoff(state_dir)) is not None:
            launch_crossing_cover(state_dir, crossing)
        # Up first and up through everything below.  A session that ended
        # BECAUSE the player went has nothing left to draw with, and nothing to
        # hide: the cut to the runtime's environment already came.
        with _closing_cover(state_dir, player, enabled=player.poll() is None) as shutdown:
            if voice_controller is not None:
                voice_controller.stop()
            if voice_thread is not None:
                voice_thread.join(timeout=2.0)
            dispatch_runner.stop()
            dispatch_thread.join(timeout=2.0)
            shutdown.advance("companions")
            kill_recorded_child(children["audio_pid"])
            shutdown.advance("players")
            # Held, the player outlives this session with only its cover left.
            held = crossing is not None and _leave_the_headset_covered(
                state_dir, stop_runtime=not runtime_was_up,
            )
            if not held:
                kill_recorded_child(children["vr_player_pid"])  # last: it wears the cover
            if crossing is None:  # nothing is crossing in to adopt a parked one
                close_a_kept_origenerator(state_dir)
        if not held:
            _release_vr_runtime(runtime_was_up)  # after the player: it held an XR session
    return exit_code


def _leave_the_headset_covered(state_dir: Path, *, stop_runtime: bool) -> bool:
    """Ask the player to hold its cover and let go of every channel."""
    hold_the_headset(state_dir, stop_runtime=stop_runtime)
    deadline = time.monotonic() + HEADSET_HOLD_ACK_TIMEOUT_S
    while time.monotonic() < deadline:
        if headset_is_held(state_dir):
            logger.info("The VR player is holding the headset covered")
            return True
        time.sleep(0.1)
    release_the_headset(state_dir)
    logger.warning("The VR player did not take the headset hold; closing it")
    return False


def _release_vr_runtime(was_up: bool) -> None:
    if was_up:
        return
    logger.info("Stopping the VR runtime this session started")
    vr_runtime.stop_runtime()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = SessionEnvironment.from_environ(os.environ)
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
    keep_the_crossing_cover(config.paths.state_dir)  # a crossing at either end
    ensure_runtime_files(config)
    clear_handoff_request(config.paths.state_dir)
    validate_config(config)
    validate_vr_config(config)
    # One app, one button: a VR session lights Fun Time's.  Only a session on
    # the installed config relabels the pin -- the desktop's rule.
    if config.config_path == DEFAULT_CONFIG_PATH:
        stamp_shortcut_aumid()

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    signal_startup_resolved(config, VR_STARTUP_MARKER_NAME)
    exit_code = run_vr_bridge(config, env)
    hand_over_if_asked(config, logger)  # last: the relay waits on this process's mutex
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
