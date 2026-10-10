"""The engine a headset's Funestra plays through: libmpv rendered into a
texture, and silent until the headset is worn.

A sink parked on the stand takes the stream without draining it, and mpv's
clock follows audio, so a player heard before the compositor is presenting
freezes its picture on frame 1.  What the room or a chip asks before then is
remembered and comes on with the sound.
"""
from __future__ import annotations

from collections.abc import Callable

from player_core.render_player import MpvRenderPlayer


class HeadsetPlayer(MpvRenderPlayer):
    def __init__(self, get_proc_address: Callable[[str], int | None], *,
                 loop_file: bool, prefetch: bool) -> None:
        super().__init__(get_proc_address, muted=True, loop_file=loop_file, prefetch=prefetch)
        self._sound_live = False
        self._muted_asked = True

    def set_muted(self, muted: bool) -> None:
        self._muted_asked = muted
        if self._sound_live:
            super().set_muted(muted)

    def sound_goes_live(self, device: str) -> str | None:
        if self._sound_live:
            return None
        self._sound_live = True
        picked = self.set_audio_device_matching(device) if device else None
        super().set_muted(self._muted_asked)
        return picked
