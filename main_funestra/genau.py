from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from funestra.flick_folder import flat_flicks_in, scan_flicks, trash_folder_for
from funestra.flick_overlay import FlickFrame
from funestra.funestra_verbs import NEXT, PREV
from funestra.genau_controls import apply_runtime_command
from funestra.inclusion_pair import BOTH, ORIENTATION, Included
from funestra.main_hud import ModeHud
from funestra.messages import MessageWriter
from funestra.modes import MessageLevel
from shared_ui.orientation import PORTRAIT

from fun_time.arrivals import LOOK_EVERY_S, Arrivals
from fun_time.content import no_other_flicks
from fun_time.gallery import Gallery, GalleryFlips
from fun_time.genau_config import GenauSettings
from fun_time.genau_engine import (
    EngineSeams,
    FlickLibrary,
    GenauChannels,
    GenauHost,
    Narrow,
    build_genau_engine,
)
from fun_time.main_list import has_an_enhanced_version
from fun_time.media_metadata import RecordsRead, recorded_orientation
from fun_time.modes import videos_in
from fun_time.origenerator_trash import tell_origenerator_it_went

from .gallery_names import NO_GALLERY_NAMES, GalleryNames
from .genau_verbs import apply_command as answer_genaus_verb

logger = logging.getLogger(__name__)

ROBOT_HAND = "robot hand"


@dataclass(frozen=True)
class GenauFlicks:
    folder: Path
    start_flick: Path | None = None
    latest: bool = False
    favorites: bool = False
    enhanced: bool = False
    orientations: Included = BOTH
    metadata_root: Path | None = None
    narrow: Narrow | None = None
    gallery: Gallery = field(default_factory=lambda: Gallery([]))
    gallery_names: GalleryNames = NO_GALLERY_NAMES


@dataclass(frozen=True)
class GenauOutputs:
    notifier: Any
    tcode_sink: Any
    messages: MessageWriter | None = None
    origenerator_commands: Path | None = None


STANDARD_SEAMS = EngineSeams(log=logger)


def _a_step(command: str) -> bool:
    return command.split(None, 1)[0].upper() in (NEXT, PREV) if command.strip() else False


class Genau:
    def __init__(
        self,
        flicks: GenauFlicks,
        *,
        settings: GenauSettings,
        channels: GenauChannels,
        outputs: GenauOutputs,
        seams: EngineSeams = STANDARD_SEAMS,
    ) -> None:
        clock = seams.clock
        notifier = outputs.notifier
        self._flicks_folder = Path(flicks.folder)
        self._settings = settings
        self._recent = flicks.latest
        self._favorites = flicks.favorites
        self._enhanced = flicks.enhanced
        self._orientations = flicks.orientations
        self._metadata_root = flicks.metadata_root
        self._gallery = flicks.gallery
        self._gallery_names = flicks.gallery_names
        self._notifier = notifier
        self._messages = outputs.messages
        self._frame = None
        self._flick: Path | None = None
        self._loading: str | None = None
        self._showing = False
        self._clock = clock
        browsed = self._browsed()
        records = self._records()
        turned = self._turned_as_asked(browsed, records)
        kept = self._collections_of(turned, records)
        self._engine = build_genau_engine(
            settings,
            channels,
            FlickLibrary(kept or turned or browsed, flicks.start_flick, flicks.metadata_root,
                         flips=GalleryFlips(flicks.gallery)),
            GenauHost(
                notifier=notifier,
                tcode_sink=outputs.tcode_sink,
                blit_frame=self._take_frame,
                set_loading_text=self._set_loading,
                rescan=self._rescan,
                narrow=flicks.narrow,
                say_no_other_flicks=self._say_no_other_flicks,
                say=self._say,
                condemned_to=lambda flick: trash_folder_for(flick, self._flicks_folder),
                set_volume=self._the_apps_level_is_the_funestras,
                tell_where_it_went=tell_origenerator_it_went(outputs.origenerator_commands),
            ),
            seams,
        )
        if not kept:
            self._engine.narrow(lambda: kept)
        self._arrivals = Arrivals(lambda: videos_in(self._folders()))
        self._look_for_arrivals()
        self._next_look = clock() + LOOK_EVERY_S

    def apply_command(self, command: str) -> bool:
        if self._showing and _a_step(command):
            apply_runtime_command(command, self._engine.controls)
            return True
        return answer_genaus_verb(command, self)

    def keep_favorites(self, on: bool) -> None:
        self._favorites = on
        self._engine.narrow(self._scan)

    def keep_enhanced(self, on: bool) -> None:
        self._enhanced = on
        self._engine.narrow(self._scan)

    def keep_orientations(self, orientations: Included) -> None:
        self._orientations = orientations
        self._engine.narrow(self._scan, says=ORIENTATION.flash(orientations))

    def tick(self) -> None:
        self._put_up_what_arrived()
        self._engine.refresh()

    def _put_up_what_arrived(self) -> None:
        if self._clock() < self._next_look:
            return
        self._next_look = self._clock() + LOOK_EVERY_S
        self._look_for_arrivals()
        self._arrivals.hand_to(_GenausFlicks(self))

    def _look_for_arrivals(self) -> None:
        if self._recent:
            self._arrivals.look()
        else:
            self._arrivals.forget()

    def status_fields(self) -> dict[str, str]:
        return {}

    def top_block(self) -> ModeHud:
        flick = self._engine.current_flick
        name = "" if flick is None else self._gallery_names.of(flick) or flick.stem
        return ModeHud(video=name)

    def set_showing(self, showing: bool) -> None:
        self._showing = showing
        self._engine.controls.hud.on = not showing

    def own_frame(self) -> FlickFrame:
        played, count = self._engine.playhead()
        elapsed_s, interval_s = self._engine.time_on_screen
        return FlickFrame(frame=self._frame, played=played, count=count,
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
        if self._messages is not None:
            self._messages.say(no_other_flicks(), level=MessageLevel.WARNING)

    def _say(self, words: str) -> None:
        if self._messages is not None:
            self._messages.say(words, level=MessageLevel.NORMAL)

    def _scan(self) -> list[Path]:
        return self._kept_of(self._browsed())

    def _kept_of(self, flicks: list[Path]) -> list[Path]:
        records = self._records()
        return self._collections_of(self._turned_as_asked(flicks, records), records)

    def _records(self) -> RecordsRead:
        return RecordsRead(self._metadata_root, lambda: self._gallery)

    def _turned_as_asked(self, flicks: list[Path], records: RecordsRead) -> list[Path]:
        if self._orientations == BOTH:
            return flicks
        portrait, landscape = self._orientations
        return [flick for flick in flicks
                if (portrait if recorded_orientation(records.of(flick)) == PORTRAIT
                    else landscape)]

    def _browsed(self) -> list[Path]:
        return self._shaped(scan_flicks(self._folders(),
                                        shuffle_on_load=self._settings.shuffle_on_load,
                                        recent=self._recent))

    def _folders(self) -> tuple[Path, ...]:
        return (flat_flicks_in(self._flicks_folder),)

    def _shaped(self, flicks: list[Path]) -> list[Path]:
        return flicks

    def _collections_of(self, flicks: list[Path], records: RecordsRead) -> list[Path]:
        return self._enhanced_of(self._favorites_of(flicks, records))

    def _favorites_of(self, flicks: list[Path], records: RecordsRead) -> list[Path]:
        if not self._favorites or self._metadata_root is None:
            return flicks
        return [flick for flick in flicks if records.favorite(flick)]

    def _enhanced_of(self, flicks: list[Path]) -> list[Path]:
        if not self._enhanced:
            return flicks
        enhanced = has_an_enhanced_version([str(flick) for flick in flicks])
        return [flick for flick in flicks if enhanced(str(flick))]

    def _rescan(self, recent: bool) -> list[Path]:
        self._recent = recent
        return self._scan()

    @staticmethod
    def _the_apps_level_is_the_funestras(_level: int, _muted: bool) -> None:
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
        return [str(flick) for flick in self.genau._kept_of(self.genau._shaped(flicks))]

    def put_up_next(self, path: str) -> None:
        self.genau._engine.play_next(Path(path))
