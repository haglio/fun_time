"""Genau's role in the VR process: the engine the Main Funestra runs, on the same
file channel, with what a headset needs kept here -- the frame the engine chose,
the clip's projection, whether the clip has the scene, the console it composed."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from player_core.clip_decode import load_clip_frames
from player_core.clip_folder import flat_clips_in, scan_clips, vr_clips_in, weird_folder_for

from fun_time.genau_config import GenauSettings
from fun_time.genau_engine import build_genau_engine
from fun_time.vr_videos import keep_shapes

from .projection import default_projection

logger = logging.getLogger(__name__)

# The desktop window's rate: a slower tick shows fewer of the clip's frames per cycle.
TICK_HZ = 120.0


class GenauRole:
    def __init__(
        self,
        *,
        clips_folder: Path,
        settings: GenauSettings,
        command_file: Path,
        paused_file: Path,
        drive_file: Path,
        console_file: Path | None,
        notifier,
        tcode_sink,
        stop_event: threading.Event,
        start_clip: Path | None = None,
        latest: bool = False,
        status_file: Path | None = None,
        metadata_root: Path | None = None,
        decode: Callable[[Path], list] = load_clip_frames,
        start_thread=start_daemon_thread,
        clock: Callable[[], float] = time.monotonic,
        log: logging.Logger = logger,
    ) -> None:
        self._clips_folder = Path(clips_folder)
        self._vr_dirs = (vr_clips_in(self._clips_folder),)
        self._clips_dirs = (*self._vr_dirs, flat_clips_in(self._clips_folder))
        self._settings = settings
        self._lock = threading.Lock()
        self._frame_waiting: tuple[object, Path | None] | None = None
        self._clip_on_screen: Path | None = None
        self._loading: str | None = None
        self._console_hud = None
        self._volume = 100
        self._muted = False
        self._projection_of: tuple[Path | None, str] = (None, "")
        self._recent = latest
        self._shapes = (True, True)
        self._engine = build_genau_engine(
            clips=self._scan(),
            settings=settings,
            command_file=command_file,
            paused_file=paused_file,
            status_file=status_file,
            drive_file=drive_file,
            notifier=notifier,
            tcode_sink=tcode_sink,
            blit_frame=self._take_from_engine,
            set_loading_text=self._set_loading,
            rescan=self._rescan,
            narrow=self._narrow,
            condemned_to=lambda clip: weird_folder_for(clip, self._clips_folder),
            set_volume=self._set_volume,
            stop_event=stop_event,
            console_file=console_file,
            set_console=self._set_console,
            start_clip=start_clip,
            metadata_root=metadata_root,
            decode=decode,
            start_thread=start_thread,
            clock=clock,
            log=log,
        )
        self.robot_hand = self._engine.robot_hand

    # ------------------------------------------------------------------ state

    @property
    def showing(self) -> bool:
        """Whether the clip has the scene; HUD_ON is kino mode."""
        return self._engine.showing

    @property
    def current_clip(self) -> Path | None:
        return self._engine.current_clip

    @property
    def projection(self) -> str:
        """How the clip on screen is watched: by its name, else by whether it lives in a VR folder."""
        clip = self._clip_on_screen
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
        """The console the engine last composed, or None before its first tick."""
        return self._console_hud

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def seek(self, fraction: float) -> None:
        """Put the clip *fraction* along its bar, and the device where that is."""
        self._engine.seek(fraction)

    @property
    def playhead(self) -> tuple[int, int]:
        return self._engine.playhead()

    # ------------------------------------------------------------------ turns

    def refresh(self) -> None:
        """One turn of the engine: file I/O every time, so never the render thread's."""
        self._engine.refresh()

    def take_frame(self):
        """The frame the engine chose since last asked, or None; the render thread's one read."""
        with self._lock:
            if self._frame_waiting is None:
                return None
            frame, self._clip_on_screen = self._frame_waiting
            self._frame_waiting = None
            return frame

    def close(self) -> None:
        self._engine.close()

    # ---------------------------------------------------------------- engine's

    def _take_from_engine(self, frame, clip: Path | None) -> None:
        with self._lock:
            self._frame_waiting = (frame, clip)

    def _set_loading(self, text: str | None) -> None:
        self._loading = text

    def _set_console(self, hud) -> None:
        self._console_hud = hud

    def _set_volume(self, level: int, muted: bool) -> None:
        self._volume, self._muted = level, muted

    def _scan(self) -> list[Path]:
        plays_vr, plays_flat = self._shapes
        return keep_shapes(
            scan_clips(self._clips_dirs, shuffle_on_load=self._settings.shuffle_on_load,
                       recent=self._recent),
            vr_dirs=self._vr_dirs, plays_vr=plays_vr, plays_flat=plays_flat)

    def _rescan(self, recent: bool) -> list[Path]:
        """LATEST and SHUFFLE: the folder in that order, browsed from the top."""
        self._recent = recent
        return self._scan()

    def _narrow(self, plays_vr: bool, plays_flat: bool) -> list[Path]:
        self._shapes = (plays_vr, plays_flat)
        return self._scan()


def run_ticks(role: GenauRole, stop: threading.Event, *, hz: float = TICK_HZ) -> None:
    """The engine's own thread: the desktop window's loop without the window."""
    period = 1.0 / hz
    while not stop.is_set():
        started = time.monotonic()
        role.refresh()
        stop.wait(max(0.0, period - (time.monotonic() - started)))
