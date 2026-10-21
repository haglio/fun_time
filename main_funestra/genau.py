"""Genau, as it runs on the Main Funestra: the room drives it through files of its
own, and with the window it brings its own picture, a flick scrubbed to the OSR2."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from funestra.console_hud import ModeHud
from funestra.flick_decode import load_flick_frames
from funestra.flick_folder import flat_flicks_in, scan_flicks, weird_folder_for
from funestra.flick_picture import Picture
from funestra.modes import NoticeLevel

from fun_time.arrivals import LOOK_EVERY_S, Arrivals
from fun_time.content import no_other_flicks
from fun_time.genau_config import GenauSettings
from fun_time.genau_engine import Narrow, build_genau_engine
from fun_time.modes import keep_favorites, videos_in

from .genau_verbs import apply_command as answer_genaus_verb
from .notice import NoticeWriter

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
        flicks_folder: Path,
        settings: GenauSettings,
        channels: GenauChannels,
        notifier,
        tcode_sink,
        notices: NoticeWriter | None = None,
        start_flick: Path | None = None,
        latest: bool = False,
        favs_file: Path | None = None,
        f_mode: bool = False,
        metadata_root: Path | None = None,
        narrow: Narrow | None = None,
        stop_event: threading.Event | None = None,
        decode: Callable[[Path], list] = load_flick_frames,
        start_thread=start_daemon_thread,
        clock: Callable[[], float] = time.monotonic,
        log: logging.Logger = logger,
    ) -> None:
        self._flicks_folder = Path(flicks_folder)
        self._settings = settings
        self._recent = latest
        self._favs_file = favs_file
        self._f_mode = f_mode
        self._notifier = notifier
        self._notices = notices
        self._frame = None
        self._flick: Path | None = None
        self._loading: str | None = None
        self._showing = False
        self._clock = clock
        browsed = self._browsed()
        kept = self._favorites_of(browsed)
        self._engine = build_genau_engine(
            flicks=kept or browsed,
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
            say_no_other_flicks=self._say_no_other_flicks,
            condemned_to=lambda flick: weird_folder_for(flick, self._flicks_folder),
            set_volume=self._the_rooms_level_is_the_funestras,
            stop_event=stop_event,
            start_flick=start_flick,
            metadata_root=metadata_root,
            decode=decode,
            start_thread=start_thread,
            clock=clock,
            log=log,
        )
        if not kept:
            self._engine.narrow(self._scan)
        self._arrivals = Arrivals(lambda: videos_in(self._folders()))
        self._arrivals.look()
        self._next_look = clock() + LOOK_EVERY_S

    def apply_command(self, command: str) -> bool:
        return answer_genaus_verb(command, self)

    def keep_favorites(self, on: bool) -> None:
        self._f_mode = on
        self._engine.narrow(self._scan)

    def tick(self) -> None:
        self._put_up_what_arrived()
        self._engine.refresh()

    def _put_up_what_arrived(self) -> None:
        if self._clock() < self._next_look:
            return
        self._next_look = self._clock() + LOOK_EVERY_S
        self._arrivals.look()
        self._arrivals.hand_to(_GenausFlicks(self))

    def status_fields(self) -> dict[str, str]:
        return {}

    def top_block(self) -> ModeHud:
        flick = self._engine.current_flick
        return ModeHud(video="" if flick is None else flick.stem, scripted_filter=self._f_mode)

    def set_showing(self, showing: bool) -> None:
        self._showing = showing
        self._engine.controls.hud.on = not showing

    def picture(self) -> Picture:
        played, count = self._engine.playhead()
        elapsed_s, interval_s = self._engine.time_on_screen
        return Picture(frame=self._frame, played=played, count=count,
                       elapsed_ms=elapsed_s * 1000.0, interval_ms=interval_s * 1000.0,
                       loading=self._loading,
                       seek_time=lambda ms: self._engine.seek_the_time_on_screen(ms / 1000.0),
                       seek_loop=self._engine.seek, flick=self._flick)

    def close(self) -> None:
        self._engine.close()
        self._notifier.close()

    def _take_frame(self, frame, flick: Path | None) -> None:
        self._frame, self._flick = frame, flick

    def _set_loading(self, text: str | None) -> None:
        self._loading = text

    def _say_no_other_flicks(self) -> None:
        if self._notices is not None:
            self._notices.say(no_other_flicks(), level=NoticeLevel.WARNING)

    def _scan(self) -> list[Path]:
        return self._favorites_of(self._browsed())

    def _browsed(self) -> list[Path]:
        return self._shaped(scan_flicks(self._folders(),
                                        shuffle_on_load=self._settings.shuffle_on_load,
                                        recent=self._recent))

    def _folders(self) -> tuple[Path, ...]:
        return (flat_flicks_in(self._flicks_folder),)

    def _shaped(self, flicks: list[Path]) -> list[Path]:
        return flicks

    def _favorites_of(self, flicks: list[Path]) -> list[Path]:
        if not self._f_mode or self._favs_file is None:
            return flicks
        return keep_favorites(flicks, self._favs_file)

    def _rescan(self, recent: bool) -> list[Path]:
        self._recent = recent
        return self._scan()

    @staticmethod
    def _the_rooms_level_is_the_funestras(_level: int, _muted: bool) -> None:
        pass


@dataclass(frozen=True)
class _GenausFlicks:
    genau: Genau
    name: str = "Genau"

    def in_latest(self) -> bool:
        return self.genau._recent

    def plays_its_own_list(self) -> bool:
        return self.genau._showing

    def lets_through(self, paths: list[str]) -> list[str]:
        flicks = [Path(path) for path in paths]
        return [str(flick) for flick in self.genau._favorites_of(self.genau._shaped(flicks))]

    def put_up_next(self, path: str) -> None:
        self.genau._engine.play_next(Path(path))
