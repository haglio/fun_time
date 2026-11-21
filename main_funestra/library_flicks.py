from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from funestra.flick_folder import flat_flicks_in, scan_flicks, trash_folder_for
from funestra.genau import Narrow, Opening
from funestra.inclusion_pair import BOTH, Included
from shared_ui.orientation import PORTRAIT

from fun_time.arrivals import LOOK_EVERY_S, Arrivals
from fun_time.gallery import Gallery, GalleryFlips
from fun_time.main_list import has_an_enhanced_version
from fun_time.media_metadata import RecordsRead, recorded_orientation
from fun_time.modes import videos_in
from fun_time.origenerator_trash import tell_origenerator_it_went

from .gallery_names import NO_GALLERY_NAMES, GalleryNames
from .genau_verbs import apply_command as answer_genaus_verb


@dataclass(frozen=True)
class _TrashBesideTheFolder:
    folder: Path
    went: Callable[[Path, Path], None]

    def folder_for(self, flick: Path) -> Path:
        return trash_folder_for(flick, self.folder)


class LibraryFlicks:
    keep_shapes = None

    def __init__(
        self,
        folder: Path,
        *,
        favorites: bool = False,
        enhanced: bool = False,
        orientations: Included = BOTH,
        metadata_root: Path | None = None,
        gallery: Gallery | None = None,
        gallery_names: GalleryNames = NO_GALLERY_NAMES,
        origenerator_commands: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._folder = Path(folder)
        self._favorites = favorites
        self._enhanced = enhanced
        self._orientations = orientations
        self._gallery = Gallery([]) if gallery is None else gallery
        self._gallery_names = gallery_names
        self._clock = clock
        self._next_look: float | None = None
        self._arrivals = Arrivals(lambda: videos_in(self._folders()))
        self.metadata_root = metadata_root
        self.flips = GalleryFlips(self._gallery)
        self.trash = _TrashBesideTheFolder(self._folder,
                                           tell_origenerator_it_went(origenerator_commands))

    def opening(self, *, recent: bool, shuffled: bool) -> Opening:
        browsed = self._browsed(recent, shuffled)
        records = self._records()
        turned = self._turned_as_asked(browsed, records)
        kept = self._collections_of(turned, records)
        return Opening(kept or turned or browsed, none_kept=not kept)

    def listed(self, *, recent: bool, shuffled: bool) -> list[Path]:
        return self._kept_of(self._browsed(recent, shuffled))

    def name_of(self, flick: Path) -> str:
        return self._gallery_names.of(flick) or flick.stem

    def up_next(self, *, recent: bool, showing: bool) -> list[Path]:
        now = self._clock()
        if self._next_look is not None and now < self._next_look:
            return []
        self._next_look = now + LOOK_EVERY_S
        if recent:
            self._arrivals.look()
        else:
            self._arrivals.forget()
        lineup = _UpNext(recent, showing, self._arriving_kept)
        self._arrivals.hand_to(lineup)
        return lineup.picked

    def apply_command(self, command: str, narrow: Narrow) -> bool:
        return answer_genaus_verb(command, self, narrow)

    def keep_favorites(self, on: bool) -> None:
        self._favorites = on

    def keep_enhanced(self, on: bool) -> None:
        self._enhanced = on

    def keep_orientations(self, orientations: Included) -> None:
        self._orientations = orientations

    def _arriving_kept(self, flicks: list[Path]) -> list[Path]:
        return self._kept_of(self._shaped(flicks))

    def _kept_of(self, flicks: list[Path]) -> list[Path]:
        records = self._records()
        return self._collections_of(self._turned_as_asked(flicks, records), records)

    def _records(self) -> RecordsRead:
        return RecordsRead(self.metadata_root, lambda: self._gallery)

    def _turned_as_asked(self, flicks: list[Path], records: RecordsRead) -> list[Path]:
        if self._orientations == BOTH:
            return flicks
        portrait, landscape = self._orientations
        return [flick for flick in flicks
                if (portrait if recorded_orientation(records.of(flick)) == PORTRAIT
                    else landscape)]

    def _browsed(self, recent: bool, shuffled: bool) -> list[Path]:
        return self._shaped(scan_flicks(self._folders(), shuffle_on_load=shuffled, recent=recent))

    def _folders(self) -> tuple[Path, ...]:
        return (flat_flicks_in(self._folder),)

    def _shaped(self, flicks: list[Path]) -> list[Path]:
        return flicks

    def _collections_of(self, flicks: list[Path], records: RecordsRead) -> list[Path]:
        return self._enhanced_of(self._favorites_of(flicks, records))

    def _favorites_of(self, flicks: list[Path], records: RecordsRead) -> list[Path]:
        if not self._favorites or self.metadata_root is None:
            return flicks
        return [flick for flick in flicks if records.favorite(flick)]

    def _enhanced_of(self, flicks: list[Path]) -> list[Path]:
        if not self._enhanced:
            return flicks
        enhanced = has_an_enhanced_version([str(flick) for flick in flicks])
        return [flick for flick in flicks if enhanced(str(flick))]


@dataclass
class _UpNext:
    recent: bool
    showing: bool
    kept_of: Callable[[list[Path]], list[Path]]
    name: str = "Genau"
    picked: list[Path] = field(default_factory=list)

    def in_latest(self) -> bool:
        return self.recent

    def plays_its_own_list(self) -> bool:
        return self.showing

    def lets_through(self, paths: list[str]) -> list[str]:
        return [str(flick) for flick in self.kept_of([Path(path) for path in paths])]

    def put_up_next(self, path: str) -> None:
        self.picked.append(Path(path))
