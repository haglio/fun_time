"""Genau's role in the VR process: the clip player's contract, in-process.

The same engine the desktop window runs (:mod:`player_core.genau_refresh` and
what it composes), on the same file channel, so nothing that drives Genau
knows the display changed.  What a headset needs that a window supplied is
the little kept here: the frame the engine chose, the clip's projection,
whether the clip has the scene, and the console the engine composed.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from player_core.broker_feed import BrokerFeed, udp_reader
from player_core.clip_advance import ClipAdvanceState
from player_core.clip_cache import ClipCacheStore, DecodeRequestState
from player_core.clip_decode import load_clip_frames
from player_core.clip_folder import (
    cache_dir_for_clips_folder,
    move_clip_to_weird,
    scan_clips,
    weird_dir_for_clips_folder,
)
from player_core.clip_loader import ClipLoadController
from player_core.clip_renderer import ClipRenderController
from player_core.clip_selection import ClipSelectionController
from player_core.clip_sequence import ClipSequenceController
from player_core.cruise_control import CruiseControlState
from player_core.file_channel import read_paused_state
from player_core.flag import Flag
from player_core.genau_controls import GenauControls
from player_core.genau_refresh import GenauRefreshController
from player_core.robot_hand import RobotHandState, bpm_for_speed
from player_core.robot_hand_beat import BeatEngine
from player_core.robot_hand_driver import RobotHandTCodeDriver

from .genau_settings import GenauSettings
from .projection import default_projection

logger = logging.getLogger(__name__)

# The desktop window's rate: a slower tick shows fewer of the clip's frames per cycle.
TICK_HZ = 120.0


class GenauRole:
    def __init__(
        self,
        *,
        clips_dirs: Sequence[Path],
        settings: GenauSettings,
        vr_dirs: Sequence[Path] = (),
        command_file: Path,
        paused_file: Path,
        drive_file: Path,
        console_file: Path | None,
        notifier,
        tcode_sink,
        stop_event: threading.Event,
        start_clip: Path | None = None,
        status_file: Path | None = None,
        decode: Callable[[Path], list] | None = None,
        start_thread=start_daemon_thread,
        clock: Callable[[], float] = time.monotonic,
        log: logging.Logger = logger,
    ) -> None:
        self._clips_dirs = tuple(Path(folder) for folder in clips_dirs)
        self._vr_dirs = tuple(Path(folder) for folder in vr_dirs)
        self._settings = settings
        self._log = log
        self._lock = threading.Lock()
        self._frame = None
        self._frame_taken = True
        self._loading: str | None = None
        self._console_hud = None
        self._volume = 100
        self._muted = False
        self._projection_of: tuple[Path | None, str] = (None, "")

        clips = scan_clips(self._clips_dirs, shuffle_on_load=settings.shuffle_on_load)
        self._sequence = ClipSequenceController(clips, start_at=start_clip)
        decode = decode or (
            lambda path: load_clip_frames(path, cache_dir_for_clips_folder(path.parent)))

        clip_store = ClipCacheStore(limit=settings.clip_cache_size)
        self._renderer = ClipRenderController(clip_store=clip_store, blit_frame=self._take_from_engine)
        loader = ClipLoadController(
            clip_store=clip_store,
            load_state=DecodeRequestState(),
            prefetch_state=DecodeRequestState(),
            current_clip_path_getter=lambda: self._renderer.current_clip_path,
            decode_clip=decode,
            start_thread=start_thread,
            logger=log,
            on_active_clip_loaded=self._renderer.prepare_active_clip_for_current_size,
        )
        self._selection = ClipSelectionController(
            sequence=self._sequence,
            clip_store=clip_store,
            loader=loader,
            renderer=self._renderer,
            notifier=notifier,
            condemn_clip=lambda path: self._condemn(path, weird_dir_for_clips_folder(path.parent)),
        )

        # The same hand, cruise stack, clip advance and driver the desktop builds.
        self.robot_hand = RobotHandState(playing=False, speed=50, bpm=bpm_for_speed(50))
        cruise = CruiseControlState()
        self._driver = RobotHandTCodeDriver(tcode_sink, robot_hand=self.robot_hand, cruise=cruise)
        self._hud = Flag()
        self._broker = BrokerFeed()
        start_thread(
            target=udp_reader,
            args=(settings.udp_host, settings.udp_port, self._broker, stop_event, log),
            name="genau-udp",
        )
        self._controls = GenauControls(
            engine=BeatEngine(last_tick=clock()),
            paused=Flag(),
            step_clip=self._selection.step,
            condemn_clip=self._selection.condemn_current,
            robot_hand=self.robot_hand,
            cruise_control_state=cruise,
            set_motion_phase=self._driver.set_motion_phase,
            clip_advance_state=ClipAdvanceState(),
            stop_event=stop_event,
            hud=self._hud,
            set_volume=self._set_volume,
            reorder_clips=self._reorder,
        )
        self._controller = GenauRefreshController(
            controls=self._controls,
            broker=self._broker,
            loader=loader,
            notifier=notifier,
            renderer=self._renderer,
            selection=self._selection,
            command_file=Path(command_file),
            paused_file=Path(paused_file),
            beats_per_loop=settings.beats_per_loop,
            bpm_smoothing=settings.bpm_smoothing,
            sync_strength=settings.sync_strength,
            set_loading_text=self._set_loading,
            logger=log,
            now_source=clock,
            read_paused_state=read_paused_state,
            tcode_sender=self._driver,
            status_file=status_file,
            drive_file=Path(drive_file),
            console_file=Path(console_file) if console_file else None,
            set_console=self._set_console,
        )
        self._selection.set_current_clip(self._selection.current_path)

    # ------------------------------------------------------------------ state

    @property
    def showing(self) -> bool:
        """Whether the clip has the scene: HUD_ON is video mode, where on the
        desktop Genau is the see-through layer over the video."""
        return not self._hud.on

    @property
    def current_clip(self) -> Path | None:
        return self._renderer.current_clip_path

    @property
    def projection(self) -> str:
        """How the clip on screen is watched: by its name, else by whether it lives in a VR folder."""
        clip = self.current_clip
        cached_for, projection = self._projection_of
        if clip is None:
            return ""
        if cached_for != clip:
            projection = default_projection(str(clip), self._vr_dirs)
            self._projection_of = (clip, projection)
        return projection

    @property
    def loading(self) -> str | None:
        return self._loading

    @property
    def console_hud(self):
        """The console the engine last composed, or None while the broker has the room."""
        return self._console_hud

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    # ------------------------------------------------------------------ turns

    def refresh(self) -> None:
        """One turn of the engine: file I/O every time, so never the render thread's."""
        self._controller.refresh()

    def take_frame(self):
        """The frame the engine chose since last asked, or None; the render thread's one read."""
        with self._lock:
            if self._frame_taken:
                return None
            self._frame_taken = True
            return self._frame

    def close(self) -> None:
        self._driver.close()

    # ---------------------------------------------------------------- engine's

    def _take_from_engine(self, frame) -> None:
        with self._lock:
            self._frame = frame
            self._frame_taken = False

    def _set_loading(self, text: str | None) -> None:
        self._loading = text

    def _set_console(self, hud) -> None:
        self._console_hud = hud

    def _set_volume(self, level: int, muted: bool) -> None:
        self._volume, self._muted = level, muted

    def _reorder(self, recent: bool) -> None:
        """LATEST and SHUFFLE: rescan the folder in that order and browse it from the top."""
        try:
            clips = scan_clips(
                self._clips_dirs, shuffle_on_load=self._settings.shuffle_on_load, recent=recent,
            )
        except (OSError, RuntimeError):
            self._log.warning("Could not rescan %s; keeping the sequence", self._clips_dirs,
                              exc_info=True)
            return
        self._selection.reorder(clips)

    def _condemn(self, path: Path, weird_dir: Path) -> None:
        try:
            landed = move_clip_to_weird(path, weird_dir)
        except OSError:
            self._log.warning("Could not move %s to %s", path.name, weird_dir, exc_info=True)
            return
        if landed is None:
            self._log.info("Clip %s was already gone; nothing to condemn", path.name)
        else:
            self._log.info("Condemned %s to %s", path.name, weird_dir)


def run_ticks(role: GenauRole, stop: threading.Event, *, hz: float = TICK_HZ) -> None:
    """The engine's own thread: the desktop window's loop without the window."""
    period = 1.0 / hz
    while not stop.is_set():
        started = time.monotonic()
        role.refresh()
        stop.wait(max(0.0, period - (time.monotonic() - started)))
