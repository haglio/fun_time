"""Kino, as it runs on the Main Funestra.

The Funestra plays the list and draws; Kino is what only the library side
knows: which files are the same video at different qualities, the length modes,
the compilation a clip belongs to and the scene it was cut from, the funscript
jumps, the mode written down for the next session, and the loop a video is
being marked into.  The Funestra asks Kino about every command first, gives it
a pass of its own each frame before the playback advances, publishes its lines
after its own, and heads the console with what Kino says it is playing.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path

from player_core.console_hud import ModeHud

from .clip_jumps import ClipJumps
from .controls import KinoControls, apply_command
from .funscript_jumps import FunscriptJumps
from .loop_machine import LoopMachine
from .modes import Modes, reload_playlist
from .status import status_fields
from .versions import Versions


class Kino:
    def __init__(self, playback, *, source, clip_nav, notices, memory, remembered,
                 resolve_playlist) -> None:
        self._playback = playback
        self._memory = memory
        self._loops = LoopMachine(
            playback, seek_to=playback.seek_to,
            take_the_device_over=playback.take_the_device_over, mark=playback.set_mark)
        entries = source.entries if source is not None else []
        self._jumps = ClipJumps(clip_nav, playback, {e.video: e.funscript for e in entries}, notices)
        self._jumps.resume(remembered.compilation,
                           Path(remembered.video) if remembered.video else None)
        self._modes = Modes(
            source, playback, self._jumps,
            Versions(playback, source.version_index if source is not None else None),
            remembered=remembered.length_mode)
        self._controls = KinoControls(
            playback=playback, loops=self._loops, versions=self._modes.versions,
            modes=self._modes, jumps=self._jumps,
            funscript_jumps=FunscriptJumps(playback, notices),
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
            self._loops.observe(self._playback.position_ms)
        self._memory.sync(self._modes.remembered)

    def status_fields(self) -> dict[str, str]:
        self._follow_the_item()
        return status_fields(self._loops, self._modes.library_status)

    def top_block(self) -> ModeHud:
        self._follow_the_item()
        return self._modes.hud

    def _follow_the_item(self) -> None:
        if self._playback.loads != self._loads_seen:
            self._loads_seen = self._playback.loads
            self._loops.open(self._playback.current_funscript)
