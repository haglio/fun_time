from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from funestra.main_hud import ModeHud

from .clip_jumps import ClipJumps
from .controls import KinoControls, apply_command
from .gallery_names import NO_GALLERY_NAMES, GalleryNames
from .modes import Modes, reload_playlist
from .snippet_machine import SnippetMachine
from .status import status_fields
from .versions import Versions


@dataclass(frozen=True)
class KinoLibrary:
    source: Any
    clip_nav: Any
    resolve_playlist: Callable[[], list]
    gallery_names: GalleryNames = NO_GALLERY_NAMES


class Kino:
    def __init__(self, playback, *, library: KinoLibrary, messages, memory, remembered) -> None:
        source, resolve_playlist = library.source, library.resolve_playlist
        self._playback = playback
        self._memory = memory
        self._snippet = SnippetMachine(
            playback, seek_to=playback.seek_to,
            take_the_device_over=playback.take_the_device_over, floor_seeks_at=playback.set_mark)
        entries = source.entries if source is not None else []
        self._jumps = ClipJumps(library.clip_nav, playback, {e.video: e.funscript for e in entries}, messages)
        self._jumps.resume(remembered.compilation,
                           Path(remembered.video) if remembered.video else None)
        self._modes = Modes(
            playback, self._jumps,
            Versions(playback, source.version_index if source is not None else None),
            the_main_list=resolve_playlist, gallery_name_of=library.gallery_names.of)
        self._controls = KinoControls(
            playback=playback, snippet=self._snippet, versions=self._modes.versions,
            modes=self._modes, jumps=self._jumps,
            reload_playlist=partial(reload_playlist, playback, self._jumps, resolve_playlist),
        )
        self._loads_seen: int | None = None
        self._follow_the_item()

    def apply_command(self, command: str) -> bool:
        self._follow_the_item()
        return apply_command(command, self._controls)

    def tick(self) -> None:
        self._follow_the_item()
        if not self._playback.is_paused:
            self._snippet.observe(self._playback.position_ms)
        self._memory.sync(self._modes.remembered)

    def status_fields(self) -> dict[str, str]:
        self._follow_the_item()
        return status_fields(self._snippet, self._modes.library_status)

    def top_block(self) -> ModeHud:
        self._follow_the_item()
        return self._modes.hud

    def set_showing(self, _showing: bool) -> None:
        pass

    def own_frame(self) -> None:
        pass

    def close(self) -> None:
        self._memory.sync(self._modes.remembered)

    def _follow_the_item(self) -> None:
        if self._playback.loads != self._loads_seen:
            self._loads_seen = self._playback.loads
            self._snippet.open(self._playback.current_funscript)
