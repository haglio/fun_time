"""Genau, as it runs on the Main Funestra: the room drives it through files of its
own, and with the window it brings its own picture, a clip scrubbed to the OSR2."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from player_core.clip_decode import load_clip_frames
from player_core.clip_folder import flat_clips_in, scan_clips, weird_folder_for
from player_core.clip_picture import Picture
from player_core.console_hud import ModeHud

from fun_time.genau_config import GenauSettings
from fun_time.genau_engine import Narrow, build_genau_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenauChannels:
    """The files Genau is driven through, beside the Funestra's own."""

    command: Path
    paused: Path
    status: Path
    drive: Path


class Genau:
    def __init__(
        self,
        *,
        clips_folder: Path,
        settings: GenauSettings,
        channels: GenauChannels,
        notifier,
        tcode_sink,
        start_clip: Path | None = None,
        latest: bool = False,
        metadata_root: Path | None = None,
        narrow: Narrow | None = None,
        stop_event: threading.Event | None = None,
        decode: Callable[[Path], list] = load_clip_frames,
        start_thread=start_daemon_thread,
        clock: Callable[[], float] = time.monotonic,
        log: logging.Logger = logger,
    ) -> None:
        self._clips_folder = Path(clips_folder)
        self._settings = settings
        self._recent = latest
        self._notifier = notifier
        self._frame = None
        self._clip: Path | None = None
        self._loading: str | None = None
        self._engine = build_genau_engine(
            clips=self._scan(),
            settings=settings,
            command_file=channels.command,
            paused_file=channels.paused,
            status_file=channels.status,
            drive_file=channels.drive,
            notifier=notifier,
            tcode_sink=tcode_sink,
            blit_frame=self._take_frame,
            set_loading_text=self._set_loading,
            rescan=self._rescan,
            narrow=narrow,
            condemned_to=lambda clip: weird_folder_for(clip, self._clips_folder),
            set_volume=self._the_rooms_level_is_the_funestras,
            stop_event=stop_event,
            start_clip=start_clip,
            metadata_root=metadata_root,
            decode=decode,
            start_thread=start_thread,
            clock=clock,
            log=log,
        )

    def apply_command(self, command: str) -> bool:
        return False

    def tick(self) -> None:
        self._engine.refresh()

    def status_fields(self) -> dict[str, str]:
        return {}

    def top_block(self) -> ModeHud:
        clip = self._engine.current_clip
        return ModeHud(video="" if clip is None else clip.stem)

    def set_showing(self, showing: bool) -> None:
        self._engine.controls.hud.on = not showing

    def picture(self) -> Picture:
        played, count = self._engine.playhead()
        elapsed_s, interval_s = self._engine.time_on_screen
        return Picture(frame=self._frame, played=played, count=count,
                       elapsed_ms=elapsed_s * 1000.0, interval_ms=interval_s * 1000.0,
                       loading=self._loading,
                       seek_time=lambda ms: self._engine.seek_the_time_on_screen(ms / 1000.0),
                       seek_loop=self._engine.seek, clip=self._clip)

    def close(self) -> None:
        self._engine.close()
        self._notifier.close()

    def _take_frame(self, frame, clip: Path | None) -> None:
        self._frame, self._clip = frame, clip

    def _set_loading(self, text: str | None) -> None:
        self._loading = text

    def _scan(self) -> list[Path]:
        return scan_clips(flat_clips_in(self._clips_folder),
                          shuffle_on_load=self._settings.shuffle_on_load, recent=self._recent)

    def _rescan(self, recent: bool) -> list[Path]:
        self._recent = recent
        return self._scan()

    @staticmethod
    def _the_rooms_level_is_the_funestras(_level: int, _muted: bool) -> None:
        pass
